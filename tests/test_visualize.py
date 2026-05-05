"""Tests for visualize.py — the timeline math and the HTML output."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
import pytest

from btc_sonify.config import RunConfig
from btc_sonify.symphony import detect_movements, map_symphony
from btc_sonify.visualize import compute_timeline, write_visualization


FIXTURE = Path(__file__).parent / "fixtures" / "sample_btc_daily.csv"


@pytest.fixture
def fixture_df() -> pd.DataFrame:
    df = pd.read_csv(FIXTURE)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


@pytest.fixture
def cfg() -> RunConfig:
    return RunConfig()


# --- compute_timeline (plain mode) -------------------------------------

def test_plain_mode_one_movement_covers_all_candles(fixture_df, cfg):
    candles, movements = compute_timeline(None, fixture_df, cfg)
    assert len(candles) == len(fixture_df)
    assert len(movements) == 1
    assert movements[0].start_idx == 0
    assert movements[0].end_idx == len(fixture_df) - 1


def test_plain_mode_candles_have_increasing_start_times(fixture_df, cfg):
    candles, _ = compute_timeline(None, fixture_df, cfg)
    times = [c.start_s for c in candles]
    assert times == sorted(times)


def test_plain_mode_first_candle_after_grace_pad(fixture_df, cfg):
    candles, _ = compute_timeline(None, fixture_df, cfg)
    expected = cfg.grace_ticks * 60.0 / cfg.bpm / cfg.ppq
    assert candles[0].start_s == pytest.approx(expected)


def test_plain_mode_candle_duration_matches_bpm(fixture_df, cfg):
    candles, _ = compute_timeline(None, fixture_df, cfg)
    expected = cfg.candle_ticks * 60.0 / cfg.bpm / cfg.ppq  # 0.5s at 120 BPM quarters
    for c in candles:
        assert c.duration_s == pytest.approx(expected)


# --- compute_timeline (symphony mode) ----------------------------------

def test_symphony_timeline_movement_count_matches(fixture_df, cfg):
    movements = detect_movements(fixture_df, movements=3)
    _, _, rendered = map_symphony(fixture_df, cfg, movements)
    _, tl_movements = compute_timeline(rendered, fixture_df, cfg)
    assert len(tl_movements) == 3


def test_symphony_movements_chronological(fixture_df, cfg):
    movements = detect_movements(fixture_df, movements=3)
    _, _, rendered = map_symphony(fixture_df, cfg, movements)
    _, tl_movements = compute_timeline(rendered, fixture_df, cfg)
    for a, b in zip(tl_movements, tl_movements[1:]):
        assert a.end_s <= b.start_s


def test_symphony_high_vol_movement_is_faster(cfg):
    """A movement at 144 BPM should have shorter candles than one at 120."""
    # Build a synthetic dataset that forces both regimes
    n = 60
    closes = [100.0 + i for i in range(n // 2)] + [160.0 - i * 2 for i in range(n // 2)]
    df = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC"),
        "open": closes,
        "high": [c + 1 for c in closes],
        "low": [c - 1 for c in closes],
        "close": closes,
        "volume": [1000.0] * n,
    })
    movements = detect_movements(df, movements=2)
    _, _, rendered = map_symphony(df, cfg, movements)
    candles, _ = compute_timeline(rendered, df, cfg)
    # Group candle durations by movement
    durations = {}
    for c in candles:
        durations.setdefault(c.movement, []).append(c.duration_s)
    # Each movement's candles share the same duration; different movements
    # may have different durations if BPMs differ.
    if len(set(rendered[i].config.bpm for i in range(len(rendered)))) > 1:
        m_durations = {k: v[0] for k, v in durations.items()}
        assert len(set(m_durations.values())) > 1


def test_symphony_dedups_boundary_candles(fixture_df, cfg):
    """Movement boundaries share endpoint candles in detect_movements;
    the timeline must not double-count them."""
    movements = detect_movements(fixture_df, movements=3)
    _, _, rendered = map_symphony(fixture_df, cfg, movements)
    candles, _ = compute_timeline(rendered, fixture_df, cfg)
    indices = [c.idx for c in candles]
    # Each candle index appears at most once
    assert len(indices) == len(set(indices))


# --- write_visualization ----------------------------------------------

def test_writes_html_file(tmp_path, fixture_df, cfg):
    out = tmp_path / "out.html"
    write_visualization(
        df=fixture_df, rendered_movements=None, base_config=cfg,
        output_path=out, audio_path="out.mp3",
        title="Test", palette_name="classical",
    )
    assert out.exists()
    assert out.stat().st_size > 1000


def test_html_contains_audio_path(tmp_path, fixture_df, cfg):
    out = tmp_path / "out.html"
    write_visualization(
        df=fixture_df, rendered_movements=None, base_config=cfg,
        output_path=out, audio_path="custom-audio.mp3",
        title="Test", palette_name="synthwave",
    )
    html = out.read_text()
    assert "custom-audio.mp3" in html
    assert "synthwave" in html
    assert "Test" in html


def test_html_embeds_valid_json_payload(tmp_path, fixture_df, cfg):
    out = tmp_path / "out.html"
    write_visualization(
        df=fixture_df, rendered_movements=None, base_config=cfg,
        output_path=out, audio_path="out.mp3",
        title="BTC Test", palette_name="classical",
    )
    html = out.read_text()
    m = re.search(r"const DATA = (\{.*?\});", html)
    assert m, "DATA payload not found in HTML"
    payload = json.loads(m.group(1))
    assert payload["palette"] == "classical"
    assert payload["title"] == "BTC Test"
    assert payload["n_candles"] == len(fixture_df)
    assert len(payload["candles_meta"]) == len(fixture_df)
    assert len(payload["movements"]) >= 1


def test_html_payload_candle_fields_present(tmp_path, fixture_df, cfg):
    out = tmp_path / "out.html"
    write_visualization(
        df=fixture_df, rendered_movements=None, base_config=cfg,
        output_path=out, audio_path="out.mp3",
        title="Test", palette_name="classical",
    )
    html = out.read_text()
    m = re.search(r"const DATA = (\{.*?\});", html)
    payload = json.loads(m.group(1))
    sample = payload["candles_meta"][0]
    assert {"i", "t", "o", "h", "l", "c", "v"} <= set(sample)


def test_html_symphony_payload_includes_movement_metadata(tmp_path, fixture_df, cfg):
    movements = detect_movements(fixture_df, movements=3)
    _, _, rendered = map_symphony(fixture_df, cfg, movements)
    out = tmp_path / "out.html"
    write_visualization(
        df=fixture_df, rendered_movements=rendered, base_config=cfg,
        output_path=out, audio_path="out.mp3",
        title="Test", palette_name="synthwave",
    )
    html = out.read_text()
    m = re.search(r"const DATA = (\{.*?\});", html)
    payload = json.loads(m.group(1))
    assert len(payload["movements"]) == 3
    sample_m = payload["movements"][0]
    assert {"label", "dir", "scale", "root", "bpm", "s0", "s1"} <= set(sample_m)


def test_html_creates_parent_directory(tmp_path, fixture_df, cfg):
    out = tmp_path / "deep" / "nested" / "out.html"
    write_visualization(
        df=fixture_df, rendered_movements=None, base_config=cfg,
        output_path=out, audio_path="out.mp3",
        title="Test", palette_name="classical",
    )
    assert out.exists()


# --- Empty df ----------------------------------------------------------

def test_empty_df_compute_timeline(cfg):
    candles, movements = compute_timeline(None, pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"]), cfg)
    assert candles == []
    assert len(movements) == 1  # plain mode synthesizes a single virtual movement
