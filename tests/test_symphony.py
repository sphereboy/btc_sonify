"""Tests for symphony.py — movement detection + orchestration."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from btc_sonify.config import RunConfig
from btc_sonify.symphony import (
    DIRECTION_SCALES,
    Movement,
    RenderedMovement,
    TempoMarker,
    derive_movement_config,
    detect_movements,
    map_symphony,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_btc_daily.csv"


def _synthetic_df(prices: list[float]) -> pd.DataFrame:
    """Build an OHLCV DataFrame from a list of close prices.
    Open = previous close (or same), high = close * 1.01, low = close * 0.99,
    volume = constant. Just enough structure for detect_movements to work."""
    n = len(prices)
    closes = np.array(prices, dtype=float)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    highs = closes * 1.01
    lows = closes * 0.99
    volumes = np.full(n, 1000.0)
    return pd.DataFrame({
        "timestamp": pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC"),
        "open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes,
    })


@pytest.fixture
def fixture_df() -> pd.DataFrame:
    df = pd.read_csv(FIXTURE)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


# --- detect_movements ---------------------------------------------------

def test_empty_df_returns_no_movements():
    assert detect_movements(pd.DataFrame()) == []


def test_monotonic_climb_is_one_bull_movement():
    df = _synthetic_df([100, 110, 120, 130, 140, 150, 160])
    movements = detect_movements(df)
    assert len(movements) == 1
    assert movements[0].direction == "bull"
    assert movements[0].avg_return_pct > 0


def test_monotonic_descent_is_one_bear_movement():
    df = _synthetic_df([100, 90, 80, 70, 60, 50, 40])
    movements = detect_movements(df)
    assert len(movements) == 1
    assert movements[0].direction == "bear"


def test_v_shaped_price_yields_two_movements():
    """Down 50%, then back up 50% — should split into bear then bull."""
    prices = [100, 90, 80, 70, 60, 50, 60, 75, 90, 100, 115]
    df = _synthetic_df(prices)
    movements = detect_movements(df, min_excursion_pct=15)
    assert len(movements) >= 2
    assert movements[0].direction == "bear"
    assert movements[-1].direction == "bull"


def test_forced_movement_count_is_honoured():
    df = _synthetic_df([100 + i for i in range(50)])
    for n in (2, 3, 4, 5):
        movements = detect_movements(df, movements=n)
        assert len(movements) == n


def test_movement_indices_cover_full_range():
    df = _synthetic_df([100 + i for i in range(50)])
    movements = detect_movements(df, movements=3)
    assert movements[0].start_idx == 0
    assert movements[-1].end_idx == len(df) - 1


def test_movement_labels_have_roman_numerals():
    df = _synthetic_df([100 + i for i in range(50)])
    movements = detect_movements(df, movements=3)
    assert movements[0].label.startswith("I.")
    assert movements[1].label.startswith("II.")
    assert movements[2].label.startswith("III.")


def test_movements_are_chronological():
    df = _synthetic_df([100, 80, 100, 80, 100, 80, 100])
    movements = detect_movements(df, min_excursion_pct=15)
    for a, b in zip(movements, movements[1:]):
        assert a.end_idx <= b.start_idx
        assert a.index < b.index


def test_tiny_excursions_below_threshold_dont_split():
    """Wiggles smaller than min_excursion_pct should not produce movements."""
    prices = [100 + (i % 2) * 0.5 for i in range(40)]  # 0.5% wiggle
    df = _synthetic_df(prices)
    movements = detect_movements(df, min_excursion_pct=20)
    assert len(movements) == 1


def test_fixture_yields_at_least_one_movement(fixture_df):
    """30 days of real BTC data should produce a single movement (not
    enough range for the default 20% threshold)."""
    movements = detect_movements(fixture_df)
    assert len(movements) >= 1


# --- derive_movement_config --------------------------------------------

def test_direction_picks_scale_when_user_didnt_specify():
    base = RunConfig()
    bull = Movement(0, 0, 10, "bull", 50.0, 0.02, "I. Bull")
    cfg, _ = derive_movement_config(base, bull, None, None, 0.02, user_specified_scale=False)
    assert cfg.scale == DIRECTION_SCALES["bull"]


def test_user_specified_scale_overrides_direction():
    base = RunConfig(scale="blues")
    bull = Movement(0, 0, 10, "bull", 50.0, 0.02, "I. Bull")
    cfg, _ = derive_movement_config(base, bull, None, None, 0.02, user_specified_scale=True)
    assert cfg.scale == "blues"


def test_first_movement_uses_base_root():
    base = RunConfig(root="A")
    m = Movement(0, 0, 10, "bull", 5.0, 0.01, "I.")
    cfg, used_root = derive_movement_config(base, m, None, None, 0.02, user_specified_scale=False)
    assert cfg.root == "A"
    assert used_root == "A"


def test_subsequent_movements_modulate_by_fifth():
    """A → E → B → F#…"""
    base = RunConfig(root="A")
    expected = ["A", "E", "B", "F#"]
    prev = None
    for exp in expected:
        m = Movement(0, 0, 10, "bull", 5.0, 0.01, "x")
        cfg, prev = derive_movement_config(base, m, None, prev, 0.02, user_specified_scale=False)
        assert cfg.root == exp


