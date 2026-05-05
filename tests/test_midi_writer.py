"""Tests for midi_writer.py — round-trip events through mido."""
from __future__ import annotations

from pathlib import Path

import mido
import pandas as pd
import pytest

from btc_sonify.config import RunConfig
from btc_sonify.mapping import MidiEvent, map_candles_to_events
from btc_sonify.midi_writer import write_midi


FIXTURE = Path(__file__).parent / "fixtures" / "sample_btc_daily.csv"


@pytest.fixture
def cfg() -> RunConfig:
    return RunConfig()


@pytest.fixture
def fixture_events(cfg) -> list[MidiEvent]:
    df = pd.read_csv(FIXTURE)
    return map_candles_to_events(df, cfg)


# --- Structural shape ---------------------------------------------------

def test_creates_file(tmp_path, fixture_events, cfg):
    out = tmp_path / "out.mid"
    write_midi(fixture_events, out, cfg)
    assert out.exists()
    assert out.stat().st_size > 0


def test_creates_parent_directory(tmp_path, fixture_events, cfg):
    out = tmp_path / "nested" / "deep" / "out.mid"
    write_midi(fixture_events, out, cfg)
    assert out.exists()


def test_file_is_type_1_smf(tmp_path, fixture_events, cfg):
    out = tmp_path / "out.mid"
    write_midi(fixture_events, out, cfg)
    mid = mido.MidiFile(out)
    assert mid.type == 1
    assert mid.ticks_per_beat == cfg.ppq


def test_three_tracks_meta_melody_harmony(tmp_path, fixture_events, cfg):
    out = tmp_path / "out.mid"
    write_midi(fixture_events, out, cfg)
    mid = mido.MidiFile(out)
    assert len(mid.tracks) == 3


# --- Tempo --------------------------------------------------------------

def test_tempo_meta_message_present(tmp_path, fixture_events, cfg):
    out = tmp_path / "out.mid"
    write_midi(fixture_events, out, cfg)
    mid = mido.MidiFile(out)
    meta_track = mid.tracks[0]
    tempo_msgs = [m for m in meta_track if m.type == "set_tempo"]
    assert len(tempo_msgs) == 1
    assert tempo_msgs[0].tempo == mido.bpm2tempo(cfg.bpm)


@pytest.mark.parametrize("bpm", [60, 120, 180, 240])
def test_custom_bpm_round_trips(tmp_path, fixture_events, bpm):
    cfg = RunConfig(bpm=bpm)
    out = tmp_path / f"out_{bpm}.mid"
    write_midi(fixture_events, out, cfg)
    mid = mido.MidiFile(out)
    tempo_msgs = [m for m in mid.tracks[0] if m.type == "set_tempo"]
    assert tempo_msgs[0].tempo == mido.bpm2tempo(bpm)


# --- Program changes ----------------------------------------------------

def test_melody_track_has_acoustic_grand_program(tmp_path, fixture_events, cfg):
    out = tmp_path / "out.mid"
    write_midi(fixture_events, out, cfg)
    mid = mido.MidiFile(out)
    melody_track = mid.tracks[1]
    pcs = [m for m in melody_track if m.type == "program_change"]
    assert len(pcs) == 1
    assert pcs[0].program == 0  # Acoustic Grand
    assert pcs[0].channel == cfg.melody_channel


def test_harmony_track_has_string_ensemble_program(tmp_path, fixture_events, cfg):
    out = tmp_path / "out.mid"
    write_midi(fixture_events, out, cfg)
    mid = mido.MidiFile(out)
    harmony_track = mid.tracks[2]
    pcs = [m for m in harmony_track if m.type == "program_change"]
    assert len(pcs) == 1
    assert pcs[0].program == 48  # String Ensemble 1
    assert pcs[0].channel == cfg.harmony_channel


# --- Note round-trip ----------------------------------------------------

def _count_note_pairs(track: mido.MidiTrack) -> tuple[int, int]:
    on = sum(1 for m in track if m.type == "note_on" and m.velocity > 0)
    off = sum(1 for m in track if m.type == "note_off"
              or (m.type == "note_on" and m.velocity == 0))
    return on, off


