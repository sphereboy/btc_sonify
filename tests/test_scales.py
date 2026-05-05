"""Unit tests for scales.py — the math foundation.

The mapping layer assumes these primitives are correct, so we test
exhaustively: every scale, every note name, the boundaries of
quantize_to_scale, and the clamping behavior of scale_step.
"""
from __future__ import annotations

import pytest

from btc_sonify.scales import (
    NOTE_TO_SEMITONE,
    SCALES,
    build_scale_notes,
    note_name_to_midi,
    quantize_to_scale,
    scale_step,
)


# --- SCALES dict --------------------------------------------------------

def test_all_eight_scales_present():
    expected = {
        "major", "minor", "pentatonic_major", "pentatonic_minor",
        "dorian", "phrygian", "hijaz", "blues",
    }
    assert set(SCALES) == expected


@pytest.mark.parametrize(
    "name, expected",
    [
        ("major",            (0, 2, 4, 5, 7, 9, 11)),
        ("minor",            (0, 2, 3, 5, 7, 8, 10)),
        ("pentatonic_major", (0, 2, 4, 7, 9)),
        ("pentatonic_minor", (0, 3, 5, 7, 10)),
        ("dorian",           (0, 2, 3, 5, 7, 9, 10)),
        ("phrygian",         (0, 1, 3, 5, 7, 8, 10)),
        ("hijaz",            (0, 1, 4, 5, 7, 8, 11)),
        ("blues",            (0, 3, 5, 6, 7, 10)),
    ],
)
def test_scale_offsets_match_spec(name, expected):
    """Offsets must match CLAUDE.md exactly — these are the canonical refs."""
    assert SCALES[name] == expected


@pytest.mark.parametrize("name", list(SCALES))
def test_every_scale_starts_on_root(name):
    assert SCALES[name][0] == 0


@pytest.mark.parametrize("name", list(SCALES))
def test_every_scale_strictly_ascending_within_octave(name):
    offsets = SCALES[name]
    assert all(a < b for a, b in zip(offsets, offsets[1:]))
    assert all(0 <= o < 12 for o in offsets)


# --- note_name_to_midi --------------------------------------------------

def test_a3_is_midi_57():
    """The default root specified in CLAUDE.md."""
    assert note_name_to_midi("A", 3) == 57


def test_middle_c_is_midi_60():
    assert note_name_to_midi("C", 4) == 60


@pytest.mark.parametrize(
    "name, octave, expected",
    [
        ("C", -1, 0),     # MIDI floor
        ("C", 0, 12),
        ("C", 4, 60),     # middle C
        ("A", 4, 69),     # A440
        ("G", 9, 127),    # MIDI ceiling
    ],
)
def test_known_midi_anchors(name, octave, expected):
    assert note_name_to_midi(name, octave) == expected


def test_sharp_and_flat_enharmonics_match():
    assert note_name_to_midi("A#", 3) == note_name_to_midi("Bb", 3)
    assert note_name_to_midi("C#", 4) == note_name_to_midi("Db", 4)
    assert note_name_to_midi("F#", 4) == note_name_to_midi("Gb", 4)


def test_lowercase_input_is_normalized():
    assert note_name_to_midi("a", 3) == 57
    assert note_name_to_midi("bb", 3) == note_name_to_midi("Bb", 3)


def test_unknown_note_raises():
    with pytest.raises(ValueError, match="Unknown note"):
        note_name_to_midi("H", 3)


def test_every_documented_note_resolves():
    """Sanity check: nothing in the alias table is mis-keyed.

    At octave=4 the result lives in the C4..B4 window (60..71); enharmonic
    spellings like Cb/B# fall just outside that window by design (the
    table treats the letter positionally, not theoretically).
    """
    for name in NOTE_TO_SEMITONE:
        midi = note_name_to_midi(name, 4)
        assert 0 <= midi <= 127
        assert 60 <= midi <= 71  # all aliases stay inside the named octave


# --- build_scale_notes --------------------------------------------------

def test_one_octave_major_at_c4():
    """Eight rungs spanning exactly one octave: C4 to C5."""
    notes = build_scale_notes("major", 60, 1)
    assert notes == [60, 62, 64, 65, 67, 69, 71, 72]


def test_three_octave_major_at_c4_spans_three_octaves():
    notes = build_scale_notes("major", 60, 3)
    assert notes[0] == 60
    assert notes[-1] == 60 + 36  # exactly 3 octaves up
    # 7 scale degrees * 3 octaves + the octave-up cap = 22 notes
    assert len(notes) == 22


