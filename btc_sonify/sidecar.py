"""JSON sidecar.

Every ``btc-sonify`` run writes a ``.json`` next to the ``.mid``. The
sidecar is the bridge between the audible artefact and a curator who
wants to *navigate* it: bar 1247 stops being just bar 1247 once the
sidecar tells you it's the 2023-01-14 EMA crossover that kicked off
the Q1 rally.

Schema follows ``CLAUDE.md`` "JSON sidecar" exactly:

```json
{
  "config":  { full RunConfig used for this run },
  "source":  { symbol, timeframe, start, end, candle_count, exchange },
  "bars":    [ {bar, tick_start, candle_index, date, close, movement, event}, ... ],
  "events":  [ {bar, kind, candle_index, date, label}, ... ],
  "movements": [ {index, label, scale, root, bpm, ...}, ... ]
}
```

Two consumers, today: any DAW/script that wants a navigable index of the
.mid, and the v1.4 HTML visualizer (already shipped) which currently
re-derives this data — it will switch to reading the sidecar so there's
one source of truth.

Determinism note: ``json.dumps`` is called with ``sort_keys=True`` so
the byte representation is stable across Python implementations. Floats
are rounded to two decimal places (matching the visualizer's compact
encoding) so the same close prices always serialise identically.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from btc_sonify.config import RunConfig
from btc_sonify.detection import detect_structural_events
from btc_sonify.symphony import RenderedMovement
from btc_sonify.timeline import compute_timeline


def _config_to_jsonable(config: RunConfig) -> dict:
    """Render the RunConfig to a JSON-friendly dict.

    Most fields serialise directly via ``dataclasses.asdict``; the
    optional ``Optional[int]`` programs are kept as None (not omitted)
    so a producer reading the sidecar can tell the field exists but is
    inactive."""
    out = asdict(config)
    # ``candle_ticks`` and ``grace_ticks`` are properties — surface them
    # so a curator can see the actual tick budget without re-deriving.
    out["candle_ticks"] = config.candle_ticks
    out["grace_ticks"] = config.grace_ticks
    return out


def build_sidecar(
    df: pd.DataFrame,
    base_config: RunConfig,
    rendered_movements: list[RenderedMovement] | None,
    *,
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    exchange: str,
    palette: str,
    mode: str,
) -> dict:
    """Build the sidecar dict for one run. Pure function — same inputs
    always produce the same dict.
    """
    candles, mov_timeline = compute_timeline(rendered_movements, df, base_config)
    events = detect_structural_events(df)

    # Index events by candle for O(1) per-bar lookup.
    event_by_candle: dict[int, list[dict]] = {}
    for e in events:
        event_by_candle.setdefault(e.candle_index, []).append({
            "kind": e.kind,
            "label": e.label,
        })

    timestamps = (
        pd.to_datetime(df["timestamp"], utc=True)
        if "timestamp" in df.columns
        else None
    )

    bars: list[dict] = []
    for bar_idx, c in enumerate(candles, start=1):
        date = (
            timestamps.iloc[c.idx].strftime("%Y-%m-%d")
            if timestamps is not None
            else None
        )
        close = round(float(df["close"].iloc[c.idx]), 2)
        # First event on this candle (if any) — full event list is in
        # the top-level ``events`` array; this is a navigational hint.
        ev = event_by_candle.get(c.idx)
        bar = {
            "bar": bar_idx,
            "tick_start": c.start_tick,
            "start_s": round(c.start_s, 3),
            "candle_index": c.idx,
            "date": date,
            "close": close,
            "movement": c.movement,
            "event": ev[0]["label"] if ev else None,
        }
        bars.append(bar)

    # Reverse map candle_index → bar so ``events[]`` carries the bar
    # number a producer would scrub to.
    bar_by_candle = {b["candle_index"]: b["bar"] for b in bars}

    event_payload: list[dict] = []
    for e in events:
        if e.candle_index not in bar_by_candle:
            # Defensive: should never happen because compute_timeline
            # produces one bar per source-row, but skip rather than emit
            # an event with bar=None.
            continue
        ts = (
            timestamps.iloc[e.candle_index].strftime("%Y-%m-%d")
            if timestamps is not None
            else None
        )
        event_payload.append({
            "bar": bar_by_candle[e.candle_index],
            "kind": e.kind,
            "candle_index": e.candle_index,
            "date": ts,
            "label": e.label,
        })

    movements_payload = [
        {
            "index": m.index,
            "label": m.label,
            "direction": m.direction,
            "scale": m.scale,
            "root": m.root,
            "bpm": m.bpm,
            "start_tick": m.start_tick,
            "end_tick": m.end_tick,
            "start_s": round(m.start_s, 3),
            "end_s": round(m.end_s, 3),
            "start_idx": m.start_idx,
            "end_idx": m.end_idx,
        }
        for m in mov_timeline
    ]

    return {
        "config": _config_to_jsonable(base_config),
        "source": {
            "symbol": symbol,
            "timeframe": timeframe,
            "start": start,
            "end": end,
            "exchange": exchange,
            "palette": palette,
            "mode": mode,
            "candle_count": len(df),
        },
        "movements": movements_payload,
        "bars": bars,
        "events": event_payload,
    }


def write_sidecar(sidecar: dict, path: Path) -> Path:
    """Write the sidecar dict to ``path`` as deterministic JSON.

    ``sort_keys=True`` keeps the byte representation stable across
    Python versions / dict insertion order, so two runs with identical
    configs produce byte-identical sidecar files."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(sidecar, sort_keys=True, indent=2)
    path.write_text(text + "\n", encoding="utf-8")
    return path


__all__ = ["build_sidecar", "write_sidecar"]
