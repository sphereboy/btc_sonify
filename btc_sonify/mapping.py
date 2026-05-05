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

**Within-candle melodic motion** — instead of one static note on the
close, non-doji candles play a short phrase across the slot: open→close
on modest moves, full open→low→high→close traversal on wide-range bars
(reversed for red, since red candles probe up before falling). This
gives the melody actual contour inside each beat — the single biggest
'soul' improvement over a metronomic one-note-per-candle mapping.

**Humanization** — velocity gets a deterministic ±N jitter per note and
note onsets are nudged ±N ticks. Both are seeded from candle index +
sub-position so determinism is preserved (same input always yields the
same MIDI), but the lead no longer sounds like a quantized step
sequencer. Toggle with ``RunConfig(humanize=False)`` for strict mode.

**Rest insertion** — candles in the bottom percentile of volume drop
their melody note entirely, leaving the harmony pad and percussion to
carry through. This is the 'breath' that makes the piece feel composed.

The output is a list of ``MidiEvent`` namedtuples in (channel, note,
velocity, start_tick, duration_ticks) form. The writer in step 5 sorts
defensively because grace notes for candle N start *before* the main
note for N, and humanized timing offsets can re-order adjacent notes.

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


# --- Within-candle phrase ----------------------------------------------

def _melody_phrase(feat: dict, range_median: float, modest_factor: float) -> list[float]:
    """Choose the price points the melody traverses inside one candle.

    Returns 1, 2, or 4 prices. Doji is handled separately (trill); this
    function assumes a non-doji candle. The shape rules:

    - Tiny range (range < median * modest_factor) → 1 note (close).
      Nothing meaningful happened; don't pretend it did.
    - Strong-body OR not-wide range → 2 notes (open, close). The body is
      the story; play the body.
    - Wide range with mid body → 4 notes traversing the wicks in the
      conventional reading order: green = open→low→high→close (price
      dipped, rallied, settled), red = open→high→low→close (price
      probed up, gave back, settled).
    """
    rng = feat["range"]
    body_ratio = feat["body_ratio"]

    if range_median > 0 and rng < range_median * modest_factor:
        return [feat["close"]]
    if body_ratio > 0.7 or (range_median > 0 and rng < range_median):
        return [feat["open"], feat["close"]]
    if feat["direction"] == "green":
        return [feat["open"], feat["low"], feat["high"], feat["close"]]
    return [feat["open"], feat["high"], feat["low"], feat["close"]]


# --- Humanization -------------------------------------------------------

# Knuth's multiplicative hash constant — mixes bits well for small inputs.
_KNUTH = 2654435761


def _stable_jitter(idx: int, salt: int, max_abs: int) -> int:
    """Deterministic pseudo-random integer in [-max_abs, +max_abs] from
    (idx, salt). Stable across Python runs (no hash() use)."""
    if max_abs <= 0:
        return 0
    h = ((idx + 1) * (salt + 17) * _KNUTH) & 0xFFFFFFFF
    return h % (2 * max_abs + 1) - max_abs


def _humanize_velocity(base: int, idx: int, salt: int, config: RunConfig) -> int:
    if not config.humanize:
        return base
    jitter = _stable_jitter(idx, salt, config.velocity_jitter_range)
    return max(config.velocity_min, min(config.velocity_max, base + jitter))


