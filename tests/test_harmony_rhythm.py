"""Harmony-rhythm tests — sustained / arp_up / arp_down.

The rhythm switch turns a held chord into a 4-position arpeggio per
candle. These tests pin:

- Note-count contract: arp modes emit exactly 4× the notes of sustained.
- Position→note table: each tier maps to the right chord-index sequence
  for both arp directions.
- Velocity contour visible: position 0 lands above position 3 after
  harmony_velocity_factor, before humanize jitter.
- Bar geometry: arp duration sums to exactly candle_ticks (no slop).
- Determinism: two runs of the same input produce identical events.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from btc_sonify.config import RunConfig
from btc_sonify.mapping import (
    _ARP_VELOCITY_OFFSETS,
    _arp_positions,
    _harmony_notes,
    map_candles_to_events,
)


def _synthetic_df(n: int = 30, seed: int = 1) -> pd.DataFrame:
    """A 30-candle frame with enough range variation to trigger all
    three tier values (single / diad / triad)."""
    rng = np.random.default_rng(seed)
    closes = 100 * np.exp(np.cumsum(rng.normal(0.005, 0.015, n)))
    opens = np.concatenate([[closes[0]], closes[:-1]])
    # Variable wick widths so the range distribution spans terciles.
    widths = rng.uniform(0.005, 0.05, n)
    return pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC"),
        "open": opens,
        "high": np.maximum(opens, closes) * (1 + widths),
        "low": np.minimum(opens, closes) * (1 - widths),
        "close": closes,
        "volume": rng.uniform(500, 5000, n),
    })


def _harmony_events(events, harmony_channel: int):
    return [e for e in events if e.channel == harmony_channel]


# --- Note-count contract -----------------------------------------------

@pytest.mark.parametrize("rhythm", ["arp_up", "arp_down"])
def test_arp_emits_four_times_sustained_count(rhythm):
    df = _synthetic_df()
    base = replace(RunConfig(), humanize=False)  # remove jitter for clean counts

    sustained = map_candles_to_events(replace(base, harmony_rhythm="sustained"), df=df) \
        if False else map_candles_to_events(df, replace(base, harmony_rhythm="sustained"))
    arp = map_candles_to_events(df, replace(base, harmony_rhythm=rhythm))

    s_h = _harmony_events(sustained, base.harmony_channel)
    a_h = _harmony_events(arp, base.harmony_channel)

    # Every candle goes from {1,2,3} sustained notes to exactly 4 arp notes.
    # So total arp = 4 * candle_count, regardless of how many sustained
    # there were per candle.
    assert len(a_h) == 4 * len(df)
    # Sanity: sustained is bounded above by 3 * candle_count.
    assert len(s_h) <= 3 * len(df)


# --- Position → note mapping (per tier, per direction) ----------------

@pytest.mark.parametrize("tier,chord_len", [(0, 1), (1, 2), (2, 3)])
def test_arp_up_position_table(tier, chord_len):
    """arp_up positions [0,1,2,3] map to the right chord indices."""
    # Build a chord of the right length.
    chord = list(range(60, 60 + chord_len))
    notes = _arp_positions(chord, tier, "up")
    assert len(notes) == 4
    if tier == 0:
        assert notes == [60, 60, 60, 60]
    elif tier == 1:
        # root, fifth, root, fifth
        assert notes == [chord[0], chord[1], chord[0], chord[1]]
    else:
        # root, third, fifth, third — pos 3 returns to inner voice
        assert notes == [chord[0], chord[1], chord[2], chord[1]]


@pytest.mark.parametrize("tier,chord_len", [(0, 1), (1, 2), (2, 3)])
def test_arp_down_position_table(tier, chord_len):
    chord = list(range(60, 60 + chord_len))
    notes = _arp_positions(chord, tier, "down")
    assert len(notes) == 4
    if tier == 0:
        assert notes == [60, 60, 60, 60]
    elif tier == 1:
        # fifth, root, fifth, root
        assert notes == [chord[1], chord[0], chord[1], chord[0]]
    else:
        # fifth, third, root, third
        assert notes == [chord[2], chord[1], chord[0], chord[1]]


# --- Velocity contour --------------------------------------------------

def test_velocity_contour_shape():
    """Position 0 ≥ position 3 in the contour offsets."""
    assert _ARP_VELOCITY_OFFSETS[0] >= _ARP_VELOCITY_OFFSETS[3]
    assert _ARP_VELOCITY_OFFSETS[0] == 0  # downbeat at full base
    # All other positions are pulled DOWN, never up.
    for off in _ARP_VELOCITY_OFFSETS[1:]:
        assert off <= 0


def test_velocity_contour_visible_in_output():
    """Without humanize jitter, position-0 harmony notes should land at
    a higher velocity than position-3 notes for the same candle."""
    df = _synthetic_df(n=30)
    cfg = replace(RunConfig(), humanize=False, harmony_rhythm="arp_up")
    events = map_candles_to_events(df, cfg)
    h = _harmony_events(events, cfg.harmony_channel)

    # Group by candle: every consecutive 4 harmony events on a single
    # candle = positions 0..3. (Output may interleave per-candle blocks
    # but within one candle the loop append-order is sequential.)
    # Simplest check: average position-0 velocity > average position-3.
    pos0 = [h[k].velocity for k in range(0, len(h), 4)]
    pos3 = [h[k].velocity for k in range(3, len(h), 4)]
    assert np.mean(pos0) > np.mean(pos3), (
        f"Velocity contour not visible: pos0 mean {np.mean(pos0):.1f}, "
        f"pos3 mean {np.mean(pos3):.1f}"
    )


# --- Bar geometry ------------------------------------------------------

def test_arp_bar_sums_to_candle_ticks():
    """The 4 arp positions must collectively fill exactly candle_ticks
    of the timeline — no slop, no overlap, no gap."""
    df = _synthetic_df(n=10)
    cfg = replace(RunConfig(), humanize=False, harmony_rhythm="arp_up")
    events = map_candles_to_events(df, cfg)
    h = _harmony_events(events, cfg.harmony_channel)

    # Walk in groups of 4 (one candle's arp).
    for k in range(0, len(h), 4):
        block = h[k:k + 4]
        first_start = block[0].start_tick
        last_end = block[-1].start_tick + block[-1].duration_ticks
        assert last_end - first_start == cfg.candle_ticks, (
            f"Arp block at index {k} has length "
            f"{last_end - first_start}, expected {cfg.candle_ticks}"
        )


# --- Determinism -------------------------------------------------------

@pytest.mark.parametrize("rhythm", ["sustained", "arp_up", "arp_down"])
def test_harmony_rhythm_is_deterministic(rhythm):
    df = _synthetic_df()
    cfg = replace(RunConfig(), harmony_rhythm=rhythm)
    a = map_candles_to_events(df, cfg)
    b = map_candles_to_events(df.copy(), cfg)
    assert a == b


def test_arp_up_and_arp_down_differ():
    """Sanity check: the two arp directions must produce different
    output (otherwise the table is broken)."""
    df = _synthetic_df()
    base = replace(RunConfig(), humanize=False)
    up = map_candles_to_events(df, replace(base, harmony_rhythm="arp_up"))
    down = map_candles_to_events(df, replace(base, harmony_rhythm="arp_down"))
    up_h = _harmony_events(up, base.harmony_channel)
    down_h = _harmony_events(down, base.harmony_channel)
    # At least some notes must differ (for non-tier-0 candles).
    assert any(u.note != d.note for u, d in zip(up_h, down_h))


# --- Sanity check: _harmony_notes still returns the right shape -------

def test_harmony_notes_unchanged_by_arp_work():
    """The new arp tables read from _harmony_notes; make sure that
    function's contract didn't drift."""
    scale = list(range(60, 80))
    assert _harmony_notes(60, 0, scale) == [60]
    assert len(_harmony_notes(60, 1, scale)) == 2
    assert len(_harmony_notes(60, 2, scale)) == 3
