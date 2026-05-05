"""Sub-bass track for modern palettes.

Doubles the close note one (or more) octaves below the melody on its
own MIDI channel, so a synth bass program can sustain underneath the
melody/harmony layer. Bass is what makes the difference between
"classical-feeling" and "modern-production" — every contemporary genre
relies on a strong low-end fundamental, and the absence of one is half
of why the default palette feels orchestral rather than current.

Implementation mirrors map_percussion: takes the same DataFrame, the
same RunConfig, and an optional list of MovementOffsets so symphony
mode lays the bass out in lockstep with the per-movement timeline.

If ``config.bass_program`` is None the function returns no events —
the classical palette opts out of bass on purpose.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from btc_sonify.config import RunConfig
from btc_sonify.mapping import MidiEvent
from btc_sonify.percussion import MovementOffset
from btc_sonify.scales import note_name_to_midi, quantize_to_scale


def map_bass(
    df: pd.DataFrame,
    config: RunConfig,
    movement_offsets: list[MovementOffset] | None = None,
) -> list[MidiEvent]:
    """Render bass events for the full DataFrame.

    Each candle gets one bass note: the quantized close pitch shifted
    down by ``config.bass_octave_shift`` octaves. The note sustains for
    the full candle slot (pad-style) at ``bass_velocity_factor`` of the
    melody's velocity ceiling.

    Symphony mode: ``movement_offsets`` distributes the bass timeline
    across the per-movement layout, including inter-movement rests, and
    each movement uses its own scale/root via the offset's stored config
    bookkeeping (we re-derive per-movement quantization here so the bass
    follows key changes correctly).
    """
    if df.empty or config.bass_program is None:
        return []

    candle_ticks = config.candle_ticks
    pad = config.grace_ticks
    bass_velocity = max(
        config.velocity_min,
        min(config.velocity_max,
            int(round(config.velocity_max * config.bass_velocity_factor))),
    )
    octave_shift_semitones = 12 * config.bass_octave_shift

    closes = df["close"].to_numpy(dtype=float)

    events: list[MidiEvent] = []

    if movement_offsets is None:
        # Plain mode: one global normalization, single ladder.
        root = note_name_to_midi(config.root, config.root_octave)
        lo, hi = closes.min(), closes.max()
        norm = np.zeros_like(closes) if hi == lo else (closes - lo) / (hi - lo)

        for i, n in enumerate(norm):
            close_note = quantize_to_scale(float(n), config.scale, root, config.octaves)
            bass_note = max(0, min(127, close_note + octave_shift_semitones))
            events.append(MidiEvent(
                channel=config.bass_channel,
                note=bass_note,
                velocity=bass_velocity,
                start_tick=pad + i * candle_ticks,
                duration_ticks=candle_ticks,
            ))
        return events

    # Symphony mode: each movement quantizes against its own scale/root,
    # using the per-movement RunConfig the melody used (carried on the
    # offset). Falls back to the base config when no per-movement config
    # was provided, preserving backwards compatibility.
    for offset in movement_offsets:
        seg_closes = closes[offset.start_idx:offset.end_idx + 1]
        if len(seg_closes) == 0:
            continue
        seg_config = offset.config if offset.config is not None else config
        root = note_name_to_midi(seg_config.root, seg_config.root_octave)
        lo, hi = seg_closes.min(), seg_closes.max()
        norm = (
            np.zeros_like(seg_closes) if hi == lo
            else (seg_closes - lo) / (hi - lo)
        )
        local_pad = offset.tick_offset + pad
        for j, n in enumerate(norm):
            close_note = quantize_to_scale(
                float(n), seg_config.scale, root, seg_config.octaves
            )
            bass_note = max(0, min(127, close_note + octave_shift_semitones))
            events.append(MidiEvent(
                channel=config.bass_channel,
                note=bass_note,
                velocity=bass_velocity,
                start_tick=local_pad + j * candle_ticks,
                duration_ticks=candle_ticks,
            ))
    return events


__all__ = ["map_bass"]