def test_note_on_off_balanced(tmp_path, fixture_events, cfg):
    """Every note_on must have a matching note_off on the same track."""
    out = tmp_path / "out.mid"
    write_midi(fixture_events, out, cfg)
    mid = mido.MidiFile(out)
    for tr in mid.tracks[1:]:  # skip meta
        on, off = _count_note_pairs(tr)
        assert on == off


def test_melody_note_count_matches_events(tmp_path, fixture_events, cfg):
    out = tmp_path / "out.mid"
    write_midi(fixture_events, out, cfg)
    mid = mido.MidiFile(out)
    expected = sum(1 for e in fixture_events if e.channel == cfg.melody_channel)
    on_count = sum(
        1 for m in mid.tracks[1] if m.type == "note_on" and m.velocity > 0
    )
    assert on_count == expected


def test_harmony_note_count_matches_events(tmp_path, fixture_events, cfg):
    out = tmp_path / "out.mid"
    write_midi(fixture_events, out, cfg)
    mid = mido.MidiFile(out)
    expected = sum(1 for e in fixture_events if e.channel == cfg.harmony_channel)
    on_count = sum(
        1 for m in mid.tracks[2] if m.type == "note_on" and m.velocity > 0
    )
    assert on_count == expected


def test_round_trip_velocity_preserved(tmp_path, cfg):
    """Velocity values written must come back unchanged."""
    events = [
        MidiEvent(channel=0, note=60, velocity=80, start_tick=0, duration_ticks=480),
        MidiEvent(channel=0, note=64, velocity=120, start_tick=480, duration_ticks=480),
    ]
    out = tmp_path / "out.mid"
    write_midi(events, out, cfg)
    mid = mido.MidiFile(out)
    note_ons = [m for m in mid.tracks[1] if m.type == "note_on" and m.velocity > 0]
    assert [m.velocity for m in note_ons] == [80, 120]


def test_round_trip_pitches_preserved(tmp_path, cfg):
    events = [
        MidiEvent(channel=0, note=57, velocity=80, start_tick=0, duration_ticks=240),
        MidiEvent(channel=1, note=64, velocity=80, start_tick=0, duration_ticks=240),
        MidiEvent(channel=0, note=72, velocity=80, start_tick=240, duration_ticks=240),
    ]
    out = tmp_path / "out.mid"
    write_midi(events, out, cfg)
    mid = mido.MidiFile(out)
    melody_pitches = [m.note for m in mid.tracks[1]
                      if m.type == "note_on" and m.velocity > 0]
    harmony_pitches = [m.note for m in mid.tracks[2]
                       if m.type == "note_on" and m.velocity > 0]
    assert melody_pitches == [57, 72]
    assert harmony_pitches == [64]


# --- Empty input -------------------------------------------------------

def test_empty_event_list_writes_valid_file(tmp_path, cfg):
    """Writing zero events should still produce a parseable MIDI file
    with the meta track and tempo present."""
    out = tmp_path / "empty.mid"
    write_midi([], out, cfg)
    mid = mido.MidiFile(out)
    assert mid.type == 1
    assert len(mid.tracks) == 3
    note_ons = sum(1 for tr in mid.tracks for m in tr if m.type == "note_on")
    assert note_ons == 0


# --- Total duration ----------------------------------------------------

# --- Multi-tempo (symphony) --------------------------------------------

def test_tempo_changes_emit_multiple_set_tempo_messages(tmp_path, cfg):
    from btc_sonify.midi_writer import TempoChange
    events = [MidiEvent(0, 60, 80, 0, 480)]
    out = tmp_path / "multi.mid"
    write_midi(events, out, cfg, tempo_changes=[
        TempoChange(tick=0, bpm=120, label="I."),
        TempoChange(tick=4800, bpm=144, label="II."),
        TempoChange(tick=9600, bpm=96, label="III."),
    ])
    mid = mido.MidiFile(out)
    tempos = [m for m in mid.tracks[0] if m.type == "set_tempo"]
    assert len(tempos) == 3
    assert tempos[0].tempo == mido.bpm2tempo(120)
    assert tempos[1].tempo == mido.bpm2tempo(144)
    assert tempos[2].tempo == mido.bpm2tempo(96)


