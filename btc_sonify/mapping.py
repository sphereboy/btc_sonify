"""OHLCV DataFrame → MIDI event list.

This is the heart of the sonification. The mapping is deterministic: the
same DataFrame and the same RunConfig always yield the same list of
events, in the same order. Musicality comes from a thoughtful mapping,
not from randomness.

The mapping rules — pitch from close, velocity from log(volume),
articulation from direction × body_ratio, ornaments from wicks, harmony
chord size from candle range — come straight from CLAUDE.md "Core
mapping spec". Each rule is exposed as a named threshold on RunConfig
so they can be tuned later without surgery on this module.

The output is a list of ``MidiEvent`` namedtuples in (channel, note,
velocity, start_tick, duration_ticks) form. Events are emitted in
chronological order, but the writer in step 5 should still sort defensively
because grace notes for candle N start *before* the main note for N.

Padding: the entire song is shifted forward by one ``grace_ticks`` so the
first candle's grace note (if any) has space to play before tick 0
becomes "before the song starts".
"""
from __future__ import annotations

from typing import NamedTuple

import numpy as np
import pandas as pd

from btc_sonify.config import RunConfig
from btc_sonify.scales import (
    build_scale_notes,
    note_name_to_midi,
    quantize_to_scale,
    scale_step,
)


class MidiEvent(NamedTuple):
    """One MIDI note. Tuple shape per CLAUDE.md."""
    channel: int
    note: int
    velocity: int
    start_tick: int
    duration_ticks: int


# --- Normalization helpers ----------------------------------------------

def _normalize_close(closes: pd.Series) -> np.ndarray:
    """Min-max normalize close prices to [0, 1] over the whole series.

    Per CLAUDE.md we use the full-series min/max, not a rolling window —
    we want the macro shape audible. If every close is identical (a
    completely flat range) we return zeros, which puts every note at the
    root — defensible and avoids a divide-by-zero.
    """
    arr = closes.to_numpy(dtype=float)
    lo, hi = arr.min(), arr.max()
    if hi == lo:
        return np.zeros_like(arr)
    return (arr - lo) / (hi - lo)


def _normalize_volume_log(
    volumes: pd.Series, vmin: int, vmax: int
) -> np.ndarray:
    """Map volume to MIDI velocity in [vmin, vmax] on a log scale.

    Volume distributions are heavy-tailed: a few huge bars and many small
    ones. Linear normalization makes the median candle whisper-quiet,
    which sonifies poorly. log1p lifts the small values into audibility
    while preserving relative loudness for spikes. Volumes of zero are
    handled (log1p(0) = 0) and clamp to vmin.
    """
    arr = volumes.to_numpy(dtype=float)
    arr = np.where(arr < 0, 0, arr)  # defensive: no negative volume
    logged = np.log1p(arr)
    lo, hi = logged.min(), logged.max()
    if hi == lo:
        return np.full_like(logged, vmin, dtype=float)
    norm = (logged - lo) / (hi - lo)
    return vmin + norm * (vmax - vmin)


# --- Per-candle derivations ---------------------------------------------

def _candle_features(row: pd.Series) -> dict:
    """Compute derived features for a single candle."""
    o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
    body_size = abs(c - o)
    cand_range = h - l
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    # body_ratio is undefined for a zero-range candle (impossibly flat).
    # Treat it as a doji (ratio = 0): "no movement" is closer to a doji
    # than to a strong-body candle.
    body_ratio = body_size / cand_range if cand_range > 0 else 0.0
    return {
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "direction": "green" if c >= o else "red",
        "body_size": body_size,
        "range": cand_range,
        "upper_wick": upper_wick,
        "lower_wick": lower_wick,
        "body_ratio": body_ratio,
    }


# --- Articulation -------------------------------------------------------

def _articulation_fraction(
    direction: str, body_ratio: float, config: RunConfig
) -> tuple[float, int]:
    """Return (duration_fraction, velocity_bonus) for the candle.

    Doji (body_ratio < doji threshold) is handled separately as a trill
    in the main loop — this function only covers the four directional
    articulations.
    """
    if direction == "green":
        if body_ratio > config.body_ratio_strong:
            return config.legato_fraction, 0
        return config.normal_fraction, 0
    # red
    if body_ratio > config.body_ratio_strong:
        return config.marcato_fraction, config.marcato_velocity_bonus
    return config.staccato_fraction, 0


# --- Harmony chord ------------------------------------------------------

def _harmony_notes(
    close_note: int,
    range_tier: int,
    scale_notes: list[int],
) -> list[int]:
    """Build the harmony chord for a candle based on its range tier.

    range_tier: 0 = bottom 33% (single note), 1 = middle 33% (diad,
    close + fifth), 2 = top 33% (triad, close + third + fifth).
    Intervals are scale-aware: 'third' = 2 scale steps, 'fifth' = 4
    scale steps. This keeps the chord inside the chosen mode rather than
    forcing chromatic intervals that would clash with non-major scales.
    """
    if range_tier == 0:
        return [close_note]
    fifth = scale_step(close_note, scale_notes, 4)
    if range_tier == 1:
        return [close_note, fifth]
    third = scale_step(close_note, scale_notes, 2)
    return [close_note, third, fifth]


