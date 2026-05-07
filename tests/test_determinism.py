"""On-disk byte-identical determinism audit.

The sacred invariant: same OHLCV + same RunConfig → byte-identical .mid
and byte-identical sidecar .json. The existing rubato tests cover the
*array* level (tempo curves match across calls), but until now the file
level had no regression net. This module fixes that before the next
wave of features (CC11 expression envelope, palette overrides, voice
leading) lands and compounds the chance of an undetected drift.

Coverage axes:
- Each of the four palettes (palette propagation).
- ``rubato`` ∈ {on, off}.
- ``humanize`` ∈ {on, off} (deterministic-but-non-trivial pseudo-noise).
- ``mode`` ∈ {plain, symphony}.

We don't run the full Cartesian product — the matrix below picks the
representative combinations that catch every axis at least twice.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from btc_sonify.bass import map_bass
from btc_sonify.config import PALETTES, RunConfig
from btc_sonify.mapping import map_candles_to_events
from btc_sonify.midi_writer import (
    StructuralMarker,
    TempoChange,
    write_midi,
    write_midi_stems,
)
from btc_sonify.percussion import MovementOffset, map_percussion
from btc_sonify.sidecar import build_sidecar, write_sidecar
from btc_sonify.symphony import (
    compute_structural_markers,
    detect_movements,
    map_symphony,
)
from btc_sonify.voice import map_voice


def _synthetic_df(n: int = 200, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    bull = 100 * np.exp(np.cumsum(rng.normal(0.01, 0.02, n // 2)))
    bear = bull[-1] * np.exp(np.cumsum(rng.normal(-0.012, 0.025, n - n // 2)))
    closes = np.concatenate([bull, bear])
    opens = np.concatenate([[closes[0]], closes[:-1]])
    return pd.DataFrame({
        "timestamp": pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC"),
        "open": opens,
        "high": closes * 1.01,
        "low": closes * 0.99,
        "close": closes,
        "volume": np.full(n, 1000.0),
    })


def _render_symphony(df: pd.DataFrame, cfg: RunConfig, out: Path) -> tuple[Path, Path]:
    """Run the symphony pipeline and write .mid + .json. Mirrors what
    cli.py does in symphony mode, minus the rich console plumbing."""
    movements = detect_movements(df)
    events, tempo_markers, rendered = map_symphony(df, cfg, movements)
    tempo_changes = [
        TempoChange(tick=t.tick, bpm=t.bpm, label=t.label) for t in tempo_markers
    ]
    structural_markers = [
        StructuralMarker(tick=t, label=label)
        for t, label in compute_structural_markers(df, rendered, cfg)
    ]
    offsets = [
        MovementOffset(
            start_idx=r.movement.start_idx,
            end_idx=r.movement.end_idx,
            tick_offset=r.tick_offset,
            config=r.config,
        )
        for r in rendered
    ]
    percussion = map_percussion(df, cfg, movement_offsets=offsets)
    bass = map_bass(df, cfg, movement_offsets=offsets)
    voice = map_voice(df, cfg, movement_offsets=offsets)
    all_events = events + bass + voice + percussion

    write_midi(
        all_events, out, cfg,
        tempo_changes=tempo_changes,
        markers=structural_markers,
        include_percussion=True,
        include_bass=bool(bass),
        include_voice=bool(voice),
        title="determinism-test",
    )
    sidecar = build_sidecar(
        df=df, base_config=cfg, rendered_movements=rendered,
        symbol="BTC/USDT", timeframe="1d",
        start="2020-01-01", end="2020-07-19",
        exchange="binanceus", palette=cfg.melody_program and "test" or "test",
        mode="symphony",
    )
    sidecar_path = out.with_suffix(".json")
    write_sidecar(sidecar, sidecar_path)
    return out, sidecar_path


def _render_plain(df: pd.DataFrame, cfg: RunConfig, out: Path) -> tuple[Path, Path]:
    """Run the plain (non-symphony) pipeline and write .mid + .json."""
    events = map_candles_to_events(df, cfg)
    bass = map_bass(df, cfg)
    voice = map_voice(df, cfg)
    all_events = events + bass + voice

    write_midi(
        all_events, out, cfg,
        include_bass=bool(bass),
        include_voice=bool(voice),
    )
    sidecar = build_sidecar(
        df=df, base_config=cfg, rendered_movements=None,
        symbol="BTC/USDT", timeframe="1d",
        start="2020-01-01", end="2020-07-19",
        exchange="binanceus", palette="test",
        mode="plain",
    )
    sidecar_path = out.with_suffix(".json")
    write_sidecar(sidecar, sidecar_path)
    return out, sidecar_path


# --- Symphony × palette -------------------------------------------------

@pytest.mark.parametrize("palette_name", list(PALETTES.keys()))
def test_symphony_midi_is_byte_identical_per_palette(tmp_path, palette_name):
    df = _synthetic_df()
    cfg = RunConfig().with_palette(PALETTES[palette_name])

    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    mid_a, json_a = _render_symphony(df, cfg, a / "run.mid")
    mid_b, json_b = _render_symphony(df, cfg, b / "run.mid")

    assert mid_a.read_bytes() == mid_b.read_bytes(), (
        f"{palette_name} palette: symphony .mid bytes differ across runs"
    )
    assert json_a.read_bytes() == json_b.read_bytes(), (
        f"{palette_name} palette: sidecar .json bytes differ across runs"
    )


# --- Symphony × rubato × humanize --------------------------------------

@pytest.mark.parametrize("rubato", [True, False])
@pytest.mark.parametrize("humanize", [True, False])
def test_symphony_byte_identical_under_toggle_combinations(
    tmp_path, rubato, humanize,
):
    df = _synthetic_df()
    base = RunConfig().with_palette(PALETTES["classical"]).with_rubato(rubato)
    cfg = replace(base, humanize=humanize)

    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    mid_a, json_a = _render_symphony(df, cfg, a / "run.mid")
    mid_b, json_b = _render_symphony(df, cfg, b / "run.mid")

    label = f"rubato={rubato} humanize={humanize}"
    assert mid_a.read_bytes() == mid_b.read_bytes(), f"{label}: .mid bytes differ"
    assert json_a.read_bytes() == json_b.read_bytes(), f"{label}: sidecar bytes differ"


# --- Plain mode ---------------------------------------------------------

def test_plain_mode_byte_identical(tmp_path):
    df = _synthetic_df()
    cfg = RunConfig().with_palette(PALETTES["classical"])

    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    mid_a, json_a = _render_plain(df, cfg, a / "plain.mid")
    mid_b, json_b = _render_plain(df, cfg, b / "plain.mid")

    assert mid_a.read_bytes() == mid_b.read_bytes()
    assert json_a.read_bytes() == json_b.read_bytes()


# --- Stems --------------------------------------------------------------

def test_stems_byte_identical(tmp_path):
    df = _synthetic_df()
    cfg = RunConfig().with_palette(PALETTES["cinematic"])
    movements = detect_movements(df)
    events, tempo_markers, rendered = map_symphony(df, cfg, movements)
    tempo_changes = [
        TempoChange(tick=t.tick, bpm=t.bpm, label=t.label) for t in tempo_markers
    ]
    structural_markers = [
        StructuralMarker(tick=t, label=label)
        for t, label in compute_structural_markers(df, rendered, cfg)
    ]
    offsets = [
        MovementOffset(
            start_idx=r.movement.start_idx,
            end_idx=r.movement.end_idx,
            tick_offset=r.tick_offset,
            config=r.config,
        )
        for r in rendered
    ]
    all_events = (
        events
        + map_bass(df, cfg, movement_offsets=offsets)
        + map_voice(df, cfg, movement_offsets=offsets)
        + map_percussion(df, cfg, movement_offsets=offsets)
    )

    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    a_paths = write_midi_stems(
        all_events, a / "run.mid", cfg,
        tempo_changes=tempo_changes, markers=structural_markers,
    )
    b_paths = write_midi_stems(
        all_events, b / "run.mid", cfg,
        tempo_changes=tempo_changes, markers=structural_markers,
    )
    assert [p.name for p in a_paths] == [p.name for p in b_paths]
    for pa, pb in zip(a_paths, b_paths):
        assert pa.read_bytes() == pb.read_bytes(), f"stem {pa.name} differs"


# --- Sidecar JSON content audit ----------------------------------------

def test_sidecar_json_is_sorted_keys(tmp_path):
    """The sidecar uses sort_keys=True so the byte representation is
    insensitive to dict insertion order. Verify by checking the top-level
    keys appear alphabetically in the file."""
    df = _synthetic_df()
    cfg = RunConfig().with_palette(PALETTES["classical"])
    _, sidecar_path = _render_plain(df, cfg, tmp_path / "p.mid")

    text = sidecar_path.read_text()
    decoded = json.loads(text)
    expected_order = sorted(decoded.keys())
    # Find position of each key in the raw text — they should appear in
    # alphabetical order.
    positions = {k: text.index(f'"{k}"') for k in expected_order}
    sorted_by_pos = sorted(positions, key=positions.get)
    assert sorted_by_pos == expected_order