def _humanize_timing(idx: int, salt: int, config: RunConfig) -> int:
    if not config.humanize:
        return 0
    return _stable_jitter(idx, salt + 1000, config.timing_jitter_ticks)


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

    velocities = _normalize_volume_log(volumes, config.velocity_min, config.velocity_max)
    tiers = _range_tiers(ranges)
    range_median = float(ranges.median()) if len(ranges) else 0.0

    # Single normalization scheme based on close min/max — within-candle
    # wicks may fall slightly outside [0,1] but quantize_to_scale clamps.
    close_min = float(closes.min())
    close_max = float(closes.max())

    def normalize(p: float) -> float:
        if close_max == close_min:
            return 0.0
        return (float(p) - close_min) / (close_max - close_min)

    # Rest insertion threshold (bottom N% of volumes are silent in melody).
    rest_pct = config.rest_volume_percentile
    if rest_pct > 0 and len(volumes) >= 10:
        rest_threshold = float(np.quantile(volumes.to_numpy(dtype=float), rest_pct))
    else:
        rest_threshold = -1.0  # disabled — no rests

    candle_ticks = config.candle_ticks
    grace_ticks = config.grace_ticks
    pad = grace_ticks  # leading silence so candle 0's grace fits

    events: list[MidiEvent] = []

    for i, row in df.reset_index(drop=True).iterrows():
        feat = _candle_features(row)
        candle_start = pad + i * candle_ticks

        close_note = quantize_to_scale(
            normalize(closes[i]), config.scale, root_midi, config.octaves
        )

        base_velocity = int(round(velocities[i]))
        base_velocity = max(config.velocity_min, min(config.velocity_max, base_velocity))

        is_doji = feat["body_ratio"] < config.body_ratio_doji
        is_rest = (rest_threshold > 0 and float(volumes[i]) <= rest_threshold)

        # --- Melody branch -------------------------------------------
        if is_rest:
            # Skip melody entirely — the harmony pad and drums carry through.
            # We still keep a "phantom" velocity for harmony-velocity scaling
            # so quiet bars get quiet harmony pads, not just silent melody
            # over loud strings.
            melody_velocity_for_harmony = base_velocity

        elif is_doji:
            # Trill: alternate close and one scale-step above for the
            # candle's full slot, divided into N subdivisions.
            up = scale_step(close_note, scale_notes, 1)
            n = max(2, config.trill_subdivisions)
            sub = candle_ticks // n
            for k in range(n):
                pitch = close_note if k % 2 == 0 else up
                v = _humanize_velocity(base_velocity, i, k, config)
                t_off = _humanize_timing(i, k, config) if k > 0 else 0
                events.append(MidiEvent(
                    channel=config.melody_channel,
                    note=pitch,
                    velocity=v,
                    start_tick=candle_start + k * sub + t_off,
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

            # Within-candle phrase: 1, 2, or 4 prices traversed across the slot.
            phrase = _melody_phrase(feat, range_median, config.range_modest_factor)
            n_notes = len(phrase)
            sub = candle_ticks // n_notes if n_notes > 0 else candle_ticks
            sub_dur = max(1, int(round(sub * fraction)))

            for k, price in enumerate(phrase):
                pitch = quantize_to_scale(
                    normalize(price), config.scale, root_midi, config.octaves
                )
                v = _humanize_velocity(note_velocity, i, k, config)
                # Don't offset the very first sub-note (the candle's
                # downbeat) — keep the grid intact at the bar line.
                t_off = _humanize_timing(i, k, config) if k > 0 else 0
                events.append(MidiEvent(
                    channel=config.melody_channel,
                    note=pitch,
                    velocity=v,
                    start_tick=candle_start + k * sub + t_off,
                    duration_ticks=sub_dur,
                ))
            melody_velocity_for_harmony = note_velocity

        # --- Ornamentation: long-wick grace notes (skip on rest) -----
        if not is_rest:
            grace_velocity = _humanize_velocity(base_velocity, i, 50, config)
            if feat["upper_wick"] > config.wick_grace_multiplier * feat["body_size"]:
                grace = scale_step(close_note, scale_notes, 1)
                events.append(MidiEvent(
                    channel=config.melody_channel,
                    note=grace,
                    velocity=grace_velocity,
                    start_tick=candle_start - grace_ticks,
                    duration_ticks=grace_ticks,
                ))
            if feat["lower_wick"] > config.wick_grace_multiplier * feat["body_size"]:
                grace = scale_step(close_note, scale_notes, -1)
                events.append(MidiEvent(
                    channel=config.melody_channel,
                    note=grace,
                    velocity=grace_velocity,
                    start_tick=candle_start - grace_ticks,
                    duration_ticks=grace_ticks,
                ))

        # --- Harmony track: chord on the second channel -------------
        chord = _harmony_notes(close_note, int(tiers[i]), scale_notes)
        h_vel_base = max(
            config.velocity_min,
            min(config.velocity_max,
                int(round(melody_velocity_for_harmony * config.harmony_velocity_factor))),
        )
        for j, note in enumerate(chord):
            # Salt with +100 so harmony jitters independently of melody.
            v = _humanize_velocity(h_vel_base, i, j + 100, config)
            events.append(MidiEvent(
                channel=config.harmony_channel,
                note=note,
                velocity=v,
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
    "_melody_phrase",
    "_stable_jitter",
    "_humanize_velocity",
    "_humanize_timing",
]
