"""Tests for rubato.py — within-movement tempo breathing."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from btc_sonify.config import PALETTES, RunConfig
from btc_sonify.rubato import compute_rubato_curve, interleave_rubato_markers
from btc_sonify.symphony import detect_movements, map_symphony

FIXTURE = Path(__file__).parent / "fixtures" / "sample_btc_daily.csv"


def _synthetic_df(prices: list[float]) -> pd.DataFrame:
    """OHLCV frame from a list of close prices. Deterministic, just
    enough structure for the rubato signal detectors to run."""
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


@pytest.fixture
def cfg() -> RunConfig:
    return RunConfig().with_palette(PALETTES["classical"])


# --- compute_rubato_curve: edge cases -----------------------------------

def test_empty_df_returns_empty_curve(cfg):
    out = compute_rubato_curve(pd.DataFrame({"close": []}), 120, cfg)
    assert len(out) == 0


def test_single_candle_returns_base_bpm(cfg):
    out = compute_rubato_curve(pd.DataFrame({"close": [100.0]}), 120, cfg)
    assert len(out) == 1
    assert int(out[0]) == 120


def test_flat_series_stays_near_base_bpm(cfg):
    """A flat close series has no pivots, no trend, no vol — curve
    should sit on or near base BPM throughout (vol_pct defaults to 0.5
    which gives zero bias)."""
    df = _synthetic_df([100.0] * 40)
    curve = compute_rubato_curve(df, 120, cfg)
    # All values within one quantize step of base.
    assert np.all(np.abs(curve - 120) <= cfg.rubato_quantize_step)


# --- compute_rubato_curve: bounds ---------------------------------------

def test_curve_respects_min_max_bounds(fixture_df, cfg):
    """Every emitted BPM must fall within
    [base * min_factor, base * max_factor], allowing one quantize-step
    of slack from the rounding."""
    base_bpm = 120
    curve = compute_rubato_curve(fixture_df, base_bpm, cfg)
    lo = base_bpm * cfg.rubato_min_factor - cfg.rubato_quantize_step
    hi = base_bpm * cfg.rubato_max_factor + cfg.rubato_quantize_step
    assert curve.min() >= lo
    assert curve.max() <= hi


# --- compute_rubato_curve: determinism ----------------------------------

def test_curve_is_deterministic(fixture_df, cfg):
    a = compute_rubato_curve(fixture_df, 120, cfg)
    b = compute_rubato_curve(fixture_df, 120, cfg)
    assert np.array_equal(a, b)


# --- compute_rubato_curve: shape around a pivot -------------------------

def test_curve_dips_near_a_clear_pivot(cfg):
    """A symmetric up-then-down series has a single sharp pivot at
    index 10. The smoothed rubato curve should sit *lower* in the
    near-pivot window than in the trending stretches before/after."""
    rising = list(range(100, 151, 5))           # 11 candles, 100..150
    falling = list(range(145, 94, -5))          # 11 candles, 145..95
    df = _synthetic_df(rising + falling)
    curve = compute_rubato_curve(df, 120, cfg)

    near_pivot = curve[8:13].mean()             # straddles the peak at idx 10
    early_trend = curve[2:6].mean()             # mid of the rising run
    late_trend = curve[16:20].mean()            # mid of the falling run

    assert near_pivot < early_trend, (
        f"Rubato should slow into the pivot; "
        f"near-pivot mean {near_pivot} not lower than early-trend mean {early_trend}"
    )
    assert near_pivot < late_trend


# --- compute_rubato_curve: quantization ---------------------------------

def test_curve_values_are_quantized(fixture_df, cfg):
    """Every emitted BPM is an exact multiple of the quantize step."""
    curve = compute_rubato_curve(fixture_df, 120, cfg)
    step = cfg.rubato_quantize_step
    assert np.all(curve % step == 0)


# --- interleave_rubato_markers: integration with map_symphony -----------

def _build_long_synthetic_df(seed: int = 0) -> pd.DataFrame:
    """Synthesise ~200 candles with two distinct movements so symphony
    detection produces something interesting. Deterministic via seeded
    numpy."""
    rng = np.random.default_rng(seed)
    bull = 100 * np.exp(np.cumsum(rng.normal(0.01, 0.02, 100)))
    bear = bull[-1] * np.exp(np.cumsum(rng.normal(-0.012, 0.025, 100)))
    closes = np.concatenate([bull, bear])
    return _synthetic_df(closes.tolist())


def test_rubato_off_emits_one_marker_per_movement(cfg):
    """With rubato disabled, map_symphony should emit exactly one
    TempoMarker per movement — the existing v1.0 behaviour, untouched."""
    df = _build_long_synthetic_df()
    cfg_off = RunConfig(**{**cfg.__dict__, "rubato": False})
    movements = detect_movements(df)
    assert movements, "synthetic data should produce >=1 movement"

    _, tempo_markers, _ = map_symphony(df, cfg_off, movements)
    assert len(tempo_markers) == len(movements)
    for marker, mov in zip(tempo_markers, movements):
        assert marker.label == mov.label


def test_rubato_on_emits_more_markers_than_movements(cfg):
    """With rubato enabled, the meta track gains additional within-
    movement breathing markers on top of the per-movement headlines."""
    df = _build_long_synthetic_df()
    movements = detect_movements(df)
    assert movements

    _, tempo_markers, _ = map_symphony(df, cfg, movements)
    assert len(tempo_markers) > len(movements), (
        "rubato should add breathing markers within at least one movement"
    )
    # Headline (labelled) marker count still equals movement count.
    labelled = [m for m in tempo_markers if m.label]
    assert len(labelled) == len(movements)


def test_rubato_markers_are_sorted_by_tick(cfg):
    df = _build_long_synthetic_df()
    movements = detect_movements(df)
    _, tempo_markers, _ = map_symphony(df, cfg, movements)
    ticks = [m.tick for m in tempo_markers]
    assert ticks == sorted(ticks)


def test_rubato_markers_are_quantized_and_distinct(cfg):
    """Adjacent rubato markers (within the same movement) must differ
    by at least one quantize step — that's the point of the bucketing."""
    df = _build_long_synthetic_df()
    movements = detect_movements(df)
    _, tempo_markers, _ = map_symphony(df, cfg, movements)

    # Group markers by movement (between consecutive headline markers).
    groups: list[list] = []
    current: list = []
    for m in tempo_markers:
        if m.label and current:
            groups.append(current)
            current = []
        current.append(m)
    if current:
        groups.append(current)

    step = cfg.rubato_quantize_step
    for group in groups:
        bpms = [m.bpm for m in group]
        for a, b in zip(bpms, bpms[1:]):
            assert a != b, "consecutive markers should never have equal BPM"
            assert abs(a - b) % step == 0 or abs(a - b) >= step, (
                f"adjacent BPM jump {a}→{b} is below the quantize step {step}"
            )


def test_rubato_pipeline_is_deterministic(cfg):
    df = _build_long_synthetic_df()
    movements = detect_movements(df)
    _, m1, _ = map_symphony(df, cfg, movements)
    _, m2, _ = map_symphony(df, cfg, movements)
    assert [(x.tick, x.bpm, x.label) for x in m1] == [
        (x.tick, x.bpm, x.label) for x in m2
    ]


# --- Palette default propagation ----------------------------------------

def test_palette_propagates_rubato_default():
    base = RunConfig()
    assert base.with_palette(PALETTES["classical"]).rubato is True
    assert base.with_palette(PALETTES["cinematic"]).rubato is True
    assert base.with_palette(PALETTES["synthwave"]).rubato is False
    assert base.with_palette(PALETTES["electronic"]).rubato is False


def test_with_rubato_override():
    cfg = RunConfig().with_palette(PALETTES["synthwave"])
    assert cfg.rubato is False
    assert cfg.with_rubato(True).rubato is True
    assert cfg.with_rubato(True).with_rubato(False).rubato is False
