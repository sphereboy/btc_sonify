"""OHLCV fetching from Binance via ccxt, with on-disk parquet caching.

Why caching: the same date range will be sonified many times across runs
(experimenting with scales, octaves, etc.) and there is no reason to hit
the network repeatedly for data that is, by definition, historical and
immutable. Cache files live in ``~/.cache/btc-sonify/`` keyed by symbol,
timeframe, and the start/end timestamps so different ranges don't collide.

Why pagination: Binance's public OHLCV endpoint returns at most 1000
candles per call. A multi-year daily fetch is fine in one call (a year
is ~365 candles), but a multi-month hourly fetch can be tens of thousands
of candles. We page until we either reach the requested end or the
exchange returns nothing new.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import ccxt
import pandas as pd

DEFAULT_SYMBOL = "BTC/USDT"
# binance.com is geo-blocked from the US (HTTP 451), so we default to
# binance.us — same OHLCV API, no auth, works from US IPs. Other
# ccxt-supported exchanges can be passed via the CLI.
DEFAULT_EXCHANGE = "binanceus"
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "btc-sonify"

# OHLCV columns as ccxt returns them, in order.
OHLCV_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")

# Page size — ccxt default + Binance max for /klines.
BINANCE_PAGE_LIMIT = 1000

# Timeframe → milliseconds. Used to advance the `since` cursor between
# pages (we step by one full bar past the last candle we received so we
# never re-fetch the same bar).
TIMEFRAME_MS: dict[str, int] = {
    "1m":  60_000,
    "5m":  5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h":  60 * 60_000,
    "4h":  4 * 60 * 60_000,
    "1d":  24 * 60 * 60_000,
    "1w":  7 * 24 * 60 * 60_000,
}


def parse_iso_date(value: str) -> int:
    """Parse 'YYYY-MM-DD' (or any ISO 8601 date) into a UTC millisecond
    timestamp — the unit ccxt expects for `since`."""
    # fromisoformat accepts 'YYYY-MM-DD' as midnight UTC if we attach tz ourselves.
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def cache_path(
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    exchange_id: str = DEFAULT_EXCHANGE,
) -> Path:
    """Build the parquet cache path for a given query.

    Filename embeds the query parameters so distinct ranges never collide
    and so a human can inspect the cache by listing the directory. Exchange
    ID is included because the same symbol can have slightly different
    candle data on different exchanges.
    """
    safe_symbol = symbol.replace("/", "_")
    start = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).strftime("%Y%m%d")
    end = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc).strftime("%Y%m%d")
    return cache_dir / f"{exchange_id}_{safe_symbol}_{timeframe}_{start}_{end}.parquet"


def _ohlcv_to_dataframe(rows: Iterable[Iterable[Any]]) -> pd.DataFrame:
    """Convert ccxt's list-of-lists OHLCV format to a typed DataFrame."""
    df = pd.DataFrame(list(rows), columns=list(OHLCV_COLUMNS))
    if df.empty:
        return df
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    return df.reset_index(drop=True)


def _fetch_paginated(
    exchange: Any,
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    on_page: Any | None = None,
) -> list[list[Any]]:
    """Loop ccxt.fetch_ohlcv until we've covered [start_ms, end_ms].

    The cursor advances by one full timeframe past the last candle we
    received; this prevents an infinite loop if the exchange returns the
    same final candle and lets us stop cleanly when the page is empty
    or reaches `end_ms`.
    """
    if timeframe not in TIMEFRAME_MS:
        raise ValueError(
            f"Unsupported timeframe {timeframe!r}. "
            f"Expected one of {sorted(TIMEFRAME_MS)}."
        )
    step_ms = TIMEFRAME_MS[timeframe]

    rows: list[list[Any]] = []
    since = start_ms
    while since <= end_ms:
        page = exchange.fetch_ohlcv(
            symbol, timeframe=timeframe, since=since, limit=BINANCE_PAGE_LIMIT
        )
        if not page:
            break
        # Drop anything past end_ms (Binance honors `since` but ignores
        # `until`, so the last page can overshoot).
        page = [row for row in page if row[0] <= end_ms]
        if not page:
            break
        rows.extend(page)
        if on_page is not None:
            on_page(len(page), len(rows))
        last_ts = page[-1][0]
        next_since = last_ts + step_ms
        if next_since <= since:
            # Defensive: shouldn't happen, but guarantees forward progress.
            break
        since = next_since
        if len(page) < BINANCE_PAGE_LIMIT:
            # Short page = exchange has no more data for this range.
            break
    return rows


def fetch_ohlcv(
    start: str,
    end: str,
    timeframe: str = "1d",
    symbol: str = DEFAULT_SYMBOL,
    exchange_id: str = DEFAULT_EXCHANGE,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    use_cache: bool = True,
    exchange: Any | None = None,
    on_page: Any | None = None,
) -> pd.DataFrame:
    """Fetch historical OHLCV candles for `symbol` between `start` and `end`.

    Returns a DataFrame with columns timestamp (UTC datetime), open, high,
    low, close, volume — in chronological order, deduplicated, and trimmed
    to the requested range.

    `start` / `end` are ISO date strings (e.g. '2020-01-01'). The end bound
    is inclusive of any candle whose open-time falls on or before midnight
    UTC of the end date — i.e. `--end 2024-12-31` includes the 2024-12-31
    daily candle.

    Cache hits short-circuit the network call entirely. Pass
    ``use_cache=False`` to force a refetch (the previous file is overwritten
    on success).

    `exchange` is injectable for tests; in production we construct a fresh
    ccxt.binance() with rate limiting enabled. `on_page(page_size, total)`
    is an optional callback used by the CLI to render a progress bar.
    """
    start_ms = parse_iso_date(start)
    # Make end inclusive of the end-date itself (treat end as "end of day UTC").
    end_ms = parse_iso_date(end) + TIMEFRAME_MS["1d"] - 1
    if end_ms < start_ms:
        raise ValueError(f"end ({end}) is before start ({start}).")

    path = cache_path(symbol, timeframe, start_ms, end_ms, cache_dir, exchange_id)
    if use_cache and path.exists():
        return pd.read_parquet(path)

    if exchange is None:
        if not hasattr(ccxt, exchange_id):
            raise ValueError(
                f"ccxt has no exchange named {exchange_id!r}. "
                f"Try one of: binanceus, binance, kraken, coinbase, bitstamp."
            )
        exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})

    rows = _fetch_paginated(
        exchange, symbol, timeframe, start_ms, end_ms, on_page=on_page
    )
    df = _ohlcv_to_dataframe(rows)
    if not df.empty:
        df = df.drop_duplicates(subset="timestamp").reset_index(drop=True)

    cache_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return df
