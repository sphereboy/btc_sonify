"""MIDI file writer.

Takes a list of MidiEvents and serialises to a Type-1 Standard MIDI File
with up to four tracks: a meta track carrying tempo (and optional
movement markers), a melody track, a harmony track, and an optional
percussion track on the GM drum channel.

Splitting the parts into separate tracks keeps the file legible when
opened in a DAW (each track shows up as its own lane) without changing
the audible result — the channel routing on each note is what the synth
actually uses.

Note ordering matters: at any given tick we emit note_off events before
note_on events of the same pitch so back-to-back notes on the same key
don't accidentally cancel themselves with overlapping note-ons.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import mido

from btc_sonify.config import GM_DRUM_CHANNEL, RunConfig
from btc_sonify.mapping import MidiEvent


@dataclass(frozen=True)
class TempoChange:
    """A tempo change at an absolute tick position. Optionally labelled
    so the meta track also carries a movement-name marker."""
    tick: int
    bpm: int
    label: str | None = None


def _ascii_safe(s: str) -> str:
    """MIDI meta strings are encoded as latin-1; anything outside that
    crashes mido.save(). Strip non-encodable characters rather than
    explode at write time."""
    return s.encode("latin-1", errors="replace").decode("latin-1")


def _track_for_channel(
    events: Iterable[MidiEvent],
    channel: int,
    program: int,
    track_name: str | None = None,
) -> mido.MidiTrack:
    """Build a MidiTrack for one channel, with an optional track name,
    a program change up front, and delta-timed note_on/note_off pairs."""
    track = mido.MidiTrack()
    if track_name:
        track.append(mido.MetaMessage("track_name", name=_ascii_safe(track_name), time=0))

    # Drum channel (9) does not take program changes — the kit is implicit.
    if channel != GM_DRUM_CHANNEL:
        track.append(mido.Message(
            "program_change", program=program, channel=channel, time=0,
        ))

    abs_events: list[tuple[int, int, MidiEvent]] = []
    for e in events:
        if e.channel != channel:
            continue
        abs_events.append((e.start_tick, 1, e))                          # on
        abs_events.append((e.start_tick + e.duration_ticks, 0, e))       # off

    abs_events.sort(key=lambda x: (x[0], x[1]))

    last_tick = 0
    for tick, kind, e in abs_events:
        delta = tick - last_tick
        last_tick = tick
        if kind == 1:
            track.append(mido.Message(
                "note_on",
                note=e.note,
                velocity=e.velocity,
                channel=e.channel,
                time=delta,
            ))
        else:
            track.append(mido.Message(
                "note_off",
                note=e.note,
                velocity=0,
                channel=e.channel,
                time=delta,
            ))
    return track


def _build_meta_track(
    config: RunConfig,
    tempo_changes: list[TempoChange] | None,
    title: str | None,
) -> mido.MidiTrack:
    """Build the meta track with tempo changes, optional title, and
    optional movement-name markers."""
    track = mido.MidiTrack()
    if title:
        track.append(mido.MetaMessage("track_name", name=_ascii_safe(title), time=0))

    if not tempo_changes:
        track.append(mido.MetaMessage(
            "set_tempo", tempo=mido.bpm2tempo(config.bpm), time=0,
        ))
        return track

    # Tempos must arrive in tick order; sort defensively.
    sorted_changes = sorted(tempo_changes, key=lambda c: c.tick)
    last_tick = 0
    for change in sorted_changes:
        delta = max(0, change.tick - last_tick)
        last_tick = change.tick
        track.append(mido.MetaMessage(
            "set_tempo", tempo=mido.bpm2tempo(change.bpm), time=delta,
        ))
        if change.label:
            # 'marker' meta messages show up as labelled section markers
            # in most DAWs — perfect for movement names.
            track.append(mido.MetaMessage("marker", text=_ascii_safe(change.label), time=0))
    return track


def write_midi(
    events: list[MidiEvent],
    path: Path,
    config: RunConfig,
    tempo_changes: list[TempoChange] | None = None,
    include_percussion: bool = False,
    title: str | None = None,
) -> None:
    """Write the event list to a .mid file at `path`.

    `tempo_changes` overrides the single-tempo default for symphony mode.
    `include_percussion` adds a fourth track on the GM drum channel —
    only set this when ``events`` actually contains drum-channel events.
    """
    mid = mido.MidiFile(type=1, ticks_per_beat=config.ppq)
    mid.tracks.append(_build_meta_track(config, tempo_changes, title))

    mid.tracks.append(_track_for_channel(
        events, config.melody_channel, config.melody_program,
        track_name="Melody",
    ))
    mid.tracks.append(_track_for_channel(
        events, config.harmony_channel, config.harmony_program,
        track_name="Harmony",
    ))
    if include_percussion:
        mid.tracks.append(_track_for_channel(
            events, GM_DRUM_CHANNEL, program=0,  # program ignored on ch9
            track_name="Percussion",
        ))

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mid.save(str(path))