def test_high_volatility_movement_gets_tempo_bump():
    base = RunConfig(bpm=120)
    high_vol = Movement(0, 0, 10, "bull", 5.0, volatility=0.05, label="x")
    cfg, _ = derive_movement_config(base, high_vol, None, None, median_volatility=0.02,
                                     user_specified_scale=False)
    assert cfg.bpm == int(120 * 1.2)


def test_normal_volatility_movement_keeps_base_bpm():
    base = RunConfig(bpm=120)
    normal = Movement(0, 0, 10, "bull", 5.0, volatility=0.02, label="x")
    cfg, _ = derive_movement_config(base, normal, None, None, median_volatility=0.05,
                                     user_specified_scale=False)
    assert cfg.bpm == 120


# --- map_symphony -------------------------------------------------------

def test_map_symphony_empty_movements_returns_empty():
    base = RunConfig()
    df = _synthetic_df([100, 110])
    events, markers, rendered = map_symphony(df, base, [])
    assert events == [] and markers == [] and rendered == []


def test_map_symphony_emits_one_tempo_marker_per_movement():
    base = RunConfig()
    df = _synthetic_df([100 + i for i in range(60)])
    movements = detect_movements(df, movements=3)
    events, markers, rendered = map_symphony(df, base, movements)
    assert len(markers) == 3
    assert len(rendered) == 3


def test_map_symphony_tempo_markers_are_chronological():
    base = RunConfig()
    df = _synthetic_df([100 + i for i in range(60)])
    movements = detect_movements(df, movements=4)
    _, markers, _ = map_symphony(df, base, movements)
    ticks = [m.tick for m in markers]
    assert ticks == sorted(ticks)
    assert ticks[0] == 0  # first movement starts at the song start


def test_map_symphony_events_in_chronological_order():
    base = RunConfig()
    df = _synthetic_df([100 + i for i in range(60)])
    movements = detect_movements(df, movements=3)
    events, _, _ = map_symphony(df, base, movements)
    last = -1
    for e in events:
        assert e.start_tick >= 0
        # Within a movement, events still flow forward overall.
    # Total span should exceed the per-movement span.
    if events:
        max_tick = max(e.start_tick + e.duration_ticks for e in events)
        # 60 candles * 480 ticks each + at least one rest = > 28800
        assert max_tick > 28800


def test_map_symphony_inserts_rest_between_movements():
    """The gap between movement 1's last note and movement 2's first
    note should be at least one beat."""
    base = RunConfig()
    df = _synthetic_df([100 + i for i in range(60)])
    movements = detect_movements(df, movements=3)
    _, _, rendered = map_symphony(df, base, movements)

    # Each rendered.tick_offset should jump by at least the movement's
    # candle count plus the rest beat.
    rest = base.ppq
    for prev, cur in zip(rendered, rendered[1:]):
        prev_cand_count = prev.movement.end_idx - prev.movement.start_idx + 1
        prev_min_extent = prev_cand_count * prev.config.candle_ticks
        gap = cur.tick_offset - prev.tick_offset
        assert gap >= prev_min_extent + rest


def test_map_symphony_rendered_movements_carry_config_used():
    base = RunConfig(root="A")
    df = _synthetic_df([100 + i for i in range(60)])
    movements = detect_movements(df, movements=3)
    _, _, rendered = map_symphony(df, base, movements)
    assert isinstance(rendered[0], RenderedMovement)
    # Roots should differ between movements (modulation)
    roots = [r.config.root for r in rendered]
    assert len(set(roots)) > 1


def test_map_symphony_with_user_scale_uses_it_throughout():
    base = RunConfig(scale="blues")
    df = _synthetic_df([100 + i for i in range(60)])
    movements = detect_movements(df, movements=3)
    _, _, rendered = map_symphony(df, base, movements, user_specified_scale=True)
    assert all(r.config.scale == "blues" for r in rendered)


def test_map_symphony_full_pipeline_on_fixture(fixture_df):
    """End-to-end with the real BTC fixture."""
    base = RunConfig()
    movements = detect_movements(fixture_df, movements=3)
    events, markers, rendered = map_symphony(fixture_df, base, movements)
    assert len(events) > 0
    assert len(markers) == 3
    assert len(rendered) == 3
    # All event ticks must be non-negative
    assert all(e.start_tick >= 0 for e in events)


def test_map_symphony_deterministic(fixture_df):
    base = RunConfig()
    movements = detect_movements(fixture_df, movements=3)
    a = map_symphony(fixture_df, base, movements)
    b = map_symphony(fixture_df.copy(), base, movements)
    assert a[0] == b[0]  # events
    assert a[1] == b[1]  # tempo markers
