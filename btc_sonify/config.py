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
from typing import Literal

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

    Bundles the channel programs (melody, harmony, optional bass + voice,
    drum kit) so a single ``--palette synthwave`` flag swaps the whole
    arrangement coherently. Bass and voice are optional — classical
    doesn't need bass, but voice (a sustained 'lead' choir line floating
    above the synth arp) is what most modern palettes are missing
    when they feel "instrumental but no lead vocal".

    ``rubato_default`` controls whether within-movement tempo breathing
    is on by default for this palette. Romantic / orchestral palettes
    (classical, cinematic) want rubato; grid-locked genres (synthwave,
    electronic) don't — but the CLI flag overrides either way.
    """
    name: str
    melody_program: int
    harmony_program: int
    drum_program: int = DRUM_KIT_STANDARD
    bass_program: int | None = None
    voice_program: int | None = None
    rubato_default: bool = True

    # Genre-specific RunConfig overrides — None = use RunConfig default.
    # Each palette is a coherent style statement: not just instrument
    # programs, but humanization, dynamic range, articulation, drum
    # density, rubato intensity, and harmony rhythm. These are typed
    # explicitly (no dict[str, Any]) so a typo on a Palette field fails
    # loudly at construction via dataclasses.replace() validation.
    humanize: bool | None = None
    velocity_min: int | None = None
    velocity_max: int | None = None
    velocity_jitter_range: int | None = None
    timing_jitter_ticks: int | None = None
    legato_fraction: float | None = None
    normal_fraction: float | None = None
    marcato_fraction: float | None = None
    staccato_fraction: float | None = None
    marcato_velocity_bonus: int | None = None
    rubato_min_factor: float | None = None
    rubato_max_factor: float | None = None
    drum_volume_decile: float | None = None
    drum_range_decile: float | None = None
    drum_velocity_factor: float | None = None
    hi_hat_velocity_factor: float | None = None
    crash_velocity_factor: float | None = None
    harmony_rhythm: Literal["sustained", "arp_up", "arp_down"] | None = None


# Tuple of RunConfig field names that Palette is allowed to override.
# replace() validates field names so a typo on a Palette field fails
# loudly during palette construction rather than silently no-op'ing.
OVERRIDABLE: tuple[str, ...] = (
    "humanize",
    "velocity_min",
    "velocity_max",
    "velocity_jitter_range",
    "timing_jitter_ticks",
    "legato_fraction",
    "normal_fraction",
    "marcato_fraction",
    "staccato_fraction",
    "marcato_velocity_bonus",
    "rubato_min_factor",
    "rubato_max_factor",
    "drum_volume_decile",
    "drum_range_decile",
    "drum_velocity_factor",
    "hi_hat_velocity_factor",
    "crash_velocity_factor",
    "harmony_rhythm",
)


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
        voice_program=52,    # Choir Aahs — orchestral choir
    ),
    "synthwave": Palette(
        name="synthwave",
        melody_program=81,   # Lead 2 (sawtooth) — the classic synth lead
        harmony_program=89,  # Pad 2 (warm) — analog poly pad
        drum_program=DRUM_KIT_ELECTRONIC,
        bass_program=38,     # Synth Bass 1 — punchy mono bass
        voice_program=53,    # Voice Oohs — synthy choir, 80s vibe
        rubato_default=False,  # genre wants grid-locked tempo
        # Synthwave: pad arpeggiates upward — chord becomes sequenced
        # motion instead of a held wash. The genre lives on arpeggios.
        harmony_rhythm="arp_up",
    ),
    "cinematic": Palette(
        name="cinematic",
        melody_program=62,   # Synth Brass 1 — big stabs
        harmony_program=95,  # Pad 8 (sweep) — film-score sweep
        drum_program=DRUM_KIT_POWER,
        bass_program=39,     # Synth Bass 2 — fat sub
        voice_program=91,    # Pad 4 (choir) — film score choir pad
        # Cinematic — film-score performance character.
        # Wide dynamic range, audible performer wobble, dramatic rubato,
        # tight kit. velocity_min=28 deliberately breaks CLAUDE.md's
        # global velocity_min=40 floor (owner-approved) so quiet
        # passages can drop to true pianissimo before swelling.
        humanize=True,
        velocity_min=28,
        velocity_max=122,
        velocity_jitter_range=10,
        timing_jitter_ticks=5,
        legato_fraction=1.02,
        normal_fraction=0.88,
        marcato_fraction=0.55,
        staccato_fraction=0.32,
        marcato_velocity_bonus=8,
        rubato_min_factor=0.55,
        rubato_max_factor=1.15,
        drum_volume_decile=0.94,
        drum_range_decile=0.92,
        drum_velocity_factor=0.55,
        hi_hat_velocity_factor=0.18,
        crash_velocity_factor=0.55,
        harmony_rhythm="sustained",  # film score, not arp
    ),
    "electronic": Palette(
        name="electronic",
        melody_program=4,    # Electric Piano 1 — Rhodes
        harmony_program=92,  # Pad 5 (bowed)
        drum_program=DRUM_KIT_TR_808,
        bass_program=38,     # Synth Bass 1
        voice_program=54,    # Synth Voice — chopped electronic vocal
        rubato_default=False,  # genre wants grid-locked tempo
        # Electronic: pad arpeggiates downward — descending sequence,
        # complementary motion to synthwave's upward arp.
        harmony_rhythm="arp_down",
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

    # Within-candle melodic motion. For non-doji candles we now play a
    # short phrase across each candle slot — open→close, or full
    # open→low→high→close traversal on wide-range bars — instead of
    # one static note on the close. This is the single biggest "soul"
    # win: the melody actually moves with the price action, instead of
    # sitting on the destination.
    range_modest_factor: float = 0.3   # range < median*this → 1 note (no motion)

    # Humanization. Real performers are slightly imperfect on purpose.
    # Velocity jitter and micro-timing wobble are deterministic functions
    # of the candle index so determinism (same input → same MIDI) is
    # preserved.
    humanize: bool = True
    velocity_jitter_range: int = 8     # ±N velocity per note
    timing_jitter_ticks: int = 3       # ±N ticks per note (subtle ahead/behind)

    # Rest insertion for phrasing. Candles in the bottom N percentile of
    # volume become silent in the melody — the harmony pad and drums
    # carry through, but the lead "breathes". This is what makes the
    # piece feel composed instead of mechanical.
    rest_volume_percentile: float = 0.10

    # Channels and instruments
    melody_channel: int = 0
    harmony_channel: int = 1
    bass_channel: int = 2
    voice_channel: int = 3
    melody_program: int = GM_ACOUSTIC_GRAND
    harmony_program: int = GM_STRING_ENSEMBLE_1
    drum_program: int = 0                # 0 = standard GM kit
    bass_program: int | None = None      # None = no bass track
    voice_program: int | None = None     # None = no voice/lead track
    harmony_velocity_factor: float = 0.6
    bass_velocity_factor: float = 0.7
    bass_octave_shift: int = -1          # one octave below the melody close note

    # Harmony rhythm — how the chord is laid out in time per candle.
    #   "sustained": one note per chord pitch covering the full candle slot
    #                (the v1 / film-score behaviour).
    #   "arp_up":    chord arpeggiated upward in 4 fixed positions per candle.
    #   "arp_down":  chord arpeggiated downward in 4 fixed positions per candle.
    # Subdivision count (4) and per-position velocity contour are fixed in
    # mapping.py — promoting them to config would let users break the
    # grid-stable bar geometry that downstream consumers rely on.
    harmony_rhythm: Literal["sustained", "arp_up", "arp_down"] = "sustained"

    # Percussion thresholds and velocity factors. Lifted out of
    # percussion.py so palettes can tune drum density / mix balance per
    # genre. Defaults preserve the pre-lift behaviour exactly.
    drum_volume_decile: float = 0.90      # candles above this volume quantile fire a kick
    drum_range_decile: float = 0.90       # candles above this range quantile fire a snare
    drum_velocity_factor: float = 0.50    # kick/snare/ride at this fraction of velocity_max
    hi_hat_velocity_factor: float = 0.30  # hi-hat (the heartbeat) sits underneath
    crash_velocity_factor: float = 0.65   # crash slightly louder for movement transitions

    # Voice (the lead "vocal" line floating above the synth arp).
    # Plays a sustained note every voice_note_length_candles candles,
    # pitched at a smoothed close price quantized to scale and shifted
    # voice_octave_shift octaves above the melody.
    voice_velocity_factor: float = 0.7
    voice_smoothing_window: int = 5      # rolling-mean window for the macro contour
    voice_note_length_candles: int = 4   # one held note covers N candles
    voice_octave_shift: int = 1          # +1 octave above melody (cap MIDI 96)

    # Rubato (within-movement tempo breathing).
    #
    # In real performance a pianist *takes time* into climaxes, *holds* at
    # turning points, and *pushes through* trending passages. Symphony
    # mode currently emits one tempo per movement (a step function across
    # months). Rubato adds a smoothed tempo curve within each movement so
    # the piece breathes — rallentando into swing pivots and vol regime
    # shifts, accelerando through trends, suspension at the climax.
    #
    # Implementation note: rubato modulates *real-time playback speed*
    # via meta-track set_tempo events. It does NOT change per-candle tick
    # counts — the audit invariant (1 candle = config.candle_ticks ticks)
    # is fully preserved. Same OHLCV + same config still produces a
    # byte-identical MIDI.
    rubato: bool = True
    rubato_min_factor: float = 0.65        # slowest = 65% of movement BPM
    rubato_max_factor: float = 1.20        # fastest = 120% of movement BPM
    rubato_smoothing_window: int = 5       # rolling-mean window over the per-candle factor
    rubato_approach_window: int = 6        # candles of rallentando lead-in to a structural event
    rubato_quantize_step: int = 4          # BPM bucket size — keeps the meta track readable

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
        """Return a copy of this config with all five channel programs
        set from the palette, rubato switched to the palette's default,
        and any non-None palette overrides applied to the matching
        RunConfig fields.

        The palette is the genre statement: it decides not just which
        instruments play but *how* they play — humanization, dynamic
        range, articulation gates, drum density, rubato breadth, and
        harmony rhythm. Knobs the palette doesn't pin keep their
        RunConfig defaults, so a partially-tuned palette is fine.

        ``replace()`` validates field names at runtime, so a typo on a
        Palette override field surfaces here as a clear TypeError rather
        than silently no-op'ing.

        Use ``with_palette(...).with_rubato(True/False)`` if the user
        supplied an explicit ``--rubato`` / ``--no-rubato`` on the CLI.
        """
        overrides = {
            field: getattr(palette, field)
            for field in OVERRIDABLE
            if getattr(palette, field) is not None
        }
        return replace(
            self,
            melody_program=palette.melody_program,
            harmony_program=palette.harmony_program,
            drum_program=palette.drum_program,
            bass_program=palette.bass_program,
            voice_program=palette.voice_program,
            rubato=palette.rubato_default,
            **overrides,
        )

    def with_rubato(self, enabled: bool) -> RunConfig:
        """Return a copy with rubato explicitly toggled. Used by the CLI
        when the user passes ``--rubato`` / ``--no-rubato`` to override
        the palette default."""
        return replace(self, rubato=enabled)
