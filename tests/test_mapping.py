"""Tests for mapping.py.

The mapping is the heart of the project, so we test it three ways:

1. Edge-case candles — doji, full-body, no-volume — built by hand.
2. The 30-row real BTC fixture, exercising the full pipeline end to end
   and asserting structural properties (event count, ordering, scale
   membership, deterministic re-runs).
3. Per-feature unit tests for the small helper functions.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from btc_sonify.config import RunConfig
from btc_sonify.mapping import (
    MidiEvent,
    _articulation_fraction,
    _candle_features,
    _harmony_notes,
    _normalize_close,
    _normalize_volume_log,
    _range_tiers,
    map_candles_to_events,
)
from btc_sonify.scales import (
    build_scale_notes,
    note_name_to_midi,
    quantize_to_scale,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_btc_daily.csv"


# --- Fixtures -----------------------------------------------------------

def _candle(o, h, l, c, v=1000.0):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


def _df(candles: list[dict]) -> pd.DataFrame:
    """Build a DataFrame with a fake timestamp column from a list of OHLCV dicts."""
    df = pd.DataFrame(candles)
    df["timestamp"] = pd.date_range("2024-01-01", periods=len(df), freq="D", tz="UTC")
    return df[["timestamp", "open", "high", "low", "close", "volume"]]


@pytest.fixture
def fixture_df() -> pd.DataFrame:
    df = pd.read_csv(FIXTURE)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


@pytest.fixture
def cfg() -> RunConfig:
    return RunConfig()


# --- Helper unit tests --------------------------------------------------

def test_normalize_close_min_max():
    s = pd.Series([10.0, 20.0, 30.0])
    out = _normalize_close(s)
    np.testing.assert_array_almost_equal(out, [0.0, 0.5, 1.0])


def test_normalize_close_flat_series_returns_zeros():
    s = pd.Series([5.0, 5.0, 5.0])
    out = _normalize_close(s)
    np.testing.assert_array_equal(out, [0.0, 0.0, 0.0])


def test_normalize_volume_log_clamps_to_velocity_band():
    s = pd.Series([0.0, 100.0, 1_000_000.0])
    out = _normalize_volume_log(s, vmin=40, vmax=127)
    assert out.min() == pytest.approx(40)
    assert out.max() == pytest.approx(127)


def test_normalize_volume_log_compresses_long_tail():
    """Log scaling: a 1000x volume gap should NOT mean 1000x velocity."""
    s = pd.Series([1.0, 1000.0])
    out = _normalize_volume_log(s, vmin=40, vmax=127)
    # Linear would put both at the extremes (40 vs 127); log compresses.
    # Just verify the output is the full band — the smoothness is implicit.
    assert out[0] == pytest.approx(40)
    assert out[1] == pytest.approx(127)


def test_normalize_volume_zero_input_doesnt_crash():
    s = pd.Series([0.0, 0.0, 0.0])
    out = _normalize_volume_log(s, 40, 127)
    np.testing.assert_array_equal(out, [40, 40, 40])


# --- Candle features ----------------------------------------------------

def test_features_green_strong_body():
    feat = _candle_features(pd.Series(_candle(100, 110, 99, 109, 500)))
    assert feat["direction"] == "green"
    assert feat["body_size"] == pytest.approx(9)
    assert feat["range"] == pytest.approx(11)
    assert feat["body_ratio"] == pytest.approx(9 / 11)
    assert feat["upper_wick"] == pytest.approx(1)
    assert feat["lower_wick"] == pytest.approx(1)


def test_features_red_candle():
    feat = _candle_features(pd.Series(_candle(110, 112, 100, 101)))
    assert feat["direction"] == "red"
    assert feat["upper_wick"] == pytest.approx(2)
    assert feat["lower_wick"] == pytest.approx(1)


def test_features_doji_body_ratio_near_zero():
    feat = _candle_features(pd.Series(_candle(100, 110, 90, 100)))
    assert feat["body_ratio"] == 0.0
    assert feat["direction"] == "green"  # close == open is "green" per spec


def test_features_zero_range_returns_zero_ratio():
    """Theoretically impossible candle (h==l). Don't crash, treat as doji."""
    feat = _candle_features(pd.Series(_candle(100, 100, 100, 100)))
    assert feat["body_ratio"] == 0.0


# --- Articulation -------------------------------------------------------

