"""Symphony mode: split a long sonification into movements.

A long BTC sonification (multi-year) flattens into one undifferentiated
arc. Symphony mode segments the price series at significant turning
points so the listener hears recognisable movements — bull rallies,
bear capitulations, sideways consolidations — each with its own key,
mode, and tempo.

Movement boundaries come from peak-trough segmentation: walk the close
prices, register a turning point whenever price moves more than
``min_excursion_pct`` against the running extremum. This is the
standard way traders think about cycles and produces musically
meaningful segments without statistical machinery.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

import numpy as np
import pandas as pd

from btc_sonify.config import RunConfig
from btc_sonify.detection import StructuralEvent, detect_structural_events
from btc_sonify.mapping import MidiEvent, map_candles_to_events
from btc_sonify.scales import note_name_to_midi

Direction = Literal["bull", "bear", "sideways"]

# Scale palette per direction. Defaults to musically defensible choices
# for each market mood.
DIRECTION_SCALES: dict[Direction, str] = {
    "bull": "dorian",          # bright modal, hopeful
    "bear": "phrygian",        # haunted, descending
    "sideways": "hijaz",       # modal-mysterious, ambiguous
}

# Roman-numeral movement labels for nice DAW track names.
ROMAN: tuple[str, ...] = (
    "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
)


@dataclass(frozen=True)
class Movement:
    """One segment of a symphonic sonification."""
    index: int                  # 0-based ordinal
    start_idx: int              # inclusive index into the source DataFrame
    end_idx: int                # inclusive
    direction: Direction
    avg_return_pct: float       # close[end] vs close[start], percent
    volatility: float           # std of log returns within the segment
    label: str                  # e.g. "I. Bull 2020-2021"


@dataclass(frozen=True)
class TempoMarker:
    """A tempo change event for the meta track."""
    tick: int
    bpm: int
    label: str


# --- Movement detection -------------------------------------------------

def _segment_close(closes: np.ndarray, min_excursion_pct: float) -> list[int]:
    """Find significant turning points in a close-price series.

    Returns indices (into ``closes``) of major peaks and troughs. The
    series always starts and ends with a turning point; intermediate
    points are added when price reverses by ``min_excursion_pct`` from
    the running extremum.
    """
    if len(closes) < 2:
        return [0, len(closes) - 1] if len(closes) else []

    threshold = min_excursion_pct / 100.0
    pivots: list[int] = [0]
    direction: int = 0     # +1 if currently rising, -1 if falling, 0 unknown
    extremum_idx = 0
    extremum_val = closes[0]

    for i in range(1, len(closes)):
        price = closes[i]
        if direction >= 0:
            # currently looking for higher highs; check for reversal down
            if price > extremum_val:
                extremum_val = price
                extremum_idx = i
                direction = 1
            elif (extremum_val - price) / extremum_val >= threshold:
                # registered a peak at extremum_idx; flip
                pivots.append(extremum_idx)
                extremum_val = price
                extremum_idx = i
                direction = -1
        else:
            # currently looking for lower lows; check for reversal up
            if price < extremum_val:
                extremum_val = price
                extremum_idx = i
            elif (price - extremum_val) / extremum_val >= threshold:
                pivots.append(extremum_idx)
                extremum_val = price
                extremum_idx = i
                direction = 1

    if pivots[-1] != len(closes) - 1:
        pivots.append(len(closes) - 1)
    return pivots


def _direction_of(start_close: float, end_close: float, sideways_threshold: float = 0.05) -> Direction:
    """Classify a segment as bull / bear / sideways by net return."""
    ret = (end_close - start_close) / start_close
    if ret > sideways_threshold:
        return "bull"
    if ret < -sideways_threshold:
        return "bear"
    return "sideways"


def detect_movements(
    df: pd.DataFrame,
    min_excursion_pct: float = 20.0,
    movements: int | None = None,
) -> list[Movement]:
    """Segment ``df`` into a list of Movements.

    Default behaviour: auto-detect via peak-trough segmentation with a
    20% excursion threshold (a 20% move against the running extremum
    counts as a regime change — the trader's rough rule of thumb for a
    bull/bear pivot). Pass ``movements=N`` to force exactly N movements,
    sized roughly equally by candle count.
    """
    if df.empty:
        return []

    closes = df["close"].to_numpy(dtype=float)
    timestamps = (
        pd.to_datetime(df["timestamp"], utc=True)
        if "timestamp" in df.columns
        else None
    )

    if movements is not None and movements > 0:
        # Equal-sized chunks. Simplest possible "manual" override.
        edges = np.linspace(0, len(df) - 1, movements + 1).round().astype(int)
        pivots = list(dict.fromkeys(edges.tolist()))  # de-dup, preserve order
    else:
        pivots = _segment_close(closes, min_excursion_pct)
        # Drop unrealistically short movements (< 5 candles) by merging
        # them into the next pivot — a 1-candle "movement" is musical noise.
        cleaned = [pivots[0]]
        for p in pivots[1:]:
            if p - cleaned[-1] < 5 and p != pivots[-1]:
                continue
            cleaned.append(p)
        pivots = cleaned
        # Cap at 10 movements — past that the symphony becomes a slideshow.
        if len(pivots) > 11:
            # Keep the most prominent: largest absolute % moves.
            scores = []
            for j in range(1, len(pivots) - 1):
                a, b = closes[pivots[j - 1]], closes[pivots[j]]
                scores.append((abs(b - a) / a, j))
            scores.sort(reverse=True)
            keep_internal = sorted(idx for _, idx in scores[:9])
            pivots = [pivots[0], *(pivots[k] for k in keep_internal), pivots[-1]]

    out: list[Movement] = []
    for i in range(len(pivots) - 1):
        s, e = pivots[i], pivots[i + 1]
        if e <= s:
            continue
        seg_close = closes[s:e + 1]
        direction = _direction_of(seg_close[0], seg_close[-1])
        ret_pct = (seg_close[-1] - seg_close[0]) / seg_close[0] * 100
        if len(seg_close) > 1:
            log_ret = np.diff(np.log(seg_close))
            vol = float(np.std(log_ret))
        else:
            vol = 0.0

        if timestamps is not None:
            year_start = timestamps.iloc[s].year
            year_end = timestamps.iloc[e].year
            year_part = (
                f"{year_start}" if year_start == year_end
                else f"{year_start}-{year_end}"
            )
        else:
            year_part = f"{s}-{e}"

        roman = ROMAN[i] if i < len(ROMAN) else f"M{i + 1}"
        label = f"{roman}. {direction.capitalize()} {year_part}"

        out.append(Movement(
            index=i,
            start_idx=s,
            end_idx=e,
            direction=direction,
            avg_return_pct=ret_pct,
            volatility=vol,
            label=label,
        ))
    return out


# --- Per-movement config derivation -------------------------------------

# Circle of fifths for root modulation: each movement transposes its
# root by one fifth from the previous, giving the symphony classical
# tonal motion without sounding random.
_FIFTH_CYCLE: tuple[str, ...] = (
    "A", "E", "B", "F#", "C#", "G#", "D#", "A#", "F", "C", "G", "D",
)


def _modulate_root(prev_root: str, steps: int = 1) -> str:
    """Return the note name `steps` perfect fifths up from prev_root."""
    if prev_root not in _FIFTH_CYCLE:
        # Fall back to alphabetical neighbours; keep things deterministic.
        return prev_root
    idx = (_FIFTH_CYCLE.index(prev_root) + steps) % len(_FIFTH_CYCLE)
    return _FIFTH_CYCLE[idx]


def derive_movement_config(
    base: RunConfig,
    movement: Movement,
    prev_movement: Movement | None,
    prev_root: str | None,
    median_volatility: float,
    user_specified_scale: bool,
) -> tuple[RunConfig, str]:
    """Build a RunConfig for one movement.

    Direction picks the scale (only when the user hasn't pinned one);
    root modulates by a perfect fifth from the previous movement;
    high-volatility movements get a 20% tempo bump.
    """
    if user_specified_scale:
        scale = base.scale
    else:
        scale = DIRECTION_SCALES[movement.direction]

    if prev_root is None:
        root = base.root
    else:
        root = _modulate_root(prev_root, steps=1)

    bpm = base.bpm
    if movement.volatility > median_volatility:
        bpm = int(round(base.bpm * 1.2))

    return replace(base, scale=scale, root=root, bpm=bpm), root


# --- Symphony orchestration ---------------------------------------------

@dataclass(frozen=True)
class RenderedMovement:
    """Bookkeeping for one movement after it has been laid into the
    global timeline. Contains everything the rest of the pipeline
    (percussion, summary panel, midi writer) needs."""
    movement: Movement
    config: RunConfig
    tick_offset: int   # global start tick of this movement's first candle


def map_symphony(
    df: pd.DataFrame,
    base_config: RunConfig,
    movements: list[Movement],
    user_specified_scale: bool = False,
) -> tuple[list[MidiEvent], list[TempoMarker], list[RenderedMovement]]:
    """Render a list of movements into a single chronological event list,
    a list of tempo-change markers, and a per-movement offset record.

    Each movement is mapped independently (its own pitch range, its own
    articulation rhythms) and concatenated with a one-beat rest between
    movements. Tick offsets accumulate across the whole timeline.

    Tempo markers come in two flavours and are returned interleaved
    (sorted by tick): one labelled headline marker per movement
    (carries the movement name, used by DAWs as a section flag), plus
    unlabelled within-movement rubato markers when rubato is enabled
    on that movement's config (see :mod:`btc_sonify.rubato`).
    """
    if not movements:
        return [], [], []

    median_vol = float(np.median([m.volatility for m in movements])) if movements else 0.0

    all_events: list[MidiEvent] = []
    tempo_markers: list[TempoMarker] = []
    rendered: list[RenderedMovement] = []
    movement_cfgs: list[RunConfig] = []
    movement_offsets: list[int] = []
    prev_root: str | None = None
    prev_mov: Movement | None = None
    tick_offset = 0
    rest_ticks = base_config.ppq  # one beat of silence between movements

    for movement in movements:
        cfg, used_root = derive_movement_config(
            base_config, movement, prev_mov, prev_root, median_vol, user_specified_scale,
        )

        seg = df.iloc[movement.start_idx:movement.end_idx + 1].reset_index(drop=True)
        seg_events = map_candles_to_events(seg, cfg)

        tempo_markers.append(TempoMarker(
            tick=tick_offset,
            bpm=cfg.bpm,
            label=movement.label,
        ))
        rendered.append(RenderedMovement(
            movement=movement, config=cfg, tick_offset=tick_offset,
        ))
        movement_cfgs.append(cfg)
        movement_offsets.append(tick_offset)

        shifted = [
            MidiEvent(
                channel=e.channel,
                note=e.note,
                velocity=e.velocity,
                start_tick=e.start_tick + tick_offset,
                duration_ticks=e.duration_ticks,
            )
            for e in seg_events
        ]
        all_events.extend(shifted)

        if shifted:
            seg_end = max(e.start_tick + e.duration_ticks for e in shifted)
            tick_offset = seg_end + rest_ticks
        else:
            tick_offset += rest_ticks

        prev_root = used_root
        prev_mov = movement

    # Rubato lives in its own module to keep the symphony orchestration
    # focused on movement detection + per-movement config; import here
    # to avoid a circular import (rubato pulls in TempoMarker from us).
    from btc_sonify.rubato import interleave_rubato_markers
    rubato_markers = interleave_rubato_markers(
        df, movements, movement_cfgs, movement_offsets, base_config,
    )
    if rubato_markers:
        tempo_markers.extend(rubato_markers)
        tempo_markers.sort(key=lambda m: m.tick)

    return all_events, tempo_markers, rendered


def compute_structural_markers(
    df: pd.DataFrame,
    rendered_movements: list[RenderedMovement],
    base_config: RunConfig,
) -> list[tuple[int, str]]:
    """Translate detected structural events into ``(tick, label)`` pairs.

    Each event from :func:`detect_structural_events` (run on the *whole*
    DataFrame so labels reflect the global narrative) is placed at the
    candle's absolute tick position inside its containing movement. When
    movement boundaries share a candle, the event is anchored to the
    *first* movement that contains it — matching the de-dup convention
    in ``timeline.compute_timeline``.

    Returns plain tuples to keep the symphony module independent of
    midi_writer's ``StructuralMarker`` type. The CLI wraps them into
    StructuralMarker before passing to ``write_midi``.
    """
    if not rendered_movements:
        return []

    events = detect_structural_events(df)
    if not events:
        return []

    candle_ticks = base_config.candle_ticks
    grace_ticks = base_config.grace_ticks

    out: list[tuple[int, str]] = []
    for event in events:
        for r in rendered_movements:
            if r.movement.start_idx <= event.candle_index <= r.movement.end_idx:
                local_idx = event.candle_index - r.movement.start_idx
                tick = r.tick_offset + grace_ticks + local_idx * candle_ticks
                out.append((tick, event.label))
                break  # anchor to the first containing movement
    return out


__all__ = [
    "Direction",
    "Movement",
    "TempoMarker",
    "RenderedMovement",
    "DIRECTION_SCALES",
    "detect_movements",
    "derive_movement_config",
    "map_symphony",
    "compute_structural_markers",
]
