"""Run configuration dataclass.

A single frozen dataclass captures every knob in the sonification so the
CLI, the mapping engine, and the MIDI writer all read from the same
source of truth. Defaults match CLAUDE.md exactly: phrygian / A3 / 3
octaves / 120 BPM / quarter note per candle.

The thresholds (body_ratio for articulation, wick multiplier for grace
notes, velocity floor) are exposed as fields rather than module-level
constants because CLAUDE.md explicitly asks for each axis to be tunable.
"""
from __future__ import annotations

from dataclasses import dataclass

# General MIDI program numbers used as defaults — Acoustic Grand for
# melody and String Ensemble 1 for harmony, per CLAUDE.md.
GM_ACOUSTIC_GRAND = 0
GM_STRING_ENSEMBLE_1 = 48


@dataclass(frozen=True)
class RunConfig:
    """All knobs for a single sonification run."""

    # Pitch
    scale: str = "phrygian"
    root: str = "A"
    root_octave: int = 3       # A3 = MIDI 57
    octaves: int = 3

    # Tempo / timing
    bpm: int = 120
    note_value: str = "quarter"  # quarter | eighth | half — duration of one candle
    ppq: int = 480               # MIDI standard pulses per quarter note

    # Velocity (loudness)
    velocity_min: int = 40       # CLAUDE.md: never below 40 — silence isn't musical
    velocity_max: int = 127

    # Articulation thresholds (body_ratio = body_size / range)
    body_ratio_strong: float = 0.7
    body_ratio_doji: float = 0.1

    # Articulation duration as fraction of the candle's slot
    legato_fraction: float = 1.0    # green, body_ratio > 0.7
    normal_fraction: float = 0.8    # green, body_ratio <= 0.7
    marcato_fraction: float = 0.6   # red,   body_ratio > 0.7  (+10 velocity)
    staccato_fraction: float = 0.4  # red,   body_ratio <= 0.7
    marcato_velocity_bonus: int = 10

    # Trill (doji): how many alternating notes inside one candle slot.
    # 4 = close, up, close, up — one ornament across the bar.
    trill_subdivisions: int = 4

    # Ornamentation
    wick_grace_multiplier: float = 2.0  # wick > 2 * body_size triggers grace

    # Channels and instruments
    melody_channel: int = 0
    harmony_channel: int = 1
    melody_program: int = GM_ACOUSTIC_GRAND
    harmony_program: int = GM_STRING_ENSEMBLE_1
    harmony_velocity_factor: float = 0.6

    @property
    def candle_ticks(self) -> int:
        """Number of MIDI ticks one candle occupies, per note_value."""
        if self.note_value == "quarter":
            return self.ppq
        if self.note_value == "eighth":
            return self.ppq // 2
        if self.note_value == "half":
            return self.ppq * 2
        raise ValueError(
            f"Unsupported note_value {self.note_value!r}. "
            f"Expected 'quarter', 'eighth', or 'half'."
        )

    @property
    def grace_ticks(self) -> int:
        """Duration (and lead-in offset) of a grace note: 1/4 of a candle slot."""
        return max(1, self.candle_ticks // 4)