def _range_tiers(ranges: pd.Series) -> np.ndarray:
    """Bucket each candle's high-low range into tier 0/1/2 by tercile.

    Uses the full-series quantiles so terciles are stable across the
    whole sonification. If every range is identical, all candles fall
    into tier 0 (single note).
    """
    arr = ranges.to_numpy(dtype=float)
    if np.all(arr == arr[0]):
        return np.zeros_like(arr, dtype=int)
    q33, q66 = np.quantile(arr, [1 / 3, 2 / 3])
    tiers = np.where(arr <= q33, 0, np.where(arr <= q66, 1, 2))
    return tiers.astype(int)


# --- Main entry point ---------------------------------------------------

def map_candles_to_events(
    df: pd.DataFrame, config: RunConfig
) -> list[MidiEvent]:
    """Convert an OHLCV DataFrame into a deterministic list of MIDI events.

    The DataFrame must have columns 'open', 'high', 'low', 'close',
    'volume' (the standard OHLCV shape from data.fetch_ohlcv). Order is
    preserved — candle 0 plays first. The first candle's optional grace
    note plays at tick 0 thanks to a leading silence pad of grace_ticks.
    """
    if df.empty:
        return []

    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"DataFrame missing required columns: {sorted(missing)}")

    root_midi = note_name_to_midi(config.root, config.root_octave)
    scale_notes = build_scale_notes(config.scale, root_midi, config.octaves)

    closes = df["close"].reset_index(drop=True)
    volumes = df["volume"].reset_index(drop=True)
    ranges = (df["high"] - df["low"]).reset_index(drop=True)

    norm_close = _normalize_close(closes)
    velocities = _normalize_volume_log(volumes, config.velocity_min, config.velocity_max)
    tiers = _range_tiers(ranges)

    candle_ticks = config.candle_ticks
    grace_ticks = config.grace_ticks
    pad = grace_ticks  # leading silence so candle 0's grace fits

    events: list[MidiEvent] = []

    for i, row in df.reset_index(drop=True).iterrows():
        feat = _candle_features(row)
        candle_start = pad + i * candle_ticks

        # --- Pitch: snap normalized close to scale ladder
        close_note = quantize_to_scale(
            norm_close[i], config.scale, root_midi, config.octaves
        )

        # --- Velocity: log-normalized volume, clamp + cast
        base_velocity = int(round(velocities[i]))
        base_velocity = max(config.velocity_min, min(config.velocity_max, base_velocity))

        # --- Articulation / trill (doji is its own branch)
        is_doji = feat["body_ratio"] < config.body_ratio_doji

        if is_doji:
            # Trill: alternate close note and one scale-step above for
            # the candle's full slot, divided into N equal subdivisions.
            up = scale_step(close_note, scale_notes, 1)
            n = max(2, config.trill_subdivisions)
            sub = candle_ticks // n
            for k in range(n):
                pitch = close_note if k % 2 == 0 else up
                events.append(MidiEvent(
                    channel=config.melody_channel,
                    note=pitch,
                    velocity=base_velocity,
                    start_tick=candle_start + k * sub,
                    duration_ticks=sub,
                ))
            melody_velocity_for_harmony = base_velocity
        else:
            fraction, vel_bonus = _articulation_fraction(
                feat["direction"], feat["body_ratio"], config
            )
            note_velocity = max(
                config.velocity_min,
                min(config.velocity_max, base_velocity + vel_bonus),
            )
            events.append(MidiEvent(
                channel=config.melody_channel,
                note=close_note,
                velocity=note_velocity,
                start_tick=candle_start,
                duration_ticks=max(1, int(round(candle_ticks * fraction))),
            ))
            melody_velocity_for_harmony = note_velocity

        # --- Ornamentation: long-wick grace notes
        # A grace plays for grace_ticks duration, ending exactly when the
        # main note begins. Both wick directions can fire on the same
        # candle — that's the "long-wick doji" case from CLAUDE.md.
        if feat["upper_wick"] > config.wick_grace_multiplier * feat["body_size"]:
            grace = scale_step(close_note, scale_notes, 1)
            events.append(MidiEvent(
                channel=config.melody_channel,
                note=grace,
                velocity=base_velocity,
                start_tick=candle_start - grace_ticks,
                duration_ticks=grace_ticks,
            ))
        if feat["lower_wick"] > config.wick_grace_multiplier * feat["body_size"]:
            grace = scale_step(close_note, scale_notes, -1)
            events.append(MidiEvent(
                channel=config.melody_channel,
                note=grace,
                velocity=base_velocity,
                start_tick=candle_start - grace_ticks,
                duration_ticks=grace_ticks,
            ))

        # --- Harmony track: chord on the second channel
        chord = _harmony_notes(close_note, int(tiers[i]), scale_notes)
        h_vel = max(
            config.velocity_min,
            min(config.velocity_max,
                int(round(melody_velocity_for_harmony * config.harmony_velocity_factor))),
        )
        # Harmony holds for the full candle slot regardless of melody
        # articulation — pad-like sustain under whatever the melody is doing.
        for note in chord:
            events.append(MidiEvent(
                channel=config.harmony_channel,
                note=note,
                velocity=h_vel,
                start_tick=candle_start,
                duration_ticks=candle_ticks,
            ))

    return events


# Module-level helpers exposed for testing of the individual pieces.
__all__ = [
    "MidiEvent",
    "map_candles_to_events",
    "_normalize_close",
    "_normalize_volume_log",
    "_candle_features",
    "_articulation_fraction",
    "_harmony_notes",
    "_range_tiers",
]
