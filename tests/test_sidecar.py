"""Tests for sidecar.py — JSON sidecar emitted alongside every .mid."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from btc_sonify.config import PALETTES, RunConfig
from btc_sonify.sidecar import build_sidecar, write_sidecar
from btc_sonify.symphony import detect_movements, map_symphony


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


def _long_df(seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    bull = 100 * np.exp(np.cumsum(rng.normal(0.01, 0.02, 100)))
    bear = bull[-1] * np.exp(np.cumsum(rng.normal(-0.012, 0.025, 100)))
    return _synthetic_df(np.concatenate([bull, bear]).tolist())


@pytest.fixture
def cfg() -> RunConfig:
    return RunConfig().with_palette(PALETTES["classical"])


def _build(df, cfg, mode: str = "plain", rendered=None) -> dict:
    return build_sidecar(
        df=df,
        base_config=cfg,
        rendered_movements=rendered,
        symbol="BTC/USDT",
        timeframe="1d",
        start="2020-01-01",
        end="2020-07-19",
        exchange="binanceus",
        palette="classical",
        mode=mode,
    )


# --- Schema -------------------------------------------------------------

def test_top_level_keys_match_spec(cfg):
    df = _long_df()
    side = _build(df, cfg)
    assert set(side.keys()) == {"config", "source", "movements", "bars", "events"}


def test_source_carries_run_metadata(cfg):
    df = _long_df()
    side = _build(df, cfg)
    src = side["source"]
    assert src["symbol"] == "BTC/USDT"
    assert src["timeframe"] == "1d"
    assert src["candle_count"] == len(df)


def test_config_block_is_complete(cfg):
    df = _long_df()
    side = _build(df, cfg)
    cfg_block = side["config"]
    # Sample of fields that must round-trip
    assert cfg_block["scale"] == cfg.scale
    assert cfg_block["root"] == cfg.root
    assert cfg_block["bpm"] == cfg.bpm
    # Computed properties surfaced
    assert cfg_block["candle_ticks"] == cfg.candle_ticks
    assert cfg_block["grace_ticks"] == cfg.grace_ticks


# --- Bars ---------------------------------------------------------------

def test_bars_count_matches_candle_count_in_plain_mode(cfg):
    df = _long_df()
    side = _build(df, cfg)
    assert len(side["bars"]) == len(df)


def test_bars_are_one_indexed_and_sequential(cfg):
    df = _long_df()
    side = _build(df, cfg)
    bar_numbers = [b["bar"] for b in side["bars"]]
    assert bar_numbers == list(range(1, len(bar_numbers) + 1))


def test_bar_tick_starts_advance_by_candle_ticks_in_plain_mode(cfg):
    df = _long_df()
    side = _build(df, cfg)
    bars = side["bars"]
    deltas = [
        bars[i + 1]["tick_start"] - bars[i]["tick_start"]
        for i in range(len(bars) - 1)
    ]
    # Every bar advances by exactly candle_ticks in plain mode (no
    # inter-movement rest, no tempo change).
    assert all(d == cfg.candle_ticks for d in deltas)


def test_bar_carries_close_and_date(cfg):
    df = _long_df()
    side = _build(df, cfg)
    first = side["bars"][0]
    assert first["candle_index"] == 0
    assert first["date"] == "2020-01-01"
    assert isinstance(first["close"], float)


# --- Symphony mode ------------------------------------------------------

def test_symphony_bars_carry_movement_index(cfg):
    df = _long_df()
    movements = detect_movements(df)
    _, _, rendered = map_symphony(df, cfg, movements)
    side = _build(df, cfg, mode="symphony", rendered=rendered)

    movement_indices = {b["movement"] for b in side["bars"]}
    assert movement_indices == set(range(len(rendered)))


def test_symphony_movements_block_is_populated(cfg):
    df = _long_df()
    movements = detect_movements(df)
    _, _, rendered = map_symphony(df, cfg, movements)
    side = _build(df, cfg, mode="symphony", rendered=rendered)

    assert len(side["movements"]) == len(rendered)
    for m, r in zip(side["movements"], rendered):
        assert m["index"] == r.movement.index
        assert m["scale"] == r.config.scale
        assert m["bpm"] == r.config.bpm
        assert m["start_idx"] == r.movement.start_idx


# --- Events -------------------------------------------------------------

def test_events_link_back_to_bars(cfg):
    df = _long_df()
    movements = detect_movements(df)
    _, _, rendered = map_symphony(df, cfg, movements)
    side = _build(df, cfg, mode="symphony", rendered=rendered)

    bar_numbers = {b["bar"] for b in side["bars"]}
    for event in side["events"]:
        assert event["bar"] in bar_numbers
        assert event["candle_index"] == side["bars"][event["bar"] - 1]["candle_index"]
        assert event["kind"] in {
            "swing_high", "swing_low",
            "vol_spike", "vol_calm",
            "ma_cross_up", "ma_cross_down",
        }


# --- Determinism --------------------------------------------------------

def test_sidecar_dict_is_deterministic(cfg):
    df = _long_df()
    a = _build(df, cfg)
    b = _build(df, cfg)
    assert a == b


def test_sidecar_bytes_are_deterministic(tmp_path, cfg):
    df = _long_df()
    side = _build(df, cfg)
    p1 = write_sidecar(side, tmp_path / "a.json")
    p2 = write_sidecar(side, tmp_path / "b.json")
    assert p1.read_bytes() == p2.read_bytes()


def test_sidecar_round_trip_loads(tmp_path, cfg):
    """Written file must parse back to a dict equal (modulo float
    rounding artefacts that don't apply because we round before serialising)."""
    df = _long_df()
    side = _build(df, cfg)
    p = write_sidecar(side, tmp_path / "side.json")
    loaded = json.loads(p.read_text())
    assert loaded["source"]["candle_count"] == len(df)
    assert len(loaded["bars"]) == len(df)


# --- Empty edge case ----------------------------------------------------

def test_sidecar_handles_empty_df(cfg):
    df = pd.DataFrame({
        "timestamp": pd.to_datetime([], utc=True),
        "open": [], "high": [], "low": [], "close": [], "volume": [],
    })
    side = _build(df, cfg)
    assert side["bars"] == []
    assert side["events"] == []
    assert side["source"]["candle_count"] == 0
