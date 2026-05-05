"""Voice track — the sustained "lead vocal" line floating above the synth arp.

Why this matters: the v2 mapping made the melody contour with the price
action inside every candle, which gave it shape — but the result still
lacked a *lead* element, the thing a listener would hum back. In a real
production that's the vocal line: it sustains across multiple bars,
sits in a higher register, and traces the macro shape rather than every
candle's detail.

This module synthesizes that. It takes the close-price series, smooths
it via a rolling-mean window, samples the smoothed value every N
candles, quantizes to scale, shifts up an octave, and emits one
sustained MIDI note per sample point. The note is held for the full N
candles' worth of ticks, so it overlaps the busy melody underneath.

Voice is opt-in via ``config.voice_program`` — palettes set it to a
choir or vocal-style GM program (Choir Aahs, Voice Oohs, Synth Voice,
or a choir pad).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from btc_sonify.config import RunConfig
from btc_sonify.mapping import MidiEvent, _humanize_velocity
from btc_sonify.percussion import MovementOffset
from btc_sonify.scales import note_name_to_midi, quantize_to_scale


def _smoothed_normalized(closes: np.ndarray, window: int) -> np.ndarray:
    """Smooth via centred rolling mean and normalize to [0, 1]."""
    if len(closes) == 0:
        return closes
    s = pd.Series(closes).rolling(window=window, min_periods=1, center=True).mean()
    arr = s.to_numpy()
    lo, hi = float(arr.min()), float(arr.max())
    if hi == lo:
        return np.zeros_like(arr)
    return (arr - lo) / (hi - lo)


def _emit_voice_for_segment(
    closes: np.ndarray,
    base_idx: int,
    pad_start_tick: int,
    config: RunConfig,
    seg_config: RunConfig,
) -> list[MidiEvent]:
    """Emit voice events for a contiguous segment of `closes`. Each note
    covers ``voice_note_length_candles`` candles and is held for that
    many candle slots. The pitch is the smoothed close at the midpoint
    of the note's window, quantized into the segment's scale and
    shifted up by ``voice_octave_shift`` octaves."""
    if len(closes) == 0:
        return []

    smoothed = _smoothed_normalized(closes, config.voice_smoothing_window)
    note_len = max(1, config.voice_note_length_candles)
    candle_ticks = config.candle_ticks
    octave_shift_semitones = 12 * config.voice_octave_shift

    root_midi = note_name_to_midi(seg_config.root, seg_config.root_octave)
    voice_velocity_base = max(
        config.velocity_min,
        min(config.velocity_max,
            int(round(config.velocity_max * config.voice_velocity_factor))),
    )

    events: list[MidiEvent] = []
    n = len(closes)
    k = 0
    while k < n:
        window_end = min(k + note_len, n)
        # Pitch from the midpoint of this voice window for stability —
        # smoothing already happens upstream, mid-window sampling means
        # the held note represents that section's centre, not its edge.
        midpoint = (k + window_end - 1) // 2
        norm = float(smoothed[midpoint])
        pitch = quantize_to_scale(norm, seg_config.scale, root_midi, seg_config.octaves)
        shifted = pitch + octave_shift_semitones
        # Cap at MIDI 96 (C7) — choir/voice patches get squeaky above that.
        shifted = max(0, min(96, shifted))

        v = _humanize_velocity(voice_velocity_base, base_idx + k, salt=200, config=config)
        duration = (window_end - k) * candle_ticks
        events.append(MidiEvent(
            channel=config.voice_channel,
            note=shifted,
            velocity=v,
            start_tick=pad_start_tick + k * candle_ticks,
            duration_ticks=duration,
        ))
        k = window_end
    return events


def map_voice(
    df: pd.DataFrame,
    config: RunConfig,
    movement_offsets: list[MovementOffset] | None = None,
) -> list[MidiEvent]:
    """Render the voice (sustained lead) track for the full DataFrame.

    Returns no events when ``config.voice_program`` is None — palettes
    without a voice (none of the defaults, but a user could clear it)
    skip this layer entirely.

    In symphony mode the voice respects per-movement scale/root via
    ``movement_offsets[i].config``, so the voice modulates with each
    movement's key change.
    """
    if df.empty or config.voice_program is None:
        return []

    closes = df["close"].to_numpy(dtype=float)
    pad = config.grace_ticks

    if movement_offsets is None:
        return _emit_voice_for_segment(
            closes=closes,
            base_idx=0,
            pad_start_tick=pad,
            config=config,
            seg_config=config,
        )

    events: list[MidiEvent] = []
    for offset in movement_offsets:
        seg_closes = closes[offset.start_idx:offset.end_idx + 1]
        if len(seg_closes) == 0:
            continue
        seg_config = offset.config if offset.config is not None else config
        events.extend(_emit_voice_for_segment(
            closes=seg_closes,
            base_idx=offset.start_idx,
            pad_start_tick=offset.tick_offset + pad,
            config=config,
            seg_config=seg_config,
        ))
    return events


__all__ = ["map_voice"]
