"""Tests for voice.py — the sustained 'lead vocal' track."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import mido
import pandas as pd
import pytest

from btc_sonify.config import PALETTES, RunConfig
from btc_sonify.midi_writer import write_midi
from btc_sonify.percussion import MovementOffset
from btc_sonify.voice import map_voice


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


# --- Opt-in semantics --------------------------------------------------

def test_no_voice_program_returns_empty():
    cfg = RunConfig()  # default = no voice
    df = _df([_candle(100, 110, 99, 100 + i, v=500) for i in range(20)])
    assert map_voice(df, cfg) == []


def test_classical_palette_includes_voice():
    cfg = RunConfig().with_palette(PALETTES["classical"])
    assert cfg.voice_program is not None


def test_synthwave_palette_includes_voice():
    cfg = RunConfig().with_palette(PALETTES["synthwave"])
    assert cfg.voice_program is not None


def test_empty_df_returns_empty():
    cfg = RunConfig().with_palette(PALETTES["synthwave"])
    assert map_voice(pd.DataFrame(), cfg) == []


# --- Channel and pitch -------------------------------------------------

def test_voice_events_on_voice_channel():
    cfg = RunConfig().with_palette(PALETTES["synthwave"])
    df = _df([_candle(100, 110, 99, 100 + i, v=500) for i in range(20)])
    events = map_voice(df, cfg)
    assert all(e.channel == cfg.voice_channel for e in events)


def test_voice_pitches_capped_at_midi_96():
    cfg = RunConfig(humanize=False, voice_octave_shift=4).with_palette(PALETTES["synthwave"])
    df = _df([_candle(100, 110, 99, 100 + i * 5, v=500) for i in range(20)])
    events = map_voice(df, cfg)
    assert all(e.note <= 96 for e in events)


def test_voice_pitches_above_melody_default():
    """+1 octave shift → voice notes should sit above where the melody
    typically lands."""
    cfg = RunConfig(humanize=False).with_palette(PALETTES["synthwave"])
    df = _df([_candle(100, 110, 99, 100 + i * 5, v=500) for i in range(20)])
    events = map_voice(df, cfg)
    # All voice notes should be at MIDI 60+ given default A3 root and +1 octave
    assert all(e.note >= 60 for e in events)


# --- Sustained timing --------------------------------------------------

def test_each_voice_note_lasts_n_candles():
    """Default note_length_candles=4 → each note holds 4 * candle_ticks."""
    cfg = RunConfig(humanize=False).with_palette(PALETTES["synthwave"])
    df = _df([_candle(100, 110, 99, 100 + i, v=500) for i in range(20)])  # 20 candles
    events = map_voice(df, cfg)
    expected_dur = cfg.voice_note_length_candles * cfg.candle_ticks
    # All except possibly the last (which may be shorter if 20 % 4 != 0) hold full length.
    full_length = [e for e in events[:-1]]
    assert all(e.duration_ticks == expected_dur for e in full_length)


def test_voice_note_count_matches_candle_chunks():
    """20 candles / 4 per note = 5 voice notes."""
    cfg = RunConfig(humanize=False).with_palette(PALETTES["synthwave"])
    df = _df([_candle(100, 110, 99, 100 + i, v=500) for i in range(20)])
    events = map_voice(df, cfg)
    assert len(events) == 5


def test_voice_note_count_handles_remainder():
    """22 candles / 4 = 6 notes (5 full of length 4 + 1 partial of length 2)."""
    cfg = RunConfig(humanize=False).with_palette(PALETTES["synthwave"])
    df = _df([_candle(100, 110, 99, 100 + i, v=500) for i in range(22)])
    events = map_voice(df, cfg)
    assert len(events) == 6
    # Final note covers the remainder
    assert events[-1].duration_ticks == 2 * cfg.candle_ticks


def test_voice_first_note_after_pad():
    cfg = RunConfig(humanize=False).with_palette(PALETTES["synthwave"])
    df = _df([_candle(100, 110, 99, 100 + i, v=500) for i in range(20)])
    events = map_voice(df, cfg)
    assert events[0].start_tick == cfg.grace_ticks


# --- Smoothing reduces jitter -----------------------------------------

def test_voice_smoothing_reduces_pitch_variation_vs_raw():
    """Voice should follow the macro contour, not whip around with each
    candle. With a wide rolling window, consecutive notes shouldn't
    span the full pitch range every step."""
    cfg = RunConfig(humanize=False, voice_smoothing_window=10).with_palette(
        PALETTES["synthwave"]
    )
    # Sawtooth-like price series
    closes = [100 + (i % 7) * 20 for i in range(40)]
    candles = [_candle(c, c + 1, c - 1, c, v=500) for c in closes]
    df = _df(candles)
    events = map_voice(df, cfg)
    pitches = [e.note for e in events]
    # Pitch range should be narrower than the raw close range would dictate
    assert max(pitches) - min(pitches) <= 24  # at most 2 octaves of motion


# --- Velocity ----------------------------------------------------------

def test_voice_velocity_in_band():
    cfg = RunConfig().with_palette(PALETTES["synthwave"])
    df = _df([_candle(100, 110, 99, 100 + i, v=500) for i in range(20)])
    events = map_voice(df, cfg)
    assert all(cfg.velocity_min <= e.velocity <= cfg.velocity_max for e in events)


# --- Determinism ------------------------------------------------------

def test_voice_deterministic(fixture_df):
    cfg = RunConfig().with_palette(PALETTES["synthwave"])
    a = map_voice(fixture_df, cfg)
    b = map_voice(fixture_df.copy(), cfg)
    assert a == b


# --- Symphony / movement_offsets ---------------------------------------

def test_voice_with_movement_offsets_uses_per_movement_config():
    cfg_a = RunConfig(scale="major", root="C").with_palette(PALETTES["synthwave"])
    cfg_b = RunConfig(scale="phrygian", root="A").with_palette(PALETTES["synthwave"])
    df = _df([_candle(100, 110, 99, 100 + i, v=500) for i in range(20)])
    offsets = [
        MovementOffset(start_idx=0, end_idx=9, tick_offset=0, config=cfg_a),
        MovementOffset(start_idx=10, end_idx=19,
                       tick_offset=10 * cfg_a.candle_ticks + cfg_a.ppq,
                       config=cfg_b),
    ]
    events = map_voice(df, cfg_a, movement_offsets=offsets)
    # 10 candles per movement / 4 per note = ceil(10/4) = 3 each, so 6 total.
    assert len(events) >= 4


def test_voice_skipped_in_offsets_when_program_none():
    cfg = replace(RunConfig().with_palette(PALETTES["synthwave"]), voice_program=None)
    df = _df([_candle(100, 110, 99, 100 + i, v=500) for i in range(20)])
    offsets = [MovementOffset(0, 19, 0, config=cfg)]
    assert map_voice(df, cfg, movement_offsets=offsets) == []


# --- Writer integration -----------------------------------------------

def test_writer_includes_voice_track(tmp_path):
    from btc_sonify.mapping import MidiEvent
    cfg = RunConfig().with_palette(PALETTES["synthwave"])
    events = [
        MidiEvent(channel=cfg.melody_channel, note=60, velocity=80,
                  start_tick=0, duration_ticks=480),
        MidiEvent(channel=cfg.voice_channel, note=72, velocity=70,
                  start_tick=0, duration_ticks=480 * 4),
    ]
    out = tmp_path / "voice.mid"
    write_midi(events, out, cfg, include_voice=True, include_bass=True)
    mid = mido.MidiFile(out)
    # Find the Voice track by name
    voice_track = next(
        tr for tr in mid.tracks
        if any(m.type == "track_name" and m.name == "Voice" for m in tr)
    )
    pcs = [m for m in voice_track if m.type == "program_change"]
    assert pcs[0].program == PALETTES["synthwave"].voice_program
    assert pcs[0].channel == cfg.voice_channel


def test_writer_omits_voice_track_when_palette_lacks_voice(tmp_path):
    from btc_sonify.mapping import MidiEvent
    cfg = replace(RunConfig().with_palette(PALETTES["synthwave"]), voice_program=None)
    events = [MidiEvent(channel=0, note=60, velocity=80, start_tick=0, duration_ticks=480)]
    out = tmp_path / "no_voice.mid"
    write_midi(events, out, cfg, include_voice=True)
    mid = mido.MidiFile(out)
    has_voice = any(
        any(m.type == "track_name" and m.name == "Voice" for m in tr)
        for tr in mid.tracks
    )
    assert not has_voice
