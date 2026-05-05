"""Percussion track for symphony mode.

Adds a third MIDI channel (the GM standard drum channel 9) carrying a
kit-style drum part derived from candle properties:

- Closed hi-hat on every candle — the steady heartbeat
- Kick on top-decile volume candles — punctuates the big bars
- Snare on top-decile range candles — wide-range slaps
- Ride bell on doji candles — indecision shimmer
- Crash cymbal at movement boundaries — announces transitions

Drums sit at 50% of the melody velocity by default so they support
rather than bulldoze the harmonic content above.

The percussion track operates on the *full* timeline of the
sonification (across all movements), so it needs the per-movement tick
offsets that symphony mode generates. We pass those in as
``movement_offsets`` rather than recompute them here — single source of
truth.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from btc_sonify.config import (
    DRUM_CRASH,
    DRUM_HI_HAT_CLOSED,
    DRUM_KICK,
    DRUM_RIDE_BELL,
    DRUM_SNARE,
    GM_DRUM_CHANNEL,
    RunConfig,
)
from btc_sonify.mapping import MidiEvent

# Velocity scaling: drums at this fraction of the melody's max velocity.
DRUM_VELOCITY_FACTOR = 0.5
HI_HAT_VELOCITY_FACTOR = 0.30   # the heartbeat sits underneath
CRASH_VELOCITY_FACTOR = 0.65    # a touch louder for transitions

# Percentile thresholds for kick / snare triggers.
VOLUME_DECILE = 0.90
RANGE_DECILE = 0.90


@dataclass(frozen=True)
class MovementOffset:
    """Maps a movement's source-DataFrame index range to its tick offset
    in the global timeline. Optionally carries the per-movement RunConfig
    so consumers (the bass track, future per-movement features) can
    re-quantize against the right scale/root."""
    start_idx: int                       # inclusive
    end_idx: int                         # inclusive
    tick_offset: int                     # ticks to add to the movement's local start
    config: RunConfig | None = None      # per-movement config (symphony only)


def _scaled_velocity(config: RunConfig, factor: float) -> int:
    """Compute a drum velocity scaled from the config's velocity ceiling."""
    v = int(round(config.velocity_max * factor))
    return max(config.velocity_min, min(config.velocity_max, v))


def map_percussion(
    df: pd.DataFrame,
    config: RunConfig,
    movement_offsets: list[MovementOffset] | None = None,
) -> list[MidiEvent]:
    """Render percussion events for the full DataFrame.

    If ``movement_offsets`` is provided, the percussion timeline aligns
    with the symphony's per-movement tick layout (including inter-
    movement rests) and crash cymbals are added at each movement's first
    candle (except the very first movement). If omitted, drums lay on a
    single contiguous timeline that mirrors the plain (non-symphony)
    melody.
    """
    if df.empty:
        return []

    candle_ticks = config.candle_ticks
    pad = config.grace_ticks

    volumes = df["volume"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    opens = df["open"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    ranges = highs - lows

    body = np.abs(closes - opens)
    body_ratio = np.where(ranges > 0, body / ranges, 0.0)

    # Use full-series percentiles so the meaning of "big volume" or
    # "big range" stays consistent across the piece.
    vol_threshold = float(np.quantile(volumes, VOLUME_DECILE)) if len(volumes) else 0.0
    range_threshold = float(np.quantile(ranges, RANGE_DECILE)) if len(ranges) else 0.0

    kick_v = _scaled_velocity(config, DRUM_VELOCITY_FACTOR)
    snare_v = _scaled_velocity(config, DRUM_VELOCITY_FACTOR)
    hat_v = _scaled_velocity(config, HI_HAT_VELOCITY_FACTOR)
    ride_v = _scaled_velocity(config, DRUM_VELOCITY_FACTOR)
    crash_v = _scaled_velocity(config, CRASH_VELOCITY_FACTOR)

    # Build a fast lookup: index -> (tick_offset, is_movement_start).
    # If no movements were given, treat the whole thing as one movement.
    if movement_offsets is None:
        offset_for: dict[int, tuple[int, bool]] = {
            i: (pad, i == 0) for i in range(len(df))
        }
        # In single-movement mode, no crash on candle 0 (no transition).
        for i in offset_for:
            offset_for[i] = (offset_for[i][0], False)
    else:
        offset_for = {}
        for k, m in enumerate(movement_offsets):
            local_pad = m.tick_offset + pad
            for i in range(m.start_idx, m.end_idx + 1):
                # First candle of any movement after the opener gets a crash.
                is_movement_start = (i == m.start_idx and k > 0)
                # Use local index within the movement for tick math
                local_i = i - m.start_idx
                offset_for[i] = (local_pad + local_i * candle_ticks, is_movement_start)

    events: list[MidiEvent] = []
    for i in range(len(df)):
        if i not in offset_for:
            continue
        start, is_movement_start = offset_for[i]

        # Hi-hat: every candle, full duration of the slot at low velocity.
        events.append(MidiEvent(
            channel=GM_DRUM_CHANNEL, note=DRUM_HI_HAT_CLOSED,
            velocity=hat_v,
            start_tick=start,
            duration_ticks=max(1, candle_ticks // 4),
        ))

        # Kick: top-decile volume candles only.
        if volumes[i] >= vol_threshold and vol_threshold > 0:
            events.append(MidiEvent(
                channel=GM_DRUM_CHANNEL, note=DRUM_KICK,
                velocity=kick_v,
                start_tick=start,
                duration_ticks=max(1, candle_ticks // 2),
            ))

        # Snare: top-decile range candles only.
        if ranges[i] >= range_threshold and range_threshold > 0:
            events.append(MidiEvent(
                channel=GM_DRUM_CHANNEL, note=DRUM_SNARE,
                velocity=snare_v,
                start_tick=start,
                duration_ticks=max(1, candle_ticks // 2),
            ))

        # Ride bell: doji candles.
        if body_ratio[i] < config.body_ratio_doji:
            events.append(MidiEvent(
                channel=GM_DRUM_CHANNEL, note=DRUM_RIDE_BELL,
                velocity=ride_v,
                start_tick=start,
                duration_ticks=max(1, candle_ticks // 2),
            ))

        # Crash: at the first candle of each movement after the opener.
        if is_movement_start:
            events.append(MidiEvent(
                channel=GM_DRUM_CHANNEL, note=DRUM_CRASH,
                velocity=crash_v,
                start_tick=start,
                duration_ticks=candle_ticks,
            ))

    return events


__all__ = ["MovementOffset", "map_percussion"]
