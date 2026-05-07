"""Bar/candle timeline computation.

Maps each candle in the source DataFrame to its absolute start time in
the rendered audio, accounting for per-movement tempo changes and the
one-beat rest between movements. Two consumers depend on this:

- ``btc_sonify.visualize`` — uses the timeline to sync the playhead and
  the candlestick chart in the HTML companion.
- ``btc_sonify.sidecar`` — uses the same timeline to write per-bar
  ``tick_start`` and ``date`` entries in the JSON sidecar.

Putting it here means the visualizer and the sidecar can never disagree
about which bar a given candle lives on. The math lives once.

Plain mode (no symphony) synthesises a single virtual movement covering
the whole DataFrame at the base tempo so callers don't have to special-
case the "no movements" path.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from btc_sonify.config import RunConfig
from btc_sonify.symphony import RenderedMovement


@dataclass(frozen=True)
class TimelineCandle:
    """One candle's position in the rendered audio timeline.

    ``start_s`` / ``duration_s`` are the audio seconds (used by the
    visualizer for playhead sync); ``start_tick`` is the absolute MIDI
    tick where the candle's downbeat lands in the rendered .mid (used by
    the sidecar so DAW navigation lines up with the audio).
    """
    idx: int
    start_s: float
    duration_s: float
    start_tick: int
    movement: int


@dataclass(frozen=True)
class TimelineMovement:
    """One movement's audio + tick start/end and its musical metadata."""
    index: int
    label: str
    direction: str       # "bull" | "bear" | "sideways"
    scale: str
    root: str
    bpm: int
    start_s: float
    end_s: float
    start_tick: int
    end_tick: int
    start_idx: int
    end_idx: int


def compute_timeline(
    rendered_movements: list[RenderedMovement] | None,
    df: pd.DataFrame,
    base_config: RunConfig,
) -> tuple[list[TimelineCandle], list[TimelineMovement]]:
    """Compute per-candle and per-movement start times in the audio
    timeline, honouring per-movement tempo changes and inter-movement
    rests. For plain mode (no symphony) we fabricate a single virtual
    movement covering the whole DataFrame at the base tempo."""
    candle_ticks = base_config.candle_ticks
    grace_ticks = base_config.grace_ticks
    ppq = base_config.ppq
    rest_ticks = ppq  # one beat between movements

    if not rendered_movements:
        # Plain mode: synthesize a single virtual movement.
        sec_per_tick = 60.0 / base_config.bpm / ppq
        candles: list[TimelineCandle] = []
        for i in range(len(df)):
            candle_tick = grace_ticks + i * candle_ticks
            candles.append(TimelineCandle(
                idx=i,
                start_s=candle_tick * sec_per_tick,
                duration_s=candle_ticks * sec_per_tick,
                start_tick=candle_tick,
                movement=0,
            ))
        last_end_tick = grace_ticks + len(df) * candle_ticks
        movements = [TimelineMovement(
            index=0,
            label=f"{base_config.scale.capitalize()} {base_config.root}",
            direction="sideways",
            scale=base_config.scale,
            root=base_config.root,
            bpm=base_config.bpm,
            start_s=0.0,
            end_s=last_end_tick * sec_per_tick,
            start_tick=0,
            end_tick=last_end_tick,
            start_idx=0,
            end_idx=len(df) - 1,
        )]
        return candles, movements

    candles = []
    movements = []
    seconds_so_far = 0.0

    for r in rendered_movements:
        bpm = r.config.bpm
        sec_per_tick = 60.0 / bpm / ppq
        movement_start_s = seconds_so_far + grace_ticks * sec_per_tick
        movement_start_tick = r.tick_offset + grace_ticks
        n = r.movement.end_idx - r.movement.start_idx + 1

        for k in range(n):
            candles.append(TimelineCandle(
                idx=r.movement.start_idx + k,
                start_s=movement_start_s + k * candle_ticks * sec_per_tick,
                duration_s=candle_ticks * sec_per_tick,
                start_tick=movement_start_tick + k * candle_ticks,
                movement=r.movement.index,
            ))

        movement_end_s = movement_start_s + n * candle_ticks * sec_per_tick
        movement_end_tick = movement_start_tick + n * candle_ticks
        movements.append(TimelineMovement(
            index=r.movement.index,
            label=r.movement.label,
            direction=r.movement.direction,
            scale=r.config.scale,
            root=r.config.root,
            bpm=r.config.bpm,
            start_s=seconds_so_far,
            end_s=movement_end_s,
            start_tick=r.tick_offset,
            end_tick=movement_end_tick,
            start_idx=r.movement.start_idx,
            end_idx=r.movement.end_idx,
        ))

        # Movement contributes pad + content + rest, all at its own tempo.
        seconds_so_far = movement_end_s + rest_ticks * sec_per_tick

    # De-duplicate candles at movement boundaries (the last candle of
    # one movement is the same source-row as the first of the next).
    # Keep the FIRST occurrence so the timeline lines up with the
    # movement that "owns" the bar narratively.
    seen: set[int] = set()
    deduped: list[TimelineCandle] = []
    for c in candles:
        if c.idx in seen:
            continue
        seen.add(c.idx)
        deduped.append(c)
    return deduped, movements


__all__ = [
    "TimelineCandle",
    "TimelineMovement",
    "compute_timeline",
]
