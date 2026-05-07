"""Structural-event detection.

Three deterministic detectors that flag candles where something
narratively interesting happens in the price series:

1. **Local pivot** — close is the local max or min within ±5 candles.
2. **Volatility regime shift** — short-window stdev of returns crosses
   above 1.5x or below 0.5x of its long-window trailing mean.
3. **EMA crossover** — fast EMA (20) crosses slow EMA (50).

These are the same triggers the rubato curve uses to drive
rallentando/accelerando, and the same triggers the MIDI writer turns
into labelled markers so a producer scrubbing in a DAW can jump between
the meaningful moments. Sharing the implementation here keeps detection
in one place — rubato and the marker writer cannot disagree about what
counts as a structural event.

All detectors are pure functions of the OHLCV data. Same input always
produces the same masks; same masks always produce the same labels.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

EventKind = Literal[
    "swing_high",
    "swing_low",
    "vol_spike",
    "vol_calm",
    "ma_cross_up",
    "ma_cross_down",
]


@dataclass(frozen=True)
class StructuralEvent:
    """One labelled structural event in the price series.

    ``candle_index`` is a 0-based index into the source DataFrame. The
    label is human-readable and dated when a timestamp column was
    available — it's what shows up in DAW marker lanes.
    """
    kind: EventKind
    candle_index: int
    label: str


# --- Mask detectors (used by rubato + labelled detector) ----------------

def local_pivots(closes: np.ndarray, window: int = 5) -> np.ndarray:
    """Boolean mask: True at indices that are a local maximum or minimum
    within ±``window`` candles AND show actual price variation in the
    window. The strict-variation guard prevents flat segments from
    flagging every candle as a pivot.
    """
    n = len(closes)
    mask = np.zeros(n, dtype=bool)
    if n < 2 * window + 1:
        return mask
    for i in range(window, n - window):
        local = closes[i - window: i + window + 1]
        if local.max() == local.min():
            continue
        if closes[i] == local.max() or closes[i] == local.min():
            mask[i] = True
    return mask


def rolling_std(values: np.ndarray, window: int) -> np.ndarray:
    """Trailing rolling standard deviation. Output length matches input;
    the first ``window-1`` entries are computed from whatever data is
    available so far so short series still get a usable curve."""
    n = len(values)
    out = np.zeros(n, dtype=float)
    for i in range(n):
        start = max(0, i - window + 1)
        out[i] = float(np.std(values[start: i + 1]))
    return out


def vol_regime_shifts(
    returns: np.ndarray, short: int = 20, long: int = 100
) -> np.ndarray:
    """Boolean mask of vol-regime-shift candles: short-window stdev
    crosses above 1.5x or below 0.5x of long-window trailing mean."""
    n = len(returns)
    if n < short + 1:
        return np.zeros(n, dtype=bool)
    short_vol = rolling_std(returns, short)
    long_mean = np.zeros(n, dtype=float)
    for i in range(n):
        start = max(0, i - long + 1)
        long_mean[i] = float(np.mean(short_vol[start: i + 1]))
    ratio = np.where(long_mean > 0, short_vol / np.maximum(long_mean, 1e-12), 1.0)
    shifts = np.zeros(n, dtype=bool)
    for i in range(1, n):
        was_normal = 0.5 <= ratio[i - 1] <= 1.5
        is_extreme = ratio[i] > 1.5 or ratio[i] < 0.5
        if was_normal and is_extreme:
            shifts[i] = True
    return shifts


def ema_crossovers(closes: np.ndarray, fast: int = 20, slow: int = 50) -> np.ndarray:
    """Boolean mask of EMA-crossover candles (fast EMA crosses slow)."""
    n = len(closes)
    if n < slow:
        return np.zeros(n, dtype=bool)
    ema_fast = pd.Series(closes).ewm(span=fast, adjust=False).mean().to_numpy()
    ema_slow = pd.Series(closes).ewm(span=slow, adjust=False).mean().to_numpy()
    sign = np.sign(ema_fast - ema_slow)
    cross = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if sign[i] != sign[i - 1] and sign[i] != 0 and sign[i - 1] != 0:
            cross[i] = True
    return cross


def structural_event_mask(closes: np.ndarray, returns: np.ndarray) -> np.ndarray:
    """Union of the three structural-event detectors. Used by rubato to
    drive the rallentando/climax curve."""
    return local_pivots(closes) | vol_regime_shifts(returns) | ema_crossovers(closes)


# --- Labelled detector --------------------------------------------------

def _date_suffix(timestamps: pd.Series | None, idx: int) -> str:
    """``_2020-03-12`` if a timestamp series is available, else ``_idx``.
    Empty string signals "no suffix" — caller decides."""
    if timestamps is None:
        return f"_idx{idx}"
    ts = timestamps.iloc[idx]
    return f"_{ts.strftime('%Y-%m-%d')}"


def detect_structural_events(df: pd.DataFrame) -> list[StructuralEvent]:
    """Return a chronologically-sorted list of labelled structural events.

    Each event carries a ``kind`` distinguishing the *direction* of the
    trigger (swing high vs low, vol spike vs calm, MA cross up vs down)
    and a date-bearing label suitable for a DAW marker lane.

    Multiple kinds can fire on the same candle — for example a swing low
    that coincides with an EMA cross-up is two events, both emitted.
    """
    n = len(df)
    if n == 0:
        return []

    closes = df["close"].to_numpy(dtype=float)
    log_returns = np.diff(
        np.log(np.maximum(closes, 1e-12)),
        prepend=np.log(max(closes[0], 1e-12)),
    )
    timestamps = (
        pd.to_datetime(df["timestamp"], utc=True)
        if "timestamp" in df.columns
        else None
    )

    pivots = local_pivots(closes)
    vol_shifts = vol_regime_shifts(log_returns)
    crosses = ema_crossovers(closes)

    # Pre-compute the EMA delta at every candle so we can label crossovers
    # by direction without recomputing inside the loop.
    if n >= 50:
        ema_fast = pd.Series(closes).ewm(span=20, adjust=False).mean().to_numpy()
        ema_slow = pd.Series(closes).ewm(span=50, adjust=False).mean().to_numpy()
        ema_delta = ema_fast - ema_slow
    else:
        ema_delta = np.zeros(n, dtype=float)

    # Precompute the short-vol ratio for vol-shift labelling (spike vs calm).
    short_vol = rolling_std(log_returns, window=20)
    long_mean = np.zeros(n, dtype=float)
    for i in range(n):
        start = max(0, i - 100 + 1)
        long_mean[i] = float(np.mean(short_vol[start: i + 1]))
    vol_ratio = np.where(
        long_mean > 0, short_vol / np.maximum(long_mean, 1e-12), 1.0,
    )

    out: list[StructuralEvent] = []
    pivot_window = 5
    for i in range(n):
        # Pivot — distinguish high vs low by comparing to local extrema.
        if pivots[i]:
            local = closes[max(0, i - pivot_window): min(n, i + pivot_window + 1)]
            if closes[i] == local.max():
                kind: EventKind = "swing_high"
            else:
                kind = "swing_low"
            out.append(StructuralEvent(
                kind=kind,
                candle_index=i,
                label=f"{kind}{_date_suffix(timestamps, i)}",
            ))

        # Vol regime shift — spike vs calm.
        if vol_shifts[i]:
            kind = "vol_spike" if vol_ratio[i] > 1.5 else "vol_calm"
            out.append(StructuralEvent(
                kind=kind,
                candle_index=i,
                label=f"{kind}{_date_suffix(timestamps, i)}",
            ))

        # EMA crossover — up vs down by current sign of (fast - slow).
        if crosses[i]:
            kind = "ma_cross_up" if ema_delta[i] > 0 else "ma_cross_down"
            out.append(StructuralEvent(
                kind=kind,
                candle_index=i,
                label=f"{kind}{_date_suffix(timestamps, i)}",
            ))

    return out


__all__ = [
    "EventKind",
    "StructuralEvent",
    "local_pivots",
    "rolling_std",
    "vol_regime_shifts",
    "ema_crossovers",
    "structural_event_mask",
    "detect_structural_events",
]
