"""Tests for percussion.py — drum-channel mapping."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from btc_sonify.config import (
    DRUM_CRASH,
    DRUM_HI_HAT_CLOSED,
    DRUM_KICK,
    DRUM_RIDE_BELL,
    DRUM_SNARE,
    GM_DRUM_CHANNEL,
    RunConfig,
)
from btc_sonify.percussion import MovementOffset, map_percussion


FIXTURE = Path(__file__).parent / "fixtures" / "sample_btc_daily.csv"


def _df(candles: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(candles)
    df["timestamp"] = pd.date_range("2024-01-01", periods=len(df), freq="D", tz="UTC")
    return df[["timestamp", "open", "high", "low", "close", "volume"]]


def _candle(o, h, l, c, v=1000.0):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


@pytest.fixture
def cfg() -> RunConfig:
    return RunConfig()


@pytest.fixture
def fixture_df() -> pd.DataFrame:
    df = pd.read_csv(FIXTURE)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


# --- Channel + structural ----------------------------------------------

def test_empty_input_returns_empty(cfg):
    assert map_percussion(pd.DataFrame(), cfg) == []


def test_all_events_on_drum_channel(cfg, fixture_df):
    events = map_percussion(fixture_df, cfg)
    assert all(e.channel == GM_DRUM_CHANNEL for e in events)


def test_only_drum_kit_notes_used(cfg, fixture_df):
    events = map_percussion(fixture_df, cfg)
    expected = {DRUM_KICK, DRUM_SNARE, DRUM_HI_HAT_CLOSED, DRUM_CRASH, DRUM_RIDE_BELL}
    assert {e.note for e in events} <= expected


# --- Hi-hat heartbeat --------------------------------------------------

def test_hi_hat_fires_on_every_candle(cfg):
    df = _df([_candle(100, 101, 99, 100, v=500) for _ in range(10)])
    events = map_percussion(df, cfg)
    hat_events = [e for e in events if e.note == DRUM_HI_HAT_CLOSED]
    assert len(hat_events) == 10


# --- Kick on top-decile volume -----------------------------------------

def test_kick_only_fires_on_high_volume_candles(cfg):
    """9 quiet candles + 1 loud one — kick should fire exactly once."""
    df = _df([_candle(100, 101, 99, 100, v=10) for _ in range(9)] +
             [_candle(100, 101, 99, 100, v=10000)])
    events = map_percussion(df, cfg)
    kick_events = [e for e in events if e.note == DRUM_KICK]
    # Top decile of 10 = the loudest one.
    assert len(kick_events) == 1


def test_kick_count_proportional_to_candle_count(cfg, fixture_df):
    """30-candle fixture: kicks land on top 10% (~3 candles)."""
    events = map_percussion(fixture_df, cfg)
    kicks = [e for e in events if e.note == DRUM_KICK]
    assert 1 <= len(kicks) <= 5


# --- Snare on top-decile range -----------------------------------------

def test_snare_only_fires_on_wide_range_candles(cfg):
    df = _df([_candle(100, 100.5, 99.5, 100, v=500) for _ in range(9)] +
             [_candle(100, 200, 50, 100, v=500)])
    events = map_percussion(df, cfg)
    snare_events = [e for e in events if e.note == DRUM_SNARE]
    assert len(snare_events) == 1


# --- Ride bell on dojis ------------------------------------------------

def test_ride_bell_fires_on_doji_only(cfg):
    df = _df([
        _candle(100, 110, 90, 100, v=500),  # doji (close == open)
        _candle(100, 110, 90, 109, v=500),  # not doji
    ])
    events = map_percussion(df, cfg)
    ride = [e for e in events if e.note == DRUM_RIDE_BELL]
    assert len(ride) == 1


# --- Crash at movement boundaries --------------------------------------

def test_no_crash_without_movement_offsets(cfg, fixture_df):
    events = map_percussion(fixture_df, cfg)
    crashes = [e for e in events if e.note == DRUM_CRASH]
    assert len(crashes) == 0


def test_crash_fires_at_movement_starts_except_first(cfg):
    df = _df([_candle(100, 101, 99, 100, v=500) for _ in range(15)])
    offsets = [
        MovementOffset(start_idx=0, end_idx=4, tick_offset=0),
        MovementOffset(start_idx=5, end_idx=9,
                       tick_offset=5 * cfg.candle_ticks + cfg.ppq),
        MovementOffset(start_idx=10, end_idx=14,
                       tick_offset=10 * cfg.candle_ticks + 2 * cfg.ppq),
    ]
    events = map_percussion(df, cfg, movement_offsets=offsets)
    crashes = [e for e in events if e.note == DRUM_CRASH]
    assert len(crashes) == 2  # at start of movements 2 and 3, not 1


def test_crash_aligns_with_movement_start_tick(cfg):
    df = _df([_candle(100, 101, 99, 100, v=500) for _ in range(15)])
    offsets = [
        MovementOffset(start_idx=0, end_idx=4, tick_offset=0),
        MovementOffset(start_idx=5, end_idx=14, tick_offset=10000),
    ]
    events = map_percussion(df, cfg, movement_offsets=offsets)
    crash = next(e for e in events if e.note == DRUM_CRASH)
    # Crash should be at second movement's first candle = tick_offset + pad
    assert crash.start_tick == 10000 + cfg.grace_ticks


# --- Velocity scaling --------------------------------------------------

def test_drum_velocities_below_melody_max(cfg, fixture_df):
    """Drums shouldn't bulldoze the melody — all velocities <= velocity_max."""
    events = map_percussion(fixture_df, cfg)
    assert all(cfg.velocity_min <= e.velocity <= cfg.velocity_max for e in events)


