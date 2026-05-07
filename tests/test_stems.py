"""Tests for write_midi_stems — per-channel .mid export."""
from __future__ import annotations

from pathlib import Path

import mido
import pytest

from btc_sonify.config import GM_DRUM_CHANNEL, PALETTES, RunConfig
from btc_sonify.mapping import MidiEvent
from btc_sonify.midi_writer import (
    StructuralMarker,
    TempoChange,
    write_midi,
    write_midi_stems,
)


@pytest.fixture
def cfg() -> RunConfig:
    return RunConfig().with_palette(PALETTES["cinematic"])


def _multi_channel_events(cfg: RunConfig) -> list[MidiEvent]:
    return [
        MidiEvent(cfg.melody_channel, 60, 100, 0, 480),
        MidiEvent(cfg.melody_channel, 62, 100, 480, 480),
        MidiEvent(cfg.harmony_channel, 64, 80, 0, 960),
        MidiEvent(cfg.bass_channel, 36, 90, 0, 960),
        MidiEvent(cfg.voice_channel, 72, 70, 0, 1920),
        MidiEvent(GM_DRUM_CHANNEL, 36, 110, 0, 240),
        MidiEvent(GM_DRUM_CHANNEL, 38, 100, 480, 240),
    ]


def _count_notes(path: Path) -> int:
    mid = mido.MidiFile(path)
    return sum(
        1
        for track in mid.tracks
        for msg in track
        if msg.type == "note_on" and getattr(msg, "velocity", 0) > 0
    )


# --- Smoke + basic structure -------------------------------------------

def test_writes_one_file_per_active_channel(tmp_path, cfg):
    base = tmp_path / "out.mid"
    paths = write_midi_stems(_multi_channel_events(cfg), base, cfg)
    names = sorted(p.name for p in paths)
    assert names == [
        "out.bass.mid",
        "out.harmony.mid",
        "out.melody.mid",
        "out.percussion.mid",
        "out.voice.mid",
    ]


def test_skips_empty_stems(tmp_path, cfg):
    """A channel with no events produces no file."""
    base = tmp_path / "out.mid"
    melody_only = [MidiEvent(cfg.melody_channel, 60, 100, 0, 480)]
    paths = write_midi_stems(melody_only, base, cfg)
    assert [p.name for p in paths] == ["out.melody.mid"]


def test_each_stem_isolates_its_channel(tmp_path, cfg):
    base = tmp_path / "out.mid"
    events = _multi_channel_events(cfg)
    paths = write_midi_stems(events, base, cfg)

    expected = {
        "melody": cfg.melody_channel,
        "harmony": cfg.harmony_channel,
        "bass": cfg.bass_channel,
        "voice": cfg.voice_channel,
        "percussion": GM_DRUM_CHANNEL,
    }
    for p in paths:
        stem = p.name.split(".")[1]
        ch = expected[stem]
        mid = mido.MidiFile(p)
        for track in mid.tracks:
            for msg in track:
                if msg.type in ("note_on", "note_off"):
                    assert msg.channel == ch, (
                        f"{p.name} contains a note on channel {msg.channel}, "
                        f"expected {ch}"
                    )


def test_stems_sum_to_combined_note_count(tmp_path, cfg):
    base = tmp_path / "out.mid"
    events = _multi_channel_events(cfg)

    write_midi(
        events, base, cfg,
        include_bass=True, include_voice=True, include_percussion=True,
    )
    combined_notes = _count_notes(base)

    paths = write_midi_stems(events, base, cfg)
    stem_notes = sum(_count_notes(p) for p in paths)

    assert stem_notes == combined_notes


# --- Determinism --------------------------------------------------------

def test_stem_bytes_are_deterministic(tmp_path, cfg):
    events = _multi_channel_events(cfg)
    a_dir = tmp_path / "a"
    b_dir = tmp_path / "b"
    a_dir.mkdir()
    b_dir.mkdir()
    a_paths = write_midi_stems(events, a_dir / "out.mid", cfg)
    b_paths = write_midi_stems(events, b_dir / "out.mid", cfg)
    assert len(a_paths) == len(b_paths)
    for a, b in zip(a_paths, b_paths):
        assert a.read_bytes() == b.read_bytes()


# --- Meta track propagation --------------------------------------------

def test_each_stem_carries_meta_track_with_tempo_and_markers(tmp_path, cfg):
    base = tmp_path / "out.mid"
    events = _multi_channel_events(cfg)
    paths = write_midi_stems(
        events, base, cfg,
        tempo_changes=[TempoChange(tick=0, bpm=120, label="I.")],
        markers=[StructuralMarker(tick=480, label="swing_low_test")],
    )
    for p in paths:
        mid = mido.MidiFile(p)
        meta = mid.tracks[0]
        types = {m.type for m in meta}
        assert "set_tempo" in types
        marker_texts = [m.text for m in meta if m.type == "marker"]
        assert "swing_low_test" in marker_texts
        assert "I." in marker_texts
