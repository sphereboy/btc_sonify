"""Tests for structural-marker emission in the symphony pipeline + writer.

Two layers:
1. ``compute_structural_markers`` translates global structural events
   into ``(tick, label)`` pairs anchored to the right movement.
2. ``write_midi(markers=...)`` writes those as MIDI ``marker`` meta
   events on the meta track without disturbing tempo changes.
"""
from __future__ import annotations

from pathlib import Path

import mido
import numpy as np
import pandas as pd
import pytest

from btc_sonify.config import PALETTES, RunConfig
from btc_sonify.mapping import MidiEvent
from btc_sonify.midi_writer import StructuralMarker, TempoChange, write_midi
from btc_sonify.symphony import (
    compute_structural_markers,
    detect_movements,
    map_symphony,
)


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


def _long_synthetic_df(seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    bull = 100 * np.exp(np.cumsum(rng.normal(0.01, 0.02, 100)))
    bear = bull[-1] * np.exp(np.cumsum(rng.normal(-0.012, 0.025, 100)))
    return _synthetic_df(np.concatenate([bull, bear]).tolist())


@pytest.fixture
def cfg() -> RunConfig:
    return RunConfig().with_palette(PALETTES["classical"])


# --- compute_structural_markers ----------------------------------------

def test_no_movements_returns_empty(cfg):
    df = _long_synthetic_df()
    assert compute_structural_markers(df, [], cfg) == []


def test_markers_anchor_inside_movement_tick_window(cfg):
    df = _long_synthetic_df()
    movements = detect_movements(df)
    _, _, rendered = map_symphony(df, cfg, movements)
    pairs = compute_structural_markers(df, rendered, cfg)
    assert pairs, "synthetic data should produce at least one structural marker"

    # Every marker tick must fall in the [tick_offset, tick_offset + (n-1)*ticks]
    # window of some movement, plus the leading grace pad.
    for tick, _ in pairs:
        in_window = False
        for r in rendered:
            mov_start = r.tick_offset + cfg.grace_ticks
            mov_n = r.movement.end_idx - r.movement.start_idx + 1
            mov_end = mov_start + (mov_n - 1) * cfg.candle_ticks
            if mov_start <= tick <= mov_end:
                in_window = True
                break
        assert in_window, f"tick {tick} not inside any movement"


def test_markers_carry_dated_labels(cfg):
    df = _long_synthetic_df()
    movements = detect_movements(df)
    _, _, rendered = map_symphony(df, cfg, movements)
    pairs = compute_structural_markers(df, rendered, cfg)
    for _, label in pairs:
        assert label.startswith((
            "swing_high", "swing_low",
            "vol_spike", "vol_calm",
            "ma_cross_up", "ma_cross_down",
        ))
        # Date suffix should be present on a real OHLCV frame with timestamps.
        assert "_2020-" in label or "_2021-" in label, label


def test_compute_structural_markers_is_deterministic(cfg):
    df = _long_synthetic_df()
    movements = detect_movements(df)
    _, _, rendered = map_symphony(df, cfg, movements)
    a = compute_structural_markers(df, rendered, cfg)
    b = compute_structural_markers(df, rendered, cfg)
    assert a == b


# --- write_midi(markers=...) -------------------------------------------

def _read_meta_messages(path: Path) -> list[mido.MetaMessage]:
    mid = mido.MidiFile(path)
    return [m for m in mid.tracks[0] if isinstance(m, mido.MetaMessage)]


def test_markers_appear_on_meta_track(tmp_path, cfg):
    out = tmp_path / "out.mid"
    events = [MidiEvent(0, 60, 100, 0, 480)]
    write_midi(
        events, out, cfg,
        tempo_changes=[TempoChange(tick=0, bpm=120, label="I.")],
        markers=[
            StructuralMarker(tick=480, label="swing_low_2020-03-12"),
            StructuralMarker(tick=1440, label="ma_cross_up_2020-04-01"),
        ],
    )
    metas = _read_meta_messages(out)
    marker_texts = [m.text for m in metas if m.type == "marker"]
    assert "swing_low_2020-03-12" in marker_texts
    assert "ma_cross_up_2020-04-01" in marker_texts
    # Headline marker still there.
    assert "I." in marker_texts


def test_markers_do_not_alter_tempo(tmp_path, cfg):
    """Writing only markers — no tempo changes — should fall back to the
    config's base BPM as the single tempo event."""
    out = tmp_path / "out.mid"
    events = [MidiEvent(0, 60, 100, 0, 480)]
    write_midi(
        events, out, cfg,
        tempo_changes=None,
        markers=[StructuralMarker(tick=480, label="swing_low_test")],
    )
    metas = _read_meta_messages(out)
    tempos = [m for m in metas if m.type == "set_tempo"]
    assert len(tempos) == 1
    assert mido.tempo2bpm(tempos[0].tempo) == pytest.approx(cfg.bpm, abs=0.5)


def test_marker_at_same_tick_as_tempo_orders_tempo_first(tmp_path, cfg):
    """When a structural marker collides with a tempo change at the
    same tick, tempo_change comes first so DAWs see the tempo before
    the label."""
    out = tmp_path / "out.mid"
    events = [MidiEvent(0, 60, 100, 0, 480)]
    write_midi(
        events, out, cfg,
        tempo_changes=[TempoChange(tick=480, bpm=140, label="II.")],
        markers=[StructuralMarker(tick=480, label="vol_spike_now")],
    )
    metas = list(mido.MidiFile(out).tracks[0])
    # Find the index of the set_tempo at tick 480 (delta 480) and confirm
    # the next marker meta is the headline 'II.' before the structural one.
    tempo_indices = [i for i, m in enumerate(metas) if m.type == "set_tempo"]
    assert tempo_indices
    # Walk after the last tempo: expect 'II.' marker, then 'vol_spike_now'.
    seen: list[str] = []
    for m in metas[tempo_indices[-1]:]:
        if m.type == "marker":
            seen.append(m.text)
    assert seen == ["II.", "vol_spike_now"]


def test_full_symphony_pipeline_writes_markers(tmp_path, cfg):
    """End-to-end: symphony pipeline produces structural markers and
    write_midi serialises them."""
    df = _long_synthetic_df()
    movements = detect_movements(df)
    events, tempo_markers, rendered = map_symphony(df, cfg, movements)
    pairs = compute_structural_markers(df, rendered, cfg)
    assert pairs

    tempo_changes = [
        TempoChange(tick=t.tick, bpm=t.bpm, label=t.label) for t in tempo_markers
    ]
    structural_markers = [
        StructuralMarker(tick=t, label=label) for t, label in pairs
    ]
    out = tmp_path / "symphony.mid"
    write_midi(
        events, out, cfg,
        tempo_changes=tempo_changes,
        markers=structural_markers,
    )
    metas = _read_meta_messages(out)
    marker_texts = {m.text for m in metas if m.type == "marker"}

    expected_kinds_present = sum(
        1 for label in marker_texts
        if any(label.startswith(k) for k in (
            "swing_high", "swing_low", "vol_spike", "vol_calm",
            "ma_cross_up", "ma_cross_down",
        ))
    )
    assert expected_kinds_present > 0