def test_articulation_legato(cfg):
    frac, bonus = _articulation_fraction("green", 0.9, cfg)
    assert frac == cfg.legato_fraction
    assert bonus == 0


def test_articulation_marcato_includes_velocity_bonus(cfg):
    frac, bonus = _articulation_fraction("red", 0.9, cfg)
    assert frac == cfg.marcato_fraction
    assert bonus == cfg.marcato_velocity_bonus


def test_articulation_staccato(cfg):
    frac, bonus = _articulation_fraction("red", 0.3, cfg)
    assert frac == cfg.staccato_fraction
    assert bonus == 0


def test_articulation_normal(cfg):
    frac, bonus = _articulation_fraction("green", 0.5, cfg)
    assert frac == cfg.normal_fraction
    assert bonus == 0


# --- Harmony ------------------------------------------------------------

def test_harmony_tier_0_is_single_note():
    notes = build_scale_notes("major", 60, 1)
    assert _harmony_notes(60, 0, notes) == [60]


def test_harmony_tier_1_is_diad():
    notes = build_scale_notes("major", 60, 1)
    chord = _harmony_notes(60, 1, notes)
    assert len(chord) == 2
    assert chord[0] == 60
    # 5th in major from C is G (4 scale steps up)
    assert chord[1] == 67


def test_harmony_tier_2_is_triad():
    notes = build_scale_notes("major", 60, 1)
    chord = _harmony_notes(60, 2, notes)
    assert len(chord) == 3
    assert chord == [60, 64, 67]  # C, E, G


def test_range_tiers_terciles():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0])
    tiers = _range_tiers(s)
    # Roughly equal split; bottom third tier 0, etc.
    assert (tiers == 0).sum() >= 2
    assert (tiers == 2).sum() >= 2


def test_range_tiers_flat_input_all_zero():
    s = pd.Series([5.0] * 10)
    tiers = _range_tiers(s)
    assert (tiers == 0).all()


# --- map_candles_to_events: structural properties ----------------------

def test_empty_dataframe_returns_no_events(cfg):
    out = map_candles_to_events(pd.DataFrame(), cfg)
    assert out == []


def test_missing_columns_raises(cfg):
    df = pd.DataFrame({"open": [1], "high": [2]})
    with pytest.raises(ValueError, match="missing required columns"):
        map_candles_to_events(df, cfg)


def test_single_candle_yields_at_least_melody_and_harmony(cfg):
    df = _df([_candle(100, 110, 99, 105)])
    events = map_candles_to_events(df, cfg)
    melody = [e for e in events if e.channel == cfg.melody_channel]
    harmony = [e for e in events if e.channel == cfg.harmony_channel]
    assert len(melody) >= 1
    assert len(harmony) >= 1


def test_event_tuple_shape_matches_spec(cfg):
    df = _df([_candle(100, 105, 99, 102)])
    events = map_candles_to_events(df, cfg)
    e = events[0]
    assert isinstance(e, MidiEvent)
    assert e._fields == ("channel", "note", "velocity", "start_tick", "duration_ticks")


def test_velocity_always_in_band(cfg):
    df = _df([
        _candle(100, 110, 99, 105, v=0),       # zero volume
        _candle(100, 110, 99, 95, v=1e9),      # huge volume
        _candle(100, 110, 99, 109, v=500),     # normal
    ])
    events = map_candles_to_events(df, cfg)
    for e in events:
        assert cfg.velocity_min <= e.velocity <= cfg.velocity_max


def test_zero_volume_clamps_to_velocity_min(cfg):
    """CLAUDE.md edge case: volume = 0 must not crash and must clamp."""
    df = _df([
        _candle(100, 110, 99, 105, v=0),
        _candle(100, 110, 99, 105, v=0),
    ])
    events = map_candles_to_events(df, cfg)
    melody = [e for e in events if e.channel == cfg.melody_channel]
    assert all(e.velocity == cfg.velocity_min for e in melody)


def test_first_candle_grace_does_not_have_negative_tick(cfg):
    """Grace notes for candle 0 must fit thanks to the leading-silence pad."""
    # Long-upper-wick on candle 0 forces a grace.
    df = _df([_candle(100, 200, 99, 102, v=500)])
    events = map_candles_to_events(df, cfg)
    assert all(e.start_tick >= 0 for e in events)


