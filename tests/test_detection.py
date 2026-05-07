"""Tests for detection.py — the shared structural-event module.

The mask-returning detectors are already covered indirectly through
test_rubato; here we focus on the new ``detect_structural_events``
labelled API and the kind-disambiguation logic.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from btc_sonify.detection import (
    StructuralEvent,
    detect_structural_events,
    ema_crossovers,
    local_pivots,
    structural_event_mask,
    vol_regime_shifts,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_btc_daily.csv"


def _synthetic_df(prices: list[float]) -> pd.DataFrame:
    n = len(prices)
    closes = np.array(prices, dtype=float)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    return pd.DataFrame({
        "timestamp": pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC"),
        "open": opens,
        "high": closes * 1.01,
        "low": closes * 0.99,
        "close": closes,
        "volume": np.full(n, 1000.0),
    })


# --- Mask detectors basic sanity (legacy parity) ------------------------

def test_local_pivots_finds_high_and_low():
    closes = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 5.0, 4.0, 3.0, 2.0, 1.0])
    mask = local_pivots(closes, window=5)
    # With window=5, only index 5 (the global peak) qualifies because we
    # need 5 candles on each side. The series is too short to flag the
    # endpoints as local minima under this window.
    assert mask[5]
    # No false positives in clear monotone runs.
    assert not mask[2]
    assert not mask[8]


def test_local_pivots_ignores_flat_window():
    closes = np.full(20, 100.0)
    assert not local_pivots(closes).any()


def test_vol_regime_shifts_empty_on_short_series():
    assert not vol_regime_shifts(np.zeros(10)).any()


def test_ema_crossovers_empty_on_short_series():
    assert not ema_crossovers(np.zeros(40)).any()


def test_structural_event_mask_is_union():
    closes = np.linspace(100, 150, 200)
    returns = np.diff(np.log(closes), prepend=np.log(closes[0]))
    mask = structural_event_mask(closes, returns)
    assert mask.dtype == bool
    assert len(mask) == 200


# --- detect_structural_events: schema -----------------------------------

def test_empty_df_returns_empty_list():
    df = pd.DataFrame({"timestamp": [], "open": [], "high": [], "low": [], "close": [], "volume": []})
    assert detect_structural_events(df) == []


def test_events_carry_kind_index_and_label():
    rng = np.random.default_rng(42)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 250)))
    df = _synthetic_df(closes.tolist())
    events = detect_structural_events(df)
    # Should produce at least a handful of events on a noisy 250-candle series.
    assert len(events) > 0
    for e in events:
        assert isinstance(e, StructuralEvent)
        assert 0 <= e.candle_index < len(df)
        assert e.label.startswith(e.kind)
        assert e.kind in {
            "swing_high", "swing_low",
            "vol_spike", "vol_calm",
            "ma_cross_up", "ma_cross_down",
        }


def test_labels_carry_iso_date_when_timestamps_present():
    df = _synthetic_df([100, 101, 102, 103, 104, 105, 100, 95, 90, 85, 80] * 5)
    events = detect_structural_events(df)
    assert events
    for e in events:
        # `<kind>_YYYY-MM-DD` shape
        assert "_2020-" in e.label or "_2021-" in e.label, e.label


def test_labels_fall_back_to_index_when_no_timestamp():
    df = _synthetic_df([100, 101, 102, 103, 104, 105, 100, 95, 90, 85, 80] * 5)
    df = df.drop(columns=["timestamp"])
    events = detect_structural_events(df)
    assert events
    assert all("_idx" in e.label for e in events)


# --- detect_structural_events: kind disambiguation ----------------------

def test_pivot_classified_as_high_at_peak():
    """A clean up-then-down series should yield a swing_high at the peak."""
    df = _synthetic_df(list(range(100, 150, 5)) + list(range(145, 95, -5)))
    events = detect_structural_events(df)
    swing_highs = [e for e in events if e.kind == "swing_high"]
    assert any(e.candle_index == 9 for e in swing_highs), (
        f"Expected swing_high near peak idx 9, got {[(e.kind, e.candle_index) for e in events]}"
    )


def test_pivot_classified_as_low_at_trough():
    df = _synthetic_df(list(range(150, 100, -5)) + list(range(105, 155, 5)))
    events = detect_structural_events(df)
    swing_lows = [e for e in events if e.kind == "swing_low"]
    assert any(e.candle_index == 9 for e in swing_lows)


# --- determinism --------------------------------------------------------

def test_detect_is_deterministic():
    df = pd.read_csv(FIXTURE)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    a = detect_structural_events(df)
    b = detect_structural_events(df)
    assert a == b
