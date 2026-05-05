"""MIDI file writer.

Takes a list of MidiEvents and serialises to a Type-1 Standard MIDI File
with three tracks: a meta track carrying tempo, a melody track on the
melody channel, and a harmony track on the harmony channel. Splitting
the parts into separate tracks keeps the file legible when opened in a
DAW (each track shows up as its own lane) without changing the audible
result — the channel routing on each note is what the synth actually uses.

Note ordering matters: at any given tick we emit note_off events before
note_on events of the same pitch so back-to-back notes on the same key
don't accidentally cancel themselves with overlapping note-ons.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

import mido

from btc_sonify.config import RunConfig
from btc_sonify.mapping import MidiEvent


def _track_for_channel(
    events: Iterable[MidiEvent],
    channel: int,
    program: int,
) -> mido.MidiTrack:
    """Build a MidiTrack for one channel, with the right program change up
    front and delta-timed note_on/note_off pairs for each event."""
    track = mido.MidiTrack()
    track.append(mido.Message(
        "program_change", program=program, channel=channel, time=0,
    ))

    # Expand each event into an absolute-tick on/off pair.
    abs_events: list[tuple[int, int, MidiEvent]] = []
    for e in events:
        if e.channel != channel:
            continue
        abs_events.append((e.start_tick, 1, e))                          # on
        abs_events.append((e.start_tick + e.duration_ticks, 0, e))       # off

    # Sort by tick, then off-before-on at the same tick so a note that
    # ends exactly when another begins releases the key cleanly.
    abs_events.sort(key=lambda x: (x[0], x[1]))

    last_tick = 0
    for tick, kind, e in abs_events:
        delta = tick - last_tick
        last_tick = tick
        if kind == 1:  # on
            track.append(mido.Message(
                "note_on",
                note=e.note,
                velocity=e.velocity,
                channel=e.channel,
                time=delta,
            ))
        else:  # off
            track.append(mido.Message(
                "note_off",
                note=e.note,
                velocity=0,
                channel=e.channel,
                time=delta,
            ))
    return track


def write_midi(
    events: list[MidiEvent],
    path: Path,
    config: RunConfig,
) -> None:
    """Write the event list to a .mid file at `path`.

    The output is a Type-1 SMF with three tracks (meta, melody, harmony).
    The directory containing `path` is created if it doesn't exist.
    Tempo is set once at tick 0 from `config.bpm`.
    """
    mid = mido.MidiFile(type=1, ticks_per_beat=config.ppq)

    # Track 0: tempo / meta
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage(
        "set_tempo", tempo=mido.bpm2tempo(config.bpm), time=0,
    ))
    mid.tracks.append(meta)

    # Track 1: melody
    mid.tracks.append(_track_for_channel(
        events, config.melody_channel, config.melody_program,
    ))

    # Track 2: harmony
    mid.tracks.append(_track_for_channel(
        events, config.harmony_channel, config.harmony_program,
    ))

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mid.save(str(path))