def test_three_octave_phrygian_at_a3():
    """The project's defaults — verify no off-by-one."""
    notes = build_scale_notes("phrygian", 57, 3)
    assert notes[0] == 57
    assert notes[-1] == 57 + 36
    assert len(notes) == 7 * 3 + 1


def test_pentatonic_minor_has_fewer_notes_per_octave():
    notes = build_scale_notes("pentatonic_minor", 60, 2)
    # 5 degrees * 2 octaves + cap = 11
    assert len(notes) == 11


def test_notes_strictly_ascending():
    for name in SCALES:
        notes = build_scale_notes(name, 60, 3)
        assert notes == sorted(notes)
        assert all(a < b for a, b in zip(notes, notes[1:]))


def test_unknown_scale_raises():
    with pytest.raises(ValueError, match="Unknown scale"):
        build_scale_notes("klingon", 60, 1)


def test_zero_octaves_raises():
    with pytest.raises(ValueError, match="octaves must be >= 1"):
        build_scale_notes("major", 60, 0)


# --- quantize_to_scale --------------------------------------------------

def test_quantize_zero_returns_root():
    assert quantize_to_scale(0.0, "phrygian", 57, 3) == 57


def test_quantize_one_returns_top_note():
    """Highest input must hit the highest rung — required for the
    'macro shape audible' guarantee in CLAUDE.md."""
    notes = build_scale_notes("phrygian", 57, 3)
    assert quantize_to_scale(1.0, "phrygian", 57, 3) == notes[-1]


def test_quantize_half_lands_near_middle():
    notes = build_scale_notes("major", 60, 2)
    mid = quantize_to_scale(0.5, "major", 60, 2)
    middle_idx = notes.index(mid)
    # Should be within one rung of the geometric middle
    assert abs(middle_idx - (len(notes) - 1) / 2) <= 0.5


def test_quantize_clamps_out_of_range_inputs():
    """Defensive: callers should normalize, but if they don't we shouldn't crash."""
    notes = build_scale_notes("major", 60, 2)
    assert quantize_to_scale(-0.5, "major", 60, 2) == notes[0]
    assert quantize_to_scale(2.0, "major", 60, 2) == notes[-1]


def test_quantize_is_deterministic():
    """Same input must always produce same output — the project's
    'deterministic and auditable' contract from CLAUDE.md."""
    args = (0.37, "phrygian", 57, 3)
    runs = [quantize_to_scale(*args) for _ in range(100)]
    assert len(set(runs)) == 1


def test_quantize_output_is_always_in_scale():
    """Property check: 50 evenly-spaced inputs all land on the ladder."""
    notes = set(build_scale_notes("phrygian", 57, 3))
    for i in range(50):
        v = i / 49
        assert quantize_to_scale(v, "phrygian", 57, 3) in notes


def test_quantize_monotonically_non_decreasing():
    """As input rises, output must never fall — preserves contour."""
    prev = -1
    for i in range(101):
        out = quantize_to_scale(i / 100, "major", 60, 3)
        assert out >= prev
        prev = out


# --- scale_step ---------------------------------------------------------

def test_scale_step_zero_returns_same_note():
    notes = build_scale_notes("major", 60, 1)
    assert scale_step(64, notes, 0) == 64  # E4 stays E4


def test_scale_step_up_one_in_major():
    """E4 + 1 step in C major = F4 (semitone offset 5, not 4+2)."""
    notes = build_scale_notes("major", 60, 1)
    assert scale_step(64, notes, 1) == 65


def test_scale_step_down_one_in_major():
    notes = build_scale_notes("major", 60, 1)
    assert scale_step(65, notes, -1) == 64  # F4 -> E4


def test_scale_step_clamps_at_top():
    notes = build_scale_notes("major", 60, 1)
    top = notes[-1]
    assert scale_step(top, notes, 5) == top


def test_scale_step_clamps_at_bottom():
    notes = build_scale_notes("major", 60, 1)
    bottom = notes[0]
    assert scale_step(bottom, notes, -5) == bottom


def test_scale_step_off_ladder_snaps_to_nearest():
    """If somehow given an out-of-scale note, snap to nearest first."""
    notes = build_scale_notes("major", 60, 1)  # no F# (66) in C major
    # 66 is between F (65) and G (67); both are 1 away. Tie -> lower (65).
    assert scale_step(66, notes, 0) == 65


def test_scale_step_traverses_octave_boundary():
    """Stepping up from B4 (top of one-octave major) should hit C5 (the cap)."""
    notes = build_scale_notes("major", 60, 1)
    assert scale_step(71, notes, 1) == 72  # B4 -> C5
