"""Scale definitions and pitch quantization.

The mapping from a normalized close-price value (0..1) to a MIDI pitch is
the foundation of the sonification. We pick a musical scale (e.g. phrygian),
build the full ladder of notes that scale produces over N octaves above the
root, and snap the input value to its nearest rung. Every other module in
the project depends on this being correct, so the public surface here is
small, pure, and fully unit-tested.

Why these specific scales? They are the ones requested in CLAUDE.md and
between them they cover most of the emotional palette useful for
sonifying market data — bright (major, pentatonic_major), dark (minor,
phrygian), modal-mysterious (dorian, hijaz), and bluesy.
"""
from __future__ import annotations

# Scale degrees expressed as semitone offsets from the root.
# Each tuple is one ascending octave; build_scale_notes() stacks octaves
# and appends the final octave-up root so the range bookends cleanly.
SCALES: dict[str, tuple[int, ...]] = {
    "major":            (0, 2, 4, 5, 7, 9, 11),
    "minor":            (0, 2, 3, 5, 7, 8, 10),  # natural minor
    "pentatonic_major": (0, 2, 4, 7, 9),
    "pentatonic_minor": (0, 3, 5, 7, 10),
    "dorian":           (0, 2, 3, 5, 7, 9, 10),
    "phrygian":         (0, 1, 3, 5, 7, 8, 10),
    "hijaz":            (0, 1, 4, 5, 7, 8, 11),  # Arabic / Middle Eastern
    "blues":            (0, 3, 5, 6, 7, 10),
}

# Note name → semitone offset within an octave. Both sharp and flat
# enharmonic spellings are accepted so a user can write either "Bb" or "A#".
NOTE_TO_SEMITONE: dict[str, int] = {
    "C": 0, "C#": 1, "Db": 1,
    "D": 2, "D#": 3, "Eb": 3,
    "E": 4, "Fb": 4,
    "F": 5, "E#": 5, "F#": 6, "Gb": 6,
    "G": 7, "G#": 8, "Ab": 8,
    "A": 9, "A#": 10, "Bb": 10,
    "B": 11, "Cb": 11,
}


def note_name_to_midi(name: str, octave: int = 3) -> int:
    """Convert a note name like 'A' or 'F#' to a MIDI number.

    MIDI uses the convention C-1 = 0, so middle C (C4) = 60 and
    A3 = 57 — which is the project's default root per CLAUDE.md.
    """
    key = name.strip()
    # Normalize so 'a', 'A', 'a#' all work.
    if len(key) >= 1:
        key = key[0].upper() + key[1:]
    if key not in NOTE_TO_SEMITONE:
        raise ValueError(
            f"Unknown note name {name!r}. "
            f"Expected one of: {sorted(set(NOTE_TO_SEMITONE))}."
        )
    return 12 * (octave + 1) + NOTE_TO_SEMITONE[key]


def build_scale_notes(
    scale_name: str, root_midi: int, octaves: int
) -> list[int]:
    """Return the sorted list of MIDI notes spanning `octaves` octaves
    above `root_midi` in the named scale.

    The list always begins at the root and ends at the root + 12*octaves
    (the octave-up of the topmost octave). For a 1-octave major scale
    rooted at C4 (60) this yields [60, 62, 64, 65, 67, 69, 71, 72] —
    eight rungs, exactly one octave from bottom to top.
    """
    if scale_name not in SCALES:
        raise ValueError(
            f"Unknown scale {scale_name!r}. "
            f"Expected one of: {sorted(SCALES)}."
        )
    if octaves < 1:
        raise ValueError(f"octaves must be >= 1, got {octaves}.")

    offsets = SCALES[scale_name]
    notes: list[int] = []
    for o in range(octaves):
        for off in offsets:
            notes.append(root_midi + 12 * o + off)
    notes.append(root_midi + 12 * octaves)  # octave-up root caps the range
    return notes


def quantize_to_scale(
    value_0_to_1: float,
    scale_name: str,
    root_midi: int,
    octaves: int,
) -> int:
    """Snap a normalized value in [0, 1] to a MIDI note in the chosen scale.

    A value of 0 returns the lowest note (the root); a value of 1 returns
    the highest (the octave-up root of the topmost octave). Values in
    between are mapped linearly to the nearest scale degree by index, so
    the macro shape of the input series is preserved as a recognisable
    contour rather than being smoothed by interpolation.

    Values outside [0, 1] are clamped — defensive only; callers should be
    normalizing upstream.
    """
    notes = build_scale_notes(scale_name, root_midi, octaves)
    clamped = max(0.0, min(1.0, value_0_to_1))
    # round() gives even distribution across rungs; len(notes) - 1 is the
    # max index so 1.0 maps exactly to the top note.
    idx = round(clamped * (len(notes) - 1))
    return notes[idx]


def scale_step(midi_note: int, scale_notes: list[int], steps: int) -> int:
    """Return the note `steps` scale-degrees away from `midi_note` within
    the given scale-notes ladder. Clamps at the boundaries.

    Used for ornamentation in the mapping layer: grace notes are one
    scale-step above or below the close note, and the doji trill
    alternates the close note with the note one step above.

    If `midi_note` is not exactly on the ladder (which shouldn't happen
    when the caller derived it from quantize_to_scale, but we don't trust
    that), we snap to the nearest ladder note before stepping. Ties go
    downward, which is arbitrary but deterministic.
    """
    if midi_note in scale_notes:
        idx = scale_notes.index(midi_note)
    else:
        idx = min(
            range(len(scale_notes)),
            key=lambda i: (abs(scale_notes[i] - midi_note), scale_notes[i]),
        )
    new_idx = max(0, min(len(scale_notes) - 1, idx + steps))
    return scale_notes[new_idx]