def test_movement_labels_written_as_markers(tmp_path, cfg):
    from btc_sonify.midi_writer import TempoChange
    events = [MidiEvent(0, 60, 80, 0, 480)]
    out = tmp_path / "labeled.mid"
    write_midi(events, out, cfg, tempo_changes=[
        TempoChange(tick=0, bpm=120, label="I. Bull 2020"),
        TempoChange(tick=4800, bpm=144, label="II. Bear 2022"),
    ])
    mid = mido.MidiFile(out)
    markers = [m for m in mid.tracks[0] if m.type == "marker"]
    assert [m.text for m in markers] == ["I. Bull 2020", "II. Bear 2022"]


# --- Percussion track --------------------------------------------------

def test_include_percussion_adds_fourth_track(tmp_path, cfg):
    events = [
        MidiEvent(channel=0, note=60, velocity=80, start_tick=0, duration_ticks=480),
        MidiEvent(channel=9, note=36, velocity=80, start_tick=0, duration_ticks=240),
    ]
    out = tmp_path / "drums.mid"
    write_midi(events, out, cfg, include_percussion=True)
    mid = mido.MidiFile(out)
    assert len(mid.tracks) == 4


def test_drum_track_has_no_program_change(tmp_path, cfg):
    """Channel 9 is implicit drums in GM — no program change should be emitted."""
    events = [
        MidiEvent(channel=9, note=36, velocity=80, start_tick=0, duration_ticks=240),
    ]
    out = tmp_path / "drums.mid"
    write_midi(events, out, cfg, include_percussion=True)
    mid = mido.MidiFile(out)
    drum_track = mid.tracks[3]
    pcs = [m for m in drum_track if m.type == "program_change"]
    assert pcs == []


def test_drum_notes_appear_only_on_drum_track(tmp_path, cfg):
    events = [
        MidiEvent(channel=0, note=60, velocity=80, start_tick=0, duration_ticks=480),
        MidiEvent(channel=9, note=36, velocity=80, start_tick=0, duration_ticks=240),
        MidiEvent(channel=9, note=42, velocity=60, start_tick=240, duration_ticks=120),
    ]
    out = tmp_path / "split.mid"
    write_midi(events, out, cfg, include_percussion=True)
    mid = mido.MidiFile(out)
    melody_drum_msgs = [m for m in mid.tracks[1]
                        if m.type == "note_on" and m.channel == 9]
    assert melody_drum_msgs == []
    drum_msgs = [m for m in mid.tracks[3]
                 if m.type == "note_on" and m.velocity > 0]
    assert len(drum_msgs) == 2


# --- Title / track names ----------------------------------------------

def test_title_attached_to_meta_track(tmp_path, cfg):
    events = [MidiEvent(0, 60, 80, 0, 480)]
    out = tmp_path / "titled.mid"
    write_midi(events, out, cfg, title="BTC Symphony 2020-2024")
    mid = mido.MidiFile(out)
    names = [m.name for m in mid.tracks[0] if m.type == "track_name"]
    assert "BTC Symphony 2020-2024" in names


def test_file_length_matches_event_extent(tmp_path, fixture_events, cfg):
    """The file's total tick length should be at least the latest event end."""
    expected_end = max(e.start_tick + e.duration_ticks for e in fixture_events)
    out = tmp_path / "out.mid"
    write_midi(fixture_events, out, cfg)
    mid = mido.MidiFile(out)
    # mid.length is in seconds; convert to ticks via tempo + ppq.
    seconds_per_beat = 60 / cfg.bpm
    expected_seconds = expected_end / cfg.ppq * seconds_per_beat
    assert mid.length == pytest.approx(expected_seconds, rel=0.05)
