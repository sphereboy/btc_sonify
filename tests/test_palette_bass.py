"""Tests for the palette system + sub-bass track."""
from __future__ import annotations

from pathlib import Path

import mido
import pandas as pd
import pytest

from btc_sonify.bass import map_bass
from btc_sonify.config import (
    DRUM_KIT_ELECTRONIC,
    DRUM_KIT_POWER,
    DRUM_KIT_STANDARD,
    DRUM_KIT_TR_808,
    PALETTES,
    Palette,
    RunConfig,
)
from btc_sonify.midi_writer import write_midi
from btc_sonify.percussion import MovementOffset

FIXTURE = Path(__file__).parent / "fixtures" / "sample_btc_daily.csv"


def _candle(o, h, l, c, v=1000.0):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


def _df(candles: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(candles)
    df["timestamp"] = pd.date_range("2024-01-01", periods=len(df), freq="D", tz="UTC")
    return df[["timestamp", "open", "high", "low", "close", "volume"]]


@pytest.fixture
def fixture_df() -> pd.DataFrame:
    df = pd.read_csv(FIXTURE)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


# --- Palettes ----------------------------------------------------------

def test_all_four_palettes_defined():
    assert set(PALETTES) == {"classical", "synthwave", "cinematic", "electronic"}


def test_classical_has_no_bass():
    assert PALETTES["classical"].bass_program is None


def test_modern_palettes_all_have_bass():
    for name in ("synthwave", "cinematic", "electronic"):
        assert PALETTES[name].bass_program is not None


def test_palette_drum_kits_match_intent():
    assert PALETTES["classical"].drum_program == DRUM_KIT_STANDARD
    assert PALETTES["synthwave"].drum_program == DRUM_KIT_ELECTRONIC
    assert PALETTES["cinematic"].drum_program == DRUM_KIT_POWER
    assert PALETTES["electronic"].drum_program == DRUM_KIT_TR_808


def test_with_palette_replaces_all_four_programs():
    base = RunConfig()  # classical defaults
    cfg = base.with_palette(PALETTES["synthwave"])
    p = PALETTES["synthwave"]
    assert cfg.melody_program == p.melody_program
    assert cfg.harmony_program == p.harmony_program
    assert cfg.drum_program == p.drum_program
    assert cfg.bass_program == p.bass_program


def test_with_palette_preserves_other_fields():
    base = RunConfig(scale="blues", root="D", octaves=4, bpm=90)
    cfg = base.with_palette(PALETTES["cinematic"])
    assert cfg.scale == "blues"
    assert cfg.root == "D"
    assert cfg.octaves == 4
    assert cfg.bpm == 90


# --- Bass mapping (plain mode) -----------------------------------------

def test_bass_returns_empty_when_no_bass_program():
    cfg = RunConfig()  # classical default = no bass
    df = _df([_candle(100, 110, 90, 100, v=500) for _ in range(5)])
    assert map_bass(df, cfg) == []


def test_bass_returns_one_event_per_candle():
    cfg = RunConfig().with_palette(PALETTES["synthwave"])
    df = _df([_candle(100, 110, 90, 100 + i * 5, v=500) for i in range(8)])
    events = map_bass(df, cfg)
    assert len(events) == 8


def test_bass_events_on_bass_channel():
    cfg = RunConfig().with_palette(PALETTES["synthwave"])
    df = _df([_candle(100, 110, 90, 100, v=500) for _ in range(3)])
    events = map_bass(df, cfg)
    assert all(e.channel == cfg.bass_channel for e in events)


def test_bass_pitch_one_octave_below_melody():
    """Bass should be exactly 12 semitones below the quantized close."""
    from btc_sonify.mapping import map_candles_to_events
    cfg = RunConfig().with_palette(PALETTES["synthwave"])
    # All non-doji candles (open differs from close) so melody is one
    # note per candle — easier to align with bass for comparison.
    df = _df([_candle(100, 130, 90, 110 + i * 5, v=500) for i in range(8)])
    melody = [e for e in map_candles_to_events(df, cfg)
              if e.channel == cfg.melody_channel
              and e.duration_ticks > cfg.grace_ticks]
    bass = map_bass(df, cfg)
    assert len(melody) == len(bass) == 8
    for m, b in zip(melody, bass):
        assert b.note == m.note - 12


def test_bass_velocity_below_melody_max():
    cfg = RunConfig().with_palette(PALETTES["synthwave"])
    df = _df([_candle(100, 110, 90, 100, v=500) for _ in range(5)])
    events = map_bass(df, cfg)
    expected = int(round(cfg.velocity_max * cfg.bass_velocity_factor))
    assert all(e.velocity == expected for e in events)


def test_bass_holds_full_candle_duration():
    cfg = RunConfig().with_palette(PALETTES["synthwave"])
    df = _df([_candle(100, 110, 90, 100, v=500) for _ in range(3)])
    events = map_bass(df, cfg)
    assert all(e.duration_ticks == cfg.candle_ticks for e in events)


def test_bass_empty_dataframe():
    cfg = RunConfig().with_palette(PALETTES["synthwave"])
    assert map_bass(pd.DataFrame(), cfg) == []


def test_bass_deterministic(fixture_df):
    cfg = RunConfig().with_palette(PALETTES["synthwave"])
    a = map_bass(fixture_df, cfg)
    b = map_bass(fixture_df.copy(), cfg)
    assert a == b


# --- Bass mapping (symphony / movement_offsets) ------------------------

def test_bass_with_movement_offsets_uses_per_movement_config():
    """Each movement quantizes against its own scale; bass must follow."""
    cfg_a = RunConfig(scale="major", root="C").with_palette(PALETTES["synthwave"])
    cfg_b = RunConfig(scale="phrygian", root="A").with_palette(PALETTES["synthwave"])
    df = _df([_candle(100, 110, 90, 100 + i, v=500) for i in range(20)])
    offsets = [
        MovementOffset(start_idx=0, end_idx=9, tick_offset=0, config=cfg_a),
        MovementOffset(start_idx=10, end_idx=19,
                       tick_offset=10 * cfg_a.candle_ticks + cfg_a.ppq,
                       config=cfg_b),
    ]
    events = map_bass(df, cfg_a, movement_offsets=offsets)
    assert len(events) == 20


def test_bass_skipped_in_offsets_when_palette_has_no_bass():
    cfg = RunConfig()  # classical
    df = _df([_candle(100, 110, 90, 100, v=500) for _ in range(10)])
    offsets = [MovementOffset(0, 9, 0, config=cfg)]
    assert map_bass(df, cfg, movement_offsets=offsets) == []


# --- midi_writer integration -------------------------------------------

def test_writer_includes_bass_track_when_requested(tmp_path):
    from btc_sonify.mapping import MidiEvent
    cfg = RunConfig().with_palette(PALETTES["synthwave"])
    events = [
        MidiEvent(channel=cfg.melody_channel, note=60, velocity=80,
                  start_tick=0, duration_ticks=480),
        MidiEvent(channel=cfg.bass_channel, note=48, velocity=60,
                  start_tick=0, duration_ticks=480),
    ]
    out = tmp_path / "bass.mid"
    write_midi(events, out, cfg, include_bass=True)
    mid = mido.MidiFile(out)
    # meta + melody + harmony + bass = 4 tracks
    assert len(mid.tracks) == 4
    bass_track = mid.tracks[3]
    pcs = [m for m in bass_track if m.type == "program_change"]
    assert pcs[0].program == PALETTES["synthwave"].bass_program


def test_writer_drum_kit_program_change_emitted(tmp_path):
    from btc_sonify.mapping import MidiEvent
    cfg = RunConfig().with_palette(PALETTES["synthwave"])  # drum_program=24
    events = [
        MidiEvent(channel=9, note=36, velocity=80, start_tick=0, duration_ticks=240),
    ]
    out = tmp_path / "kit.mid"
    write_midi(events, out, cfg, include_percussion=True)
    mid = mido.MidiFile(out)
    drum_track = mid.tracks[-1]
    pcs = [m for m in drum_track if m.type == "program_change"]
    assert len(pcs) == 1
    assert pcs[0].channel == 9
    assert pcs[0].program == DRUM_KIT_ELECTRONIC


def test_writer_no_drum_program_change_for_classical(tmp_path):
    """Standard kit (program 0) should NOT emit a redundant program_change
    on channel 9 — preserves backwards compat with strict-GM synths."""
    from btc_sonify.mapping import MidiEvent
    cfg = RunConfig()  # classical: drum_program=0
    events = [
        MidiEvent(channel=9, note=36, velocity=80, start_tick=0, duration_ticks=240),
    ]
    out = tmp_path / "classical.mid"
    write_midi(events, out, cfg, include_percussion=True)
    mid = mido.MidiFile(out)
    drum_track = mid.tracks[-1]
    pcs = [m for m in drum_track if m.type == "program_change"]
    assert pcs == []


def test_writer_skips_bass_track_when_palette_has_no_bass(tmp_path):
    from btc_sonify.mapping import MidiEvent
    cfg = RunConfig()  # classical: bass_program=None
    events = [MidiEvent(channel=0, note=60, velocity=80, start_tick=0, duration_ticks=480)]
    out = tmp_path / "no_bass.mid"
    write_midi(events, out, cfg, include_bass=True)  # asked for bass...
    mid = mido.MidiFile(out)
    # ...but classical has no bass program, so writer drops the track
    assert len(mid.tracks) == 3  # meta + melody + harmony only