def test_hi_hat_quieter_than_kick_and_snare(cfg, fixture_df):
    events = map_percussion(fixture_df, cfg)
    hat = [e.velocity for e in events if e.note == DRUM_HI_HAT_CLOSED]
    kick = [e.velocity for e in events if e.note == DRUM_KICK]
    if hat and kick:
        assert max(hat) < min(kick)


# --- Determinism -------------------------------------------------------

def test_percussion_is_deterministic(cfg, fixture_df):
    a = map_percussion(fixture_df, cfg)
    b = map_percussion(fixture_df.copy(), cfg)
    assert a == b


# --- Lifted-config regression ------------------------------------------

def test_lower_drum_volume_decile_fires_more_kicks(fixture_df):
    """The 5 percussion knobs were lifted from module constants into
    RunConfig so palettes can tune drum density. Smoke-test the lift:
    lowering ``drum_volume_decile`` from the default 0.90 to 0.50 must
    fire strictly more kicks (more candles cross the lower threshold).
    """
    from dataclasses import replace as _replace

    default = RunConfig()
    loose = _replace(default, drum_volume_decile=0.50)

    default_kicks = [
        e for e in map_percussion(fixture_df, default) if e.note == DRUM_KICK
    ]
    loose_kicks = [
        e for e in map_percussion(fixture_df, loose) if e.note == DRUM_KICK
    ]
    assert len(loose_kicks) > len(default_kicks), (
        f"Expected more kicks with lower volume decile; "
        f"got default={len(default_kicks)}, loose={len(loose_kicks)}"
    )


def test_drum_velocity_factor_scales_kick_velocity(fixture_df):
    """Lifting drum_velocity_factor onto config means a palette can
    push drums forward in the mix. Verify: doubling the factor raises
    kick velocity."""
    from dataclasses import replace as _replace

    quiet = _replace(RunConfig(), drum_velocity_factor=0.30)
    loud = _replace(RunConfig(), drum_velocity_factor=0.80)
    quiet_kicks = [
        e.velocity for e in map_percussion(fixture_df, quiet) if e.note == DRUM_KICK
    ]
    loud_kicks = [
        e.velocity for e in map_percussion(fixture_df, loud) if e.note == DRUM_KICK
    ]
    if quiet_kicks and loud_kicks:
        assert max(loud_kicks) > max(quiet_kicks)