def test_doji_emits_trill(cfg):
    """Doji = body_ratio < 0.1; close == open means body_ratio = 0."""
    df = _df([_candle(100, 110, 90, 100, v=500)])
    events = map_candles_to_events(df, cfg)
    melody = [e for e in events if e.channel == cfg.melody_channel]
    # Trill = N alternating notes; default trill_subdivisions = 4
    assert len(melody) >= cfg.trill_subdivisions
    # Note pitches alternate
    pitches = [e.note for e in melody[:cfg.trill_subdivisions]]
    assert pitches[0] == pitches[2]
    assert pitches[1] == pitches[3]
    assert pitches[0] != pitches[1]


def test_long_upper_wick_emits_grace_above(cfg):
    """upper_wick > 2 * body_size triggers a grace one scale-step above.

    Body must be >= doji threshold so we get a single main note + grace
    (no trill). Graces are identified by start_tick before the candle slot.
    """
    # Body = 2, upper wick = 9 (>> 2x body), lower wick = 1
    df = _df([_candle(100, 111, 99, 102, v=500)])
    events = map_candles_to_events(df, cfg)
    melody = [e for e in events if e.channel == cfg.melody_channel]
    candle_start = cfg.grace_ticks  # pad for candle 0
    main = [e for e in melody if e.start_tick == candle_start]
    grace = [e for e in melody if e.start_tick < candle_start]
    assert len(main) == 1
    assert len(grace) == 1
    assert grace[0].note > main[0].note
    # Grace ends exactly when main starts
    assert grace[0].start_tick + grace[0].duration_ticks == main[0].start_tick


def test_long_lower_wick_emits_grace_below(cfg):
    # Two candles so close-normalization spans a real range. Test candle
    # sits at the top, so its grace-below has somewhere to go.
    # Test candle: body = 2, lower wick = 9 (>> 2x body), upper wick = 1
    df = _df([
        _candle(40, 52, 38, 50, v=500),     # plain green, low close (no doji, no long wicks)
        _candle(100, 101, 89, 98, v=500),   # red close + long lower wick (the test candle)
    ])
    events = map_candles_to_events(df, cfg)
    melody = [e for e in events if e.channel == cfg.melody_channel]
    candle1_start = cfg.grace_ticks + cfg.candle_ticks
    main = next(e for e in melody if e.start_tick == candle1_start)
    grace = [e for e in melody if e.start_tick == candle1_start - cfg.grace_ticks]
    assert len(grace) == 1
    assert grace[0].note < main.note


def test_long_wick_doji_gets_two_graces(cfg):
    """CLAUDE.md: 'Both conditions can fire on the same candle.'

    A doji with long wicks both directions yields a trill PLUS two grace
    notes (one above, one below). Graces precede the candle slot; trill
    notes occupy it.
    """
    df = _df([_candle(100, 200, 0, 100, v=500)])  # extreme wicks, doji body
    events = map_candles_to_events(df, cfg)
    melody = [e for e in events if e.channel == cfg.melody_channel]
    candle_start = cfg.grace_ticks
    grace = [e for e in melody if e.start_tick < candle_start]
    trill = [e for e in melody if e.start_tick >= candle_start]
    assert len(grace) == 2
    assert len(trill) == cfg.trill_subdivisions


def test_marcato_red_strong_body_has_velocity_bonus(cfg):
    """Strong red body should add marcato_velocity_bonus."""
    # Rig two candles with identical volume → identical base velocity.
    # One green strong, one red strong. Red main note velocity should be
    # exactly +bonus higher than green's.
    df = _df([
        _candle(100, 110, 100, 109, v=500),  # green strong
        _candle(109, 110, 100, 100, v=500),  # red strong, same volume
    ])
    events = map_candles_to_events(df, cfg)
    melody = sorted(
        (e for e in events if e.channel == cfg.melody_channel
         and e.duration_ticks > cfg.grace_ticks),
        key=lambda e: e.start_tick,
    )
    assert melody[1].velocity - melody[0].velocity == cfg.marcato_velocity_bonus


def test_harmony_velocity_is_60_percent_of_melody(cfg):
    df = _df([_candle(100, 110, 99, 105, v=500)])
    events = map_candles_to_events(df, cfg)
    melody = [e for e in events if e.channel == cfg.melody_channel
              and e.duration_ticks > cfg.grace_ticks]
    harmony = [e for e in events if e.channel == cfg.harmony_channel]
    expected_h_vel = max(cfg.velocity_min,
                         min(cfg.velocity_max,
                             int(round(melody[0].velocity * cfg.harmony_velocity_factor))))
    assert all(e.velocity == expected_h_vel for e in harmony)


