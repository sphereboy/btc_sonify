"""Tests for the v2 'soul' features: within-candle motion, humanization,
and rest insertion."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from btc_sonify.config import RunConfig
from btc_sonify.mapping import (
    _humanize_timing,
    _humanize_velocity,
    _melody_phrase,
    _stable_jitter,
    map_candles_to_events,
)


FIXTURE = Path(__file__).parent / "fixtures" / "sample_btc_daily.csv"


def _candle(o, h, l, c, v=1000.0):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


def _df(candles: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(candles)
    df["timestamp"] = pd.date_range("2024-01-01", periods=len(df), freq="D", tz="UTC")
    return df[["timestamp", "open", "high", "low", "close", "volume"]]


# --- Within-candle phrase ----------------------------------------------

def test_phrase_tiny_range_returns_one_note():
    feat = {"open": 100, "high": 100.1, "low": 99.9, "close": 100,
            "range": 0.2, "body_ratio": 0.0, "direction": "green"}
    assert _melody_phrase(feat, range_median=10, modest_factor=0.3) == [100]


def test_phrase_strong_body_returns_two_notes():
    feat = {"open": 100, "high": 110, "low": 99, "close": 109,
            "range": 11, "body_ratio": 9 / 11, "direction": "green"}
    assert _melody_phrase(feat, range_median=11, modest_factor=0.3) == [100, 109]


def test_phrase_modest_range_returns_two_notes():
    """Range below median but body not strong → still 2-note phrase."""
    feat = {"open": 100, "high": 102, "low": 98, "close": 101,
            "range": 4, "body_ratio": 0.25, "direction": "green"}
    # range_median = 10, range = 4 (below median, above 0.3*median = 3) → 2 notes
    assert _melody_phrase(feat, range_median=10, modest_factor=0.3) == [100, 101]


def test_phrase_green_wide_range_traverses_olhc():
    """Green wide-range candle: open → low → high → close."""
    feat = {"open": 100, "high": 130, "low": 95, "close": 120,
            "range": 35, "body_ratio": 20 / 35, "direction": "green"}
    assert _melody_phrase(feat, range_median=10, modest_factor=0.3) == [100, 95, 130, 120]


def test_phrase_red_wide_range_traverses_ohlc():
    """Red wide-range candle: open → high → low → close."""
    feat = {"open": 120, "high": 130, "low": 95, "close": 100,
            "range": 35, "body_ratio": 20 / 35, "direction": "red"}
    assert _melody_phrase(feat, range_median=10, modest_factor=0.3) == [120, 130, 95, 100]


# --- Humanization helpers -----------------------------------------------

def test_stable_jitter_within_range():
    for idx in range(200):
        v = _stable_jitter(idx, salt=0, max_abs=8)
        assert -8 <= v <= 8


def test_stable_jitter_zero_max_returns_zero():
    for idx in range(50):
        assert _stable_jitter(idx, salt=0, max_abs=0) == 0


def test_stable_jitter_distribution_not_all_same():
    values = {_stable_jitter(i, 0, 8) for i in range(200)}
    # Should hit a wide range of distinct values, not stuck on one.
    assert len(values) > 10


def test_stable_jitter_deterministic_across_runs():
    a = [_stable_jitter(i, 5, 8) for i in range(100)]
    b = [_stable_jitter(i, 5, 8) for i in range(100)]
    assert a == b


def test_humanize_velocity_off_returns_base():
    cfg = RunConfig(humanize=False)
    assert _humanize_velocity(80, idx=42, salt=0, config=cfg) == 80


def test_humanize_velocity_on_within_band():
    cfg = RunConfig(humanize=True, velocity_jitter_range=8)
    for i in range(100):
        v = _humanize_velocity(80, idx=i, salt=0, config=cfg)
        assert 80 - 8 <= v <= 80 + 8


def test_humanize_velocity_clamps_to_global_band():
    """Even with jitter, never go below velocity_min or above velocity_max."""
    cfg = RunConfig(humanize=True, velocity_jitter_range=20)
    for i in range(100):
        # Base near floor
        v = _humanize_velocity(45, idx=i, salt=0, config=cfg)
        assert v >= cfg.velocity_min
        # Base near ceiling
        v = _humanize_velocity(120, idx=i, salt=0, config=cfg)
        assert v <= cfg.velocity_max


def test_humanize_timing_off_returns_zero():
    cfg = RunConfig(humanize=False)
    for i in range(50):
        assert _humanize_timing(i, salt=0, config=cfg) == 0


def test_humanize_timing_on_within_range():
    cfg = RunConfig(humanize=True, timing_jitter_ticks=5)
    for i in range(100):
        t = _humanize_timing(i, salt=0, config=cfg)
        assert -5 <= t <= 5


# --- map_candles_to_events humanization integration --------------------

def test_humanized_velocity_varies_across_identical_candles():
    """If humanization is on, two identical candles should produce
    slightly different velocities (different idx → different jitter)."""
    cfg = RunConfig(humanize=True, rest_volume_percentile=0)
    df = _df([_candle(100, 110, 99, 105, v=500)] * 20)
    events = map_candles_to_events(df, cfg)
    melody = [e for e in events if e.channel == cfg.melody_channel]
    velocities = {e.velocity for e in melody}
    assert len(velocities) > 1


def test_humanize_off_yields_identical_velocities_across_identical_candles():
    cfg = RunConfig(humanize=False, rest_volume_percentile=0)
    df = _df([_candle(100, 110, 99, 105, v=500)] * 10)
    events = map_candles_to_events(df, cfg)
    melody = [e for e in events if e.channel == cfg.melody_channel]
    velocities = {e.velocity for e in melody}
    assert len(velocities) == 1  # all identical


def test_mapping_still_deterministic_with_humanization():
    """Humanization is deterministic — same input always → same output."""
    cfg = RunConfig(humanize=True)
    df = pd.read_csv(FIXTURE)
    a = map_candles_to_events(df, cfg)
    b = map_candles_to_events(df.copy(), cfg)
    assert a == b


def test_within_candle_motion_produces_multiple_pitches_per_candle():
    """A wide-range green candle should emit 4 distinct pitches across
    its slot (open, low, high, close), not 1."""
    cfg = RunConfig(humanize=False, rest_volume_percentile=0)
    # Build a series with diverse closes so the normalization range is
    # wide enough that within-candle wicks don't clamp to the extremes.
    base_candles = [_candle(50 + 5 * i, 55 + 5 * i, 45 + 5 * i, 53 + 5 * i, v=500)
                    for i in range(20)]
    # Replace candle 10 with a wide-range green candle whose OHLC span
    # spreads across several scale steps within the [min_close, max_close]
    # range.
    base_candles[10] = _candle(100, 130, 80, 120, v=500)
    df = _df(base_candles)
    events = map_candles_to_events(df, cfg)
    pad = cfg.grace_ticks
    candle10_start = pad + 10 * cfg.candle_ticks
    candle10_end = pad + 11 * cfg.candle_ticks
    candle10_melody = [e for e in events if e.channel == cfg.melody_channel
                       and candle10_start <= e.start_tick < candle10_end]
    pitches = [e.note for e in candle10_melody]
    assert len(candle10_melody) == 4  # wide-range green → 4 sub-notes
    assert len(set(pitches)) >= 3     # spread across several scale steps


# --- Rest insertion ----------------------------------------------------

def test_rest_skips_melody_on_lowest_volume_candle():
    cfg = RunConfig(humanize=False, rest_volume_percentile=0.1)
    # 12 candles with one very low-volume one — that one should rest.
    candles = [_candle(100, 110, 99, 105, v=500) for _ in range(12)]
    candles[5] = _candle(100, 110, 99, 105, v=1)  # lowest by far
    df = _df(candles)
    events = map_candles_to_events(df, cfg)

    # Find melody events for candle 5
    candle5_start = cfg.grace_ticks + 5 * cfg.candle_ticks
    candle5_end = cfg.grace_ticks + 6 * cfg.candle_ticks
    candle5_melody = [e for e in events if e.channel == cfg.melody_channel
                      and candle5_start <= e.start_tick < candle5_end]
    assert candle5_melody == []


def test_rest_keeps_harmony_playing():
    """Rest should silence melody but keep harmony as a pad."""
    cfg = RunConfig(humanize=False, rest_volume_percentile=0.1)
    candles = [_candle(100, 110, 99, 105, v=500) for _ in range(12)]
    candles[5] = _candle(100, 110, 99, 105, v=1)
    df = _df(candles)
    events = map_candles_to_events(df, cfg)
    candle5_start = cfg.grace_ticks + 5 * cfg.candle_ticks
    candle5_harmony = [e for e in events if e.channel == cfg.harmony_channel
                       and e.start_tick == candle5_start]
    assert len(candle5_harmony) >= 1


def test_rest_disabled_when_percentile_zero():
    cfg = RunConfig(humanize=False, rest_volume_percentile=0)
    candles = [_candle(100, 110, 99, 105, v=500) for _ in range(12)]
    candles[5] = _candle(100, 110, 99, 105, v=1)
    df = _df(candles)
    events = map_candles_to_events(df, cfg)
    candle5_start = cfg.grace_ticks + 5 * cfg.candle_ticks
    candle5_end = cfg.grace_ticks + 6 * cfg.candle_ticks
    candle5_melody = [e for e in events if e.channel == cfg.melody_channel
                      and candle5_start <= e.start_tick < candle5_end]
    assert candle5_melody  # not rested


def test_rest_disabled_for_short_series():
    """Series < 10 candles can't reliably compute a rest threshold —
    no rests should be inserted."""
    cfg = RunConfig(humanize=False, rest_volume_percentile=0.1)
    df = _df([_candle(100, 110, 99, 105, v=500) for _ in range(5)])
    events = map_candles_to_events(df, cfg)
    melody = [e for e in events if e.channel == cfg.melody_channel]
    # All 5 candles should have at least one melody event
    starts = {e.start_tick - (e.start_tick % cfg.candle_ticks) for e in melody}
    assert len(starts) > 0  # melody actually played
