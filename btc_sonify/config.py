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

from dataclasses import dataclass, replace

# General MIDI program numbers used as defaults — Acoustic Grand for
# melody and String Ensemble 1 for harmony, per CLAUDE.md.
GM_ACOUSTIC_GRAND = 0
GM_STRING_ENSEMBLE_1 = 48

# GM standard drum channel (zero-indexed; many DAWs display it as channel 10).
GM_DRUM_CHANNEL = 9

# GM drum kit MIDI note numbers used by the percussion track.
DRUM_KICK = 36
DRUM_SNARE = 38
DRUM_HI_HAT_CLOSED = 42
DRUM_CRASH = 49
DRUM_RIDE_BELL = 53

# GS-extended drum kit selectors (program changes on channel 9). Logic
# and most modern synths interpret these. On strict-GM synths they fall
# back to the Standard Kit, which is fine — sound stays musical.
DRUM_KIT_STANDARD = 0
DRUM_KIT_POWER = 16
DRUM_KIT_ELECTRONIC = 24
DRUM_KIT_TR_808 = 25


@dataclass(frozen=True)
class Palette:
    """Instrument palette for a sonification run.

    Bundles the four channel programs (melody, harmony, optional bass,
    drum kit) so a single ``--palette synthwave`` flag swaps the whole
    arrangement coherently. Bass is optional — classical doesn't need
    it, but every modern palette does.
    """
    name: str
    melody_program: int
    harmony_program: int
    drum_program: int = DRUM_KIT_STANDARD
    bass_program: int | None = None


# The four palettes. Program numbers are GM/GS standard and pick out
# instruments that hold up across most synths; on Logic / pro DAWs they
# also serve as a reasonable starting point before you swap in the
# real plug-ins.
PALETTES: dict[str, Palette] = {
    "classical": Palette(
        name="classical",
        melody_program=0,    # Acoustic Grand Piano
        harmony_program=48,  # String Ensemble 1
        drum_program=DRUM_KIT_STANDARD,
        bass_program=None,
    ),
    "synthwave": Palette(
        name="synthwave",
        melody_program=81,   # Lead 2 (sawtooth) — the classic synth lead
        harmony_program=89,  # Pad 2 (warm) — analog poly pad
        drum_program=DRUM_KIT_ELECTRONIC,
        bass_program=38,     # Synth Bass 1 — punchy mono bass
    ),
    "cinematic": Palette(
        name="cinematic",
        melody_program=62,   # Synth Brass 1 — big stabs
        harmony_program=95,  # Pad 8 (sweep) — film-score sweep
        drum_program=DRUM_KIT_POWER,
        bass_program=39,     # Synth Bass 2 — fat sub
    ),
    "electronic": Palette(
        name="electronic",
        melody_program=4,    # Electric Piano 1 — Rhodes
        harmony_program=92,  # Pad 5 (bowed)
        drum_program=DRUM_KIT_TR_808,
        bass_program=38,     # Synth Bass 1
    ),
}


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
    bass_channel: int = 2
    melody_program: int = GM_ACOUSTIC_GRAND
    harmony_program: int = GM_STRING_ENSEMBLE_1
    drum_program: int = 0                # 0 = standard GM kit
    bass_program: int | None = None      # None = no bass track
    harmony_velocity_factor: float = 0.6
    bass_velocity_factor: float = 0.7
    bass_octave_shift: int = -1          # one octave below the melody close note

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

    def with_palette(self, palette: Palette) -> RunConfig:
        """Return a copy of this config with all four channel programs
        set from the palette."""
        return replace(
            self,
            melody_program=palette.melody_program,
            harmony_program=palette.harmony_program,
            drum_program=palette.drum_program,
            bass_program=palette.bass_program,
        )
