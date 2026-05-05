"""Tests for data.py.

We never hit the live exchange in tests — a ``FakeExchange`` stub lets us
exercise pagination, range trimming, dedup, and cache behaviour deterministically.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from btc_sonify.data import (
    BINANCE_PAGE_LIMIT,
    TIMEFRAME_MS,
    cache_path,
    fetch_ohlcv,
    parse_iso_date,
)


class FakeExchange:
    """Mimics ccxt.binance().fetch_ohlcv: returns up to `limit` candles
    starting at `since`, drawn from a pre-populated tape."""

    def __init__(self, candles: list[list[float]]):
        # candles is the full tape, sorted by timestamp ascending
        self.candles = sorted(candles, key=lambda c: c[0])
        self.calls: list[dict] = []

    def fetch_ohlcv(self, symbol, timeframe, since, limit):
        self.calls.append(
            {"symbol": symbol, "timeframe": timeframe, "since": since, "limit": limit}
        )
        page = [c for c in self.candles if c[0] >= since][:limit]
        return [list(c) for c in page]  # caller-isolated copies


def make_candles(start_ms: int, n: int, step_ms: int) -> list[list[float]]:
    """Generate `n` synthetic OHLCV rows starting at `start_ms`."""
    out = []
    for i in range(n):
        ts = start_ms + i * step_ms
        # Just use the index for a recognisable sequence in tests.
        o = 100.0 + i
        h = o + 1.0
        l = o - 1.0
        c = o + 0.5
        v = 1000.0 + i
        out.append([ts, o, h, l, c, v])
    return out


# --- parse_iso_date -----------------------------------------------------

def test_parse_iso_date_returns_utc_midnight_ms():
    assert parse_iso_date("2024-01-01") == 1704067200000


def test_parse_iso_date_supports_full_datetime():
    # noon UTC on 2024-01-01
    assert parse_iso_date("2024-01-01T12:00:00+00:00") == 1704067200000 + 12 * 3600 * 1000


# --- cache_path ---------------------------------------------------------

def test_cache_path_includes_query_params(tmp_path):
    start_ms = parse_iso_date("2024-01-01")
    end_ms = parse_iso_date("2024-01-31")
    path = cache_path("BTC/USDT", "1d", start_ms, end_ms, cache_dir=tmp_path)
    assert path.parent == tmp_path
    assert path.name == "binanceus_BTC_USDT_1d_20240101_20240131.parquet"


def test_cache_path_distinguishes_timeframes(tmp_path):
    start_ms = parse_iso_date("2024-01-01")
    end_ms = parse_iso_date("2024-01-02")
    p1 = cache_path("BTC/USDT", "1d", start_ms, end_ms, cache_dir=tmp_path)
    p2 = cache_path("BTC/USDT", "1h", start_ms, end_ms, cache_dir=tmp_path)
    assert p1 != p2


# --- fetch_ohlcv: basic shape ------------------------------------------

def test_fetch_returns_typed_dataframe(tmp_path):
    start_ms = parse_iso_date("2024-01-01")
    candles = make_candles(start_ms, 10, TIMEFRAME_MS["1d"])
    fake = FakeExchange(candles)
    df = fetch_ohlcv(
        "2024-01-01", "2024-01-10", "1d",
        cache_dir=tmp_path, exchange=fake, use_cache=False,
    )
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert len(df) == 10
    assert pd.api.types.is_datetime64_any_dtype(df["timestamp"])
    assert df["timestamp"].dt.tz is not None  # tz-aware (UTC)
    for col in ("open", "high", "low", "close", "volume"):
        assert df[col].dtype == float


def test_fetch_first_row_matches_start(tmp_path):
    start_ms = parse_iso_date("2024-01-01")
    candles = make_candles(start_ms, 5, TIMEFRAME_MS["1d"])
    fake = FakeExchange(candles)
    df = fetch_ohlcv(
        "2024-01-01", "2024-01-05", "1d",
        cache_dir=tmp_path, exchange=fake, use_cache=False,
    )
    assert df.iloc[0]["timestamp"] == datetime(2024, 1, 1, tzinfo=timezone.utc)


# --- fetch_ohlcv: pagination -------------------------------------------

def test_fetch_paginates_when_range_exceeds_page_limit(tmp_path):
    """Range needs > 1000 candles → must call fetch_ohlcv multiple times."""
    start_ms = parse_iso_date("2020-01-01")
    n = 2500  # exceeds the 1000 page limit
    candles = make_candles(start_ms, n, TIMEFRAME_MS["1d"])
    fake = FakeExchange(candles)

    end_iso = datetime.fromtimestamp(
        (start_ms + (n - 1) * TIMEFRAME_MS["1d"]) / 1000, tz=timezone.utc
    ).strftime("%Y-%m-%d")

    df = fetch_ohlcv(
        "2020-01-01", end_iso, "1d",
        cache_dir=tmp_path, exchange=fake, use_cache=False,
    )
    assert len(df) == n
    assert len(fake.calls) >= 3  # 2500 / 1000 = at least 3 pages
    # Each `since` should advance — never re-request the same window.
    sinces = [c["since"] for c in fake.calls]
    assert sinces == sorted(sinces)
    assert len(set(sinces)) == len(sinces)


def test_fetch_passes_correct_limit(tmp_path):
    start_ms = parse_iso_date("2024-01-01")
    candles = make_candles(start_ms, 5, TIMEFRAME_MS["1d"])
    fake = FakeExchange(candles)
    fetch_ohlcv(
        "2024-01-01", "2024-01-05", "1d",
        cache_dir=tmp_path, exchange=fake, use_cache=False,
    )
    assert all(call["limit"] == BINANCE_PAGE_LIMIT for call in fake.calls)


# --- fetch_ohlcv: range trimming ---------------------------------------

def test_fetch_trims_overshoot_past_end(tmp_path):
    """If exchange returns candles past end_ms, they must be dropped."""
    start_ms = parse_iso_date("2024-01-01")
    # Tape contains 10 candles but we only want 5 days
    candles = make_candles(start_ms, 10, TIMEFRAME_MS["1d"])
    fake = FakeExchange(candles)
    df = fetch_ohlcv(
        "2024-01-01", "2024-01-05", "1d",
        cache_dir=tmp_path, exchange=fake, use_cache=False,
    )
    # End is inclusive: 2024-01-01..2024-01-05 = 5 days
    assert len(df) == 5
    assert df.iloc[-1]["timestamp"] == datetime(2024, 1, 5, tzinfo=timezone.utc)


def test_fetch_end_before_start_raises(tmp_path):
    fake = FakeExchange([])
    with pytest.raises(ValueError, match="before start"):
        fetch_ohlcv(
            "2024-12-31", "2024-01-01", "1d",
            cache_dir=tmp_path, exchange=fake, use_cache=False,
        )


def test_fetch_unknown_timeframe_raises(tmp_path):
    fake = FakeExchange([])
    with pytest.raises(ValueError, match="Unsupported timeframe"):
        fetch_ohlcv(
            "2024-01-01", "2024-01-05", "3h",
            cache_dir=tmp_path, exchange=fake, use_cache=False,
        )


# --- fetch_ohlcv: caching ----------------------------------------------

def test_fetch_writes_parquet_cache_file(tmp_path):
    start_ms = parse_iso_date("2024-01-01")
    candles = make_candles(start_ms, 5, TIMEFRAME_MS["1d"])
    fake = FakeExchange(candles)
    fetch_ohlcv(
        "2024-01-01", "2024-01-05", "1d",
        cache_dir=tmp_path, exchange=fake, use_cache=True,
    )
    expected = tmp_path / "binanceus_BTC_USDT_1d_20240101_20240105.parquet"
    assert expected.exists()


def test_fetch_uses_cache_on_second_call(tmp_path):
    start_ms = parse_iso_date("2024-01-01")
    candles = make_candles(start_ms, 5, TIMEFRAME_MS["1d"])

    # First call hits the network (the fake)
    fake1 = FakeExchange(candles)
    df1 = fetch_ohlcv(
        "2024-01-01", "2024-01-05", "1d",
        cache_dir=tmp_path, exchange=fake1, use_cache=True,
    )

    # Second call should NOT call the exchange — pass a fake that would
    # return wrong data if used, to prove the cache short-circuits.
    fake2 = FakeExchange([])
    df2 = fetch_ohlcv(
        "2024-01-01", "2024-01-05", "1d",
        cache_dir=tmp_path, exchange=fake2, use_cache=True,
    )
    assert len(fake2.calls) == 0
    pd.testing.assert_frame_equal(df1, df2)


def test_fetch_use_cache_false_bypasses_cache(tmp_path):
    start_ms = parse_iso_date("2024-01-01")
    candles = make_candles(start_ms, 5, TIMEFRAME_MS["1d"])
    fake1 = FakeExchange(candles)
    fetch_ohlcv(
        "2024-01-01", "2024-01-05", "1d",
        cache_dir=tmp_path, exchange=fake1, use_cache=True,
    )
    fake2 = FakeExchange(candles)
    fetch_ohlcv(
        "2024-01-01", "2024-01-05", "1d",
        cache_dir=tmp_path, exchange=fake2, use_cache=False,
    )
    assert len(fake2.calls) >= 1


# --- fetch_ohlcv: dedup -------------------------------------------------

def test_fetch_deduplicates_overlapping_candles(tmp_path):
    """If pagination returns the same candle twice (rare but possible),
    we must dedup before returning."""
    start_ms = parse_iso_date("2024-01-01")
    base = make_candles(start_ms, 5, TIMEFRAME_MS["1d"])
    # Inject a duplicate timestamp
    duplicate = list(base[2])
    candles = base + [duplicate]
    fake = FakeExchange(candles)
    df = fetch_ohlcv(
        "2024-01-01", "2024-01-05", "1d",
        cache_dir=tmp_path, exchange=fake, use_cache=False,
    )
    assert len(df) == 5
    assert df["timestamp"].is_unique


# --- fetch_ohlcv: empty range ------------------------------------------

def test_fetch_empty_response_writes_empty_cache(tmp_path):
    """No data from exchange → DataFrame is empty but cache still written
    so subsequent calls don't retry the network for a known-empty range."""
    fake = FakeExchange([])
    df = fetch_ohlcv(
        "2024-01-01", "2024-01-05", "1d",
        cache_dir=tmp_path, exchange=fake, use_cache=True,
    )
    assert df.empty
    expected = tmp_path / "binanceus_BTC_USDT_1d_20240101_20240105.parquet"
    assert expected.exists()


# --- on_page callback --------------------------------------------------

def test_on_page_called_per_page(tmp_path):
    """The progress callback is fired once per non-empty page."""
    start_ms = parse_iso_date("2020-01-01")
    n = 2300
    candles = make_candles(start_ms, n, TIMEFRAME_MS["1d"])
    fake = FakeExchange(candles)
    end_iso = datetime.fromtimestamp(
        (start_ms + (n - 1) * TIMEFRAME_MS["1d"]) / 1000, tz=timezone.utc
    ).strftime("%Y-%m-%d")

    pages: list[tuple[int, int]] = []
    fetch_ohlcv(
        "2020-01-01", end_iso, "1d",
        cache_dir=tmp_path, exchange=fake, use_cache=False,
        on_page=lambda page_size, total: pages.append((page_size, total)),
    )
    # First two pages should be full (1000), last partial (300).
    assert len(pages) == 3
    assert pages[0][0] == 1000
    assert pages[1][0] == 1000
    assert pages[2][0] == 300
    assert pages[-1][1] == n  # total tracks running count
