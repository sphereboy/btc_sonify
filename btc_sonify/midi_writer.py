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


@dataclass(frozen=True)
class StructuralMarker:
    """A labelled MIDI marker that does *not* change tempo. Used for
    swing pivots, vol regime shifts, and EMA crossovers — the moments a
    producer scrubbing in a DAW would want to jump between. Distinct from
    ``TempoChange`` because tempo is set elsewhere (per-movement headline,
    rubato breathing); these are pure navigation flags."""
    tick: int
    label: str


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
    a program change up front, and delta-timed note_on/note_off pairs.

    Channel 9 (GM drums): the standard kit (program 0) is implicit, but
    a non-zero program selects an alternate kit (Power, Electronic,
    TR-808, etc.) — we emit the program_change in that case.
    """
    track = mido.MidiTrack()
    if track_name:
        track.append(mido.MetaMessage("track_name", name=_ascii_safe(track_name), time=0))

    if channel != GM_DRUM_CHANNEL or program != 0:
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
    markers: list[StructuralMarker] | None,
    title: str | None,
) -> mido.MidiTrack:
    """Build the meta track with tempo changes, structural markers,
    optional title, and optional movement-name labels.

    Tempo changes carry a ``set_tempo`` meta event and (if labelled) an
    accompanying ``marker``. Structural markers carry only a ``marker``
    event — tempo is unchanged at that tick. When a structural marker
    and a tempo change land on the same tick, the tempo change comes
    first so DAWs see the tempo before the label.
    """
    track = mido.MidiTrack()
    if title:
        track.append(mido.MetaMessage("track_name", name=_ascii_safe(title), time=0))

    tempo_changes = list(tempo_changes or [])
    markers = list(markers or [])

    if not tempo_changes and not markers:
        track.append(mido.MetaMessage(
            "set_tempo", tempo=mido.bpm2tempo(config.bpm), time=0,
        ))
        return track

    # MIDI requires a tempo at tick 0. If the caller didn't supply one
    # (e.g. plain mode passing only markers), seed the stream with the
    # config's base BPM so playback starts at a defined tempo.
    if not any(c.tick == 0 for c in tempo_changes):
        tempo_changes.append(TempoChange(tick=0, bpm=config.bpm, label=None))

    # Interleave the two streams in tick order. ``kind=0`` for tempo
    # changes (sorted first at equal ticks), ``kind=1`` for marker-only
    # events. Sort is stable, so equal-tick same-kind events preserve
    # their input order — important for the rubato collision dodge that
    # lays a same-tick marker right next to the headline change.
    items: list[tuple[int, int, object]] = []
    for c in tempo_changes:
        items.append((c.tick, 0, c))
    for m in markers:
        items.append((m.tick, 1, m))
    items.sort(key=lambda x: (x[0], x[1]))

    last_tick = 0
    for tick, kind, payload in items:
        delta = max(0, tick - last_tick)
        last_tick = tick
        if kind == 0:
            change = payload  # type: ignore[assignment]
            track.append(mido.MetaMessage(
                "set_tempo", tempo=mido.bpm2tempo(change.bpm), time=delta,
            ))
            if change.label:
                # 'marker' meta messages show up as labelled section markers
                # in most DAWs — perfect for movement names.
                track.append(mido.MetaMessage(
                    "marker", text=_ascii_safe(change.label), time=0,
                ))
        else:
            marker = payload  # type: ignore[assignment]
            track.append(mido.MetaMessage(
                "marker", text=_ascii_safe(marker.label), time=delta,
            ))
    return track


def write_midi(
    events: list[MidiEvent],
    path: Path,
    config: RunConfig,
    tempo_changes: list[TempoChange] | None = None,
    markers: list[StructuralMarker] | None = None,
    include_percussion: bool = False,
    include_bass: bool = False,
    include_voice: bool = False,
    title: str | None = None,
) -> None:
    """Write the event list to a .mid file at `path`.

    Track order: meta → melody → harmony → bass → voice → percussion.
    All non-melody/non-harmony tracks are optional and only included
    when both the corresponding ``include_*`` flag is set AND the
    config has a program assigned (so passing ``include_voice=True``
    on a palette without a ``voice_program`` is a no-op rather than an
    empty-track artefact).

    ``markers`` adds labelled MIDI markers (structural events) on the
    meta track without changing tempo. Tempo is driven exclusively by
    ``tempo_changes``.
    """
    mid = mido.MidiFile(type=1, ticks_per_beat=config.ppq)
    mid.tracks.append(_build_meta_track(config, tempo_changes, markers, title))

    mid.tracks.append(_track_for_channel(
        events, config.melody_channel, config.melody_program,
        track_name="Melody",
    ))
    mid.tracks.append(_track_for_channel(
        events, config.harmony_channel, config.harmony_program,
        track_name="Harmony",
    ))
    if include_bass and config.bass_program is not None:
        mid.tracks.append(_track_for_channel(
            events, config.bass_channel, config.bass_program,
            track_name="Bass",
        ))
    if include_voice and config.voice_program is not None:
        mid.tracks.append(_track_for_channel(
            events, config.voice_channel, config.voice_program,
            track_name="Voice",
        ))
    if include_percussion:
        mid.tracks.append(_track_for_channel(
            events, GM_DRUM_CHANNEL, program=config.drum_program,
            track_name="Percussion",
        ))

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mid.save(str(path))


# --- Stems --------------------------------------------------------------

# Stem name → (channel-resolver, program-resolver, suffix) tuples in the
# order they appear in the combined file. Channel/program resolvers take
# the RunConfig so the writer doesn't need to know which channels are
# fixed (drums) vs config-driven (melody/harmony/bass/voice).
def _stem_specs(config: RunConfig) -> list[tuple[str, int, int]]:
    """Return ``(name, channel, program)`` triples for stems whose
    channel may carry events. We always emit melody and harmony; bass,
    voice, and percussion only when the config has them configured."""
    specs: list[tuple[str, int, int]] = [
        ("melody", config.melody_channel, config.melody_program),
        ("harmony", config.harmony_channel, config.harmony_program),
    ]
    if config.bass_program is not None:
        specs.append(("bass", config.bass_channel, config.bass_program))
    if config.voice_program is not None:
        specs.append(("voice", config.voice_channel, config.voice_program))
    # Percussion is on the GM drum channel; program selects the kit.
    specs.append(("percussion", GM_DRUM_CHANNEL, config.drum_program))
    return specs


def write_midi_stems(
    events: list[MidiEvent],
    base_path: Path,
    config: RunConfig,
    tempo_changes: list[TempoChange] | None = None,
    markers: list[StructuralMarker] | None = None,
    title: str | None = None,
) -> list[Path]:
    """Write one .mid per active channel alongside the combined output.

    Filenames follow ``{base.stem}.{stem_name}.mid`` so a producer can
    drop them next to the combined file in their DAW. Each stem gets the
    same meta track (tempo + markers) as the combined file so playback
    timing is preserved if a stem is auditioned in isolation.

    A stem with zero matching events is skipped (no empty .mid files).
    Returns the list of paths actually written, in the same order as
    ``_stem_specs`` (melody, harmony, bass, voice, percussion).
    """
    base_path = Path(base_path)
    written: list[Path] = []

    for name, channel, program in _stem_specs(config):
        stem_events = [e for e in events if e.channel == channel]
        if not stem_events:
            continue

        mid = mido.MidiFile(type=1, ticks_per_beat=config.ppq)
        mid.tracks.append(
            _build_meta_track(config, tempo_changes, markers, title)
        )
        mid.tracks.append(_track_for_channel(
            stem_events, channel, program, track_name=name.capitalize(),
        ))

        # `path/to/output.mid` → `path/to/output.melody.mid`
        out = base_path.with_suffix("." + name + base_path.suffix)
        out.parent.mkdir(parents=True, exist_ok=True)
        mid.save(str(out))
        written.append(out)

    return written