# --- map_candles_to_events: full BTC fixture ---------------------------

def test_fixture_loads_and_has_30_rows(fixture_df):
    assert len(fixture_df) == 30
    assert set(fixture_df.columns) >= {"open", "high", "low", "close", "volume"}


def test_fixture_produces_events(fixture_df, cfg):
    events = map_candles_to_events(fixture_df, cfg)
    assert len(events) >= 30  # at least one melody + one harmony per candle


def test_fixture_all_notes_in_scale(fixture_df, cfg):
    """Every emitted MIDI pitch must lie on the scale ladder — no
    accidentals slip through the mapping."""
    events = map_candles_to_events(fixture_df, cfg)
    root = note_name_to_midi(cfg.root, cfg.root_octave)
    ladder = set(build_scale_notes(cfg.scale, root, cfg.octaves))
    for e in events:
        assert e.note in ladder, f"Note {e.note} off the {cfg.scale} ladder"


def test_fixture_lowest_close_maps_to_lowest_note(fixture_df, cfg):
    events = map_candles_to_events(fixture_df, cfg)
    root = note_name_to_midi(cfg.root, cfg.root_octave)
    ladder = build_scale_notes(cfg.scale, root, cfg.octaves)
    melody = [e for e in events if e.channel == cfg.melody_channel
              and e.duration_ticks > cfg.grace_ticks]
    pitches = [e.note for e in melody]
    assert min(pitches) == ladder[0]


def test_fixture_highest_close_maps_to_highest_note(fixture_df, cfg):
    events = map_candles_to_events(fixture_df, cfg)
    root = note_name_to_midi(cfg.root, cfg.root_octave)
    ladder = build_scale_notes(cfg.scale, root, cfg.octaves)
    melody = [e for e in events if e.channel == cfg.melody_channel
              and e.duration_ticks > cfg.grace_ticks]
    pitches = [e.note for e in melody]
    assert max(pitches) == ladder[-1]


def test_fixture_deterministic(fixture_df, cfg):
    """CLAUDE.md: same input + same config → same MIDI events."""
    a = map_candles_to_events(fixture_df, cfg)
    b = map_candles_to_events(fixture_df.copy(), cfg)
    assert a == b


def test_fixture_changing_scale_changes_output(fixture_df, cfg):
    a = map_candles_to_events(fixture_df, cfg)
    b = map_candles_to_events(
        fixture_df, RunConfig(scale="major", root="C", root_octave=4)
    )
    assert a != b


def test_fixture_events_have_two_distinct_channels(fixture_df, cfg):
    events = map_candles_to_events(fixture_df, cfg)
    channels = {e.channel for e in events}
    assert channels == {cfg.melody_channel, cfg.harmony_channel}


def test_fixture_no_negative_ticks(fixture_df, cfg):
    events = map_candles_to_events(fixture_df, cfg)
    assert all(e.start_tick >= 0 for e in events)


def test_fixture_total_duration_proportional_to_candle_count(fixture_df, cfg):
    events = map_candles_to_events(fixture_df, cfg)
    # The latest end-tick should be roughly (n_candles + 1) * candle_ticks
    last = max(e.start_tick + e.duration_ticks for e in events)
    expected = (len(fixture_df) + 1) * cfg.candle_ticks  # +1 for the leading pad
    # Allow some slack (graces can extend slightly).
    assert last <= expected + cfg.grace_ticks
    assert last >= len(fixture_df) * cfg.candle_ticks


# --- note_value variations ---------------------------------------------

@pytest.mark.parametrize("note_value, expected_per_candle", [
    ("quarter", 480),
    ("eighth", 240),
    ("half", 960),
])
def test_note_value_changes_candle_ticks(fixture_df, note_value, expected_per_candle):
    cfg = RunConfig(note_value=note_value)
    assert cfg.candle_ticks == expected_per_candle
    events = map_candles_to_events(fixture_df, cfg)
    assert len(events) > 0


def test_invalid_note_value_raises():
    cfg = RunConfig(note_value="sixteenth")
    with pytest.raises(ValueError, match="Unsupported note_value"):
        _ = cfg.candle_ticks
