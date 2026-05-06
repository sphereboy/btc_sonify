"""Rubato: within-movement tempo breathing.

A locked tempo across an 8-hour BTC sonification produces a metronome
with pitches. Real performers — Khatia Buniatishvili's *Clair de Lune*
is the canonical reference — *take time* into climaxes, *hold* at
turning points, and *push through* trending passages. This module
adds that breathing layer on top of the per-movement TempoMarker
already emitted by ``symphony.map_symphony``.

Mechanism. For every candle in a movement, derive a BPM multiplier
in ``[config.rubato_min_factor, config.rubato_max_factor]`` from four
signals:

1. **Approach** — rallentando into a structural event (local pivot,
   vol regime shift, EMA crossover) within the next
   ``rubato_approach_window`` candles. Pulls tempo down.
2. **Climax** — held suspension AT the structural event itself.
   Pulls tempo down hardest.
3. **Trend** — accelerando during runs of 3+ same-direction candles
   with EMA20-EMA50 reinforcing the direction. Pushes tempo up,
   modestly.
4. **Volatility bias** — slower in low-vol consolidation, faster in
   high-vol expansion.

The combined factor is smoothed across ``rubato_smoothing_window``
candles so the result *breathes* rather than flickering, then
quantized into ``rubato_quantize_step`` BPM buckets so the meta
track stays readable in DAWs (a typical 5-year run produces
~30-80 markers, not thousands).

Audit invariant. Rubato modulates *real-time playback speed* only,
via meta-track ``set_tempo`` events. The per-candle tick count
(``config.candle_ticks``) is unchanged — every candle still occupies
exactly one beat in the score. Same OHLCV + same config → byte-
identical MIDI.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from btc_sonify.config import RunConfig
from btc_sonify.symphony import Movement, TempoMarker


# --- Signal detection ---------------------------------------------------

def _local_pivots(closes: np.ndarray, window: int = 5) -> np.ndarray:
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


def _rolling_std(values: np.ndarray, window: int) -> np.ndarray:
    """Trailing rolling standard deviation. Output length matches input;
    the first ``window-1`` entries are computed from whatever data is
    available so far so short series still get a usable curve."""
    n = len(values)
    out = np.zeros(n, dtype=float)
    for i in range(n):
        start = max(0, i - window + 1)
        out[i] = float(np.std(values[start: i + 1]))
    return out


def _vol_regime_shifts(
    returns: np.ndarray, short: int = 20, long: int = 100
) -> np.ndarray:
    """Boolean mask of vol-regime-shift candles: short-window stdev
    crosses above 1.5× or below 0.5× of long-window trailing mean."""
    n = len(returns)
    if n < short + 1:
        return np.zeros(n, dtype=bool)
    short_vol = _rolling_std(returns, short)
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


def _ema_crossovers(closes: np.ndarray, fast: int = 20, slow: int = 50) -> np.ndarray:
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


def _structural_events(closes: np.ndarray, returns: np.ndarray) -> np.ndarray:
    """Union of the three structural-event detectors."""
    return _local_pivots(closes) | _vol_regime_shifts(returns) | _ema_crossovers(closes)


# --- Curve computation --------------------------------------------------

def compute_rubato_curve(
    df: pd.DataFrame,
    base_bpm: int,
    config: RunConfig,
) -> np.ndarray:
    """Per-candle bucketed BPM curve for one movement.

    ``len(out) == len(df)``; ``out[i]`` is the integer BPM in effect
    from the start of candle i, smoothed and quantized to
    ``config.rubato_quantize_step``. Ready to be diff'd into TempoMarker
    events by :func:`interleave_rubato_markers`.
    """
    n = len(df)
    if n == 0:
        return np.array([], dtype=int)
    if n == 1:
        return np.full(1, int(base_bpm), dtype=int)

    closes = df["close"].to_numpy(dtype=float)
    log_returns = np.diff(np.log(np.maximum(closes, 1e-12)), prepend=np.log(max(closes[0], 1e-12)))

    # 1. Approach + climax
    events = _structural_events(closes, log_returns)
    approach_window = max(1, config.rubato_approach_window)
    approach = np.zeros(n, dtype=float)
    climax = events.astype(float)
    for i in range(n):
        end = min(n, i + 1 + approach_window)
        future = events[i + 1: end]
        if not future.any():
            continue
        d = int(np.argmax(future)) + 1   # distance to nearest future event, 1..W
        approach[i] = max(approach[i], 1.0 - (d - 1) / approach_window)

    # 2. Trend (run length × EMA agreement, suppressed below 3-candle threshold)
    direction = np.sign(np.diff(closes, prepend=closes[0]))
    run_len = np.zeros(n, dtype=float)
    rl = 0
    last_dir = 0.0
    for i, d in enumerate(direction):
        if d != 0 and d == last_dir:
            rl += 1
        else:
            rl = 1 if d != 0 else 0
        run_len[i] = rl
        last_dir = d
    if n >= 50:
        ema_fast = pd.Series(closes).ewm(span=20, adjust=False).mean().to_numpy()
        ema_slow = pd.Series(closes).ewm(span=50, adjust=False).mean().to_numpy()
        ema_dir = np.sign(ema_fast - ema_slow)
        agree = (direction * ema_dir > 0).astype(float)
    else:
        agree = np.ones(n, dtype=float)
    trend = np.minimum(run_len / 8.0, 1.0) * agree
    trend[run_len < 3] = 0.0

    # 3. Volatility bias (percentile rank within the movement)
    short_vol = _rolling_std(log_returns, window=min(20, max(2, n // 2)))
    if short_vol.max() > 0:
        order = short_vol.argsort().argsort()
        vol_pct = order / max(1, n - 1)
    else:
        vol_pct = np.full(n, 0.5)

    # 4. Combine — climax dominates over approach when both fire
    slow_pull = np.maximum(approach, climax * 1.4)
    fast_pull = trend * 0.5
    vol_bias = (vol_pct - 0.5) * 0.3
    factor = 1.0 - slow_pull * 0.35 + fast_pull * 0.20 + vol_bias
    factor = np.clip(factor, config.rubato_min_factor, config.rubato_max_factor)

    # 5. Smooth — center=True lets the curve "anticipate" upcoming events
    win = max(1, config.rubato_smoothing_window)
    if win > 1:
        factor = (
            pd.Series(factor)
            .rolling(window=win, min_periods=1, center=True)
            .mean()
            .to_numpy()
        )

    # 6. Quantize to BPM buckets
    bpm = base_bpm * factor
    step = max(1, config.rubato_quantize_step)
    return (np.round(bpm / step).astype(int) * step)


def interleave_rubato_markers(
    df: pd.DataFrame,
    movements: list[Movement],
    movement_configs: list[RunConfig],
    movement_tick_offsets: list[int],
    base_config: RunConfig,
) -> list[TempoMarker]:
    """Compute a rubato BPM curve per movement and emit unlabelled
    TempoMarkers at every BPM-bucket change. The headline per-movement
    marker (already added by ``map_symphony`` at each ``tick_offset``)
    is not duplicated; we emit only when the curve diverges from the
    movement's nominal BPM.

    Returns an unsorted list — the meta-track writer sorts by tick.
    """
    if not movements:
        return []

    out: list[TempoMarker] = []
    candle_ticks = base_config.candle_ticks

    for mov, cfg, tick_offset in zip(
        movements, movement_configs, movement_tick_offsets
    ):
        if not cfg.rubato:
            continue
        seg = df.iloc[mov.start_idx: mov.end_idx + 1]
        # Movements shorter than 8 candles can't meaningfully breathe;
        # the smoothing window alone is wider than that.
        if len(seg) < 8:
            continue

        bpm_curve = compute_rubato_curve(seg, cfg.bpm, cfg)
        prev_bpm = cfg.bpm
        for i, bpm in enumerate(bpm_curve):
            bpm_int = int(bpm)
            if bpm_int <= 0 or bpm_int == prev_bpm:
                continue
            tick = tick_offset + i * candle_ticks
            # Avoid same-tick collision with the headline movement marker
            # — push 1 tick (sub-millisecond) past tick_offset.
            if tick == tick_offset:
                tick = tick_offset + 1
            out.append(TempoMarker(tick=tick, bpm=bpm_int, label=""))
            prev_bpm = bpm_int
    return out


__all__ = [
    "compute_rubato_curve",
    "interleave_rubato_markers",
]
