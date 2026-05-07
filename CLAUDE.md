# Bitcoin Candlestick Sonification — Project Handoff

## What we're building

A Python CLI tool that converts Bitcoin OHLCV candlestick data into music. The user provides a date range, timeframe, and musical scale; the tool outputs a MIDI file (and optionally a rendered WAV/MP3) where each candle becomes a note with articulation, dynamics, and ornamentation derived from the candle's properties.

This is a **data sonification** project, not a generative-music toy. The mapping should be deterministic and auditable — the same candles in the same scale should always produce the same MIDI. Musicality comes from a thoughtful mapping, not randomness.

## Project owner context

The owner runs multiple SaaS products and has a strong design sensibility. He's also into prediction markets, sacred geometry, and the harmonic ratios (3:2, 4:3, 1.618) that connect music theory and market analysis. Treat this as a real project that might become content — the code should be clean enough to share, the output should be musically defensible, and the README should communicate the *why* as well as the how.

His primary dev environment is Cursor + Claude Code. Python is fine but he's not a Python specialist — favor readable, well-commented code over clever code. He'll run this on macOS.

## Core mapping spec

This is the heart of the project. Implement it exactly as specified; expose each axis as a config parameter so it can be tuned later.

### Inputs per candle

Each candle has: `timestamp`, `open`, `high`, `low`, `close`, `volume`.

Derived per candle:
- `direction` = `green` if close ≥ open else `red`
- `body_size` = `abs(close - open)`
- `range` = `high - low`
- `upper_wick` = `high - max(open, close)`
- `lower_wick` = `min(open, close) - low`
- `body_ratio` = `body_size / range` (0 = doji, 1 = no wicks)

### The mapping

**Pitch ← Close price.** Normalize close prices across the entire dataset to a 0–1 range (use min-max over the whole series, not rolling — we want the macro shape to be audible). Then quantize to the chosen scale across N octaves (default 3 octaves, configurable). The lowest close in the dataset = lowest note in the range; highest close = highest note.

**Note duration ← fixed per run.** All candles get the same duration. Default: a quarter note at 120 BPM. Expose `--bpm` and `--note-value` (quarter, eighth, half).

**Velocity (loudness) ← Volume.** Normalize volume to the MIDI velocity range (40–127, never go below 40 — silence isn't musical). Use a logarithmic scale because volume distributions are heavy-tailed; raw linear mapping would make most notes whisper-quiet with rare booms. *Per-palette carve-out:* the `cinematic` palette deliberately overrides this floor (`velocity_min=28`) to reach true pianissimo before swelling — film-score dynamic range is wider than the global floor. Other palettes keep the 40 floor.

**Articulation ← Direction + body_ratio.**
- Green candle, body_ratio > 0.7 → legato (note duration = 100% of beat)
- Green candle, body_ratio ≤ 0.7 → normal (note duration = 80% of beat)
- Red candle, body_ratio > 0.7 → marcato (note duration = 60% of beat, +10 velocity)
- Red candle, body_ratio ≤ 0.7 → staccato (note duration = 40% of beat)
- Doji (body_ratio < 0.1) → trill: alternate between the close note and the note one scale-step above for the duration of the beat

**Ornamentation ← Wick lengths.**
- If `upper_wick > 2 × body_size`: add a grace note one scale-step *above* the close note, played 1/16th note before the main note
- If `lower_wick > 2 × body_size`: add a grace note one scale-step *below*, same timing
- Both conditions can fire on the same candle (it's a long-wick doji situation — gets two grace notes)

**Harmony ← Range (high – low).** This is the secondary track. Output a second MIDI channel that plays a chord on each candle:
- Range in bottom 33% of dataset's range distribution → single note (the close)
- Range in middle 33% → diad (close + fifth above, scale-aware)
- Range in top 33% → triad (close + third + fifth, scale-aware)

The harmony track plays at lower velocity (60% of melody velocity) and uses a different instrument (default: melody = piano/Acoustic Grand, harmony = strings/String Ensemble 1).

### Scales to support

Hard-code these as note-offset arrays from the root. Default root is A (MIDI 57 for A3).

- `major`: [0, 2, 4, 5, 7, 9, 11]
- `minor` (natural): [0, 2, 3, 5, 7, 8, 10]
- `pentatonic_major`: [0, 2, 4, 7, 9]
- `pentatonic_minor`: [0, 3, 5, 7, 10]
- `dorian`: [0, 2, 3, 5, 7, 9, 10]
- `phrygian`: [0, 1, 3, 5, 7, 8, 10]
- `hijaz`: [0, 1, 4, 5, 7, 8, 11]  *(Arabic/Middle Eastern, mystical feel)*
- `blues`: [0, 3, 5, 6, 7, 10]

Default scale: `phrygian`. It fits Bitcoin's character — searching, modal, slightly haunted.

## Architecture

```
btc_sonify/
├── pyproject.toml
├── README.md
├── CLAUDE.md  (this file, kept in repo)
├── btc_sonify/
│   ├── __init__.py
│   ├── cli.py          # Click/Typer entry point
│   ├── data.py         # OHLCV fetching (ccxt) + caching
│   ├── scales.py       # Scale definitions, quantization helpers
│   ├── mapping.py      # The core OHLCV → MIDI events logic
│   ├── midi_writer.py  # mido wrapper, writes the .mid file
│   ├── render.py       # Optional FluidSynth render to WAV
│   └── config.py       # Dataclass for run config
├── tests/
│   ├── test_scales.py
│   ├── test_mapping.py
│   └── fixtures/
│       └── sample_btc_daily.csv
└── output/              # gitignored, where MIDI/WAV land
```

## Dependencies

- `ccxt` — exchange data fetching (Binance is fine, use the public endpoint, no API key needed for OHLCV)
- `mido` — MIDI file writing
- `pandas` — data handling
- `typer` — CLI framework (cleaner than argparse, type hints become args automatically)
- `rich` — pretty CLI output (progress bars during fetch and render)
- `pytest` — testing

Optional/dev:
- `pyfluidsynth` for in-process WAV rendering (requires FluidSynth installed via Homebrew on macOS — document this in README). If too painful, fall back to instructing the user to open the MIDI in GarageBand or use the `fluidsynth` CLI directly.

A free GeneralUser GS soundfont (`.sf2`) should be downloaded by a `make setup` script or fetched on first run with user consent — don't commit it to the repo.

## CLI surface

```bash
# Basic usage
btc-sonify --start 2020-01-01 --end 2024-12-31 --timeframe 1d

# All options
btc-sonify \
  --start 2020-01-01 \
  --end 2024-12-31 \
  --timeframe 1d \           # 1h, 4h, 1d, 1w
  --scale phrygian \
  --root A \
  --octaves 3 \
  --bpm 120 \
  --note-value quarter \
  --output ./output/btc-2020-2024.mid \
  --render-wav \             # optional, requires fluidsynth
  --soundfont ./soundfonts/GeneralUser.sf2
```

## Build order

Implement in this order. Don't skip ahead — each step should be runnable and testable before moving on.

1. **Project skeleton + `pyproject.toml`.** Use `uv` or `pip install -e .` for dev install. Confirm `btc-sonify --help` runs.
2. **`scales.py`.** Pure functions, fully unit-tested. `quantize_to_scale(value_0_to_1, scale_name, root_midi, octaves) -> midi_note`. This is the math foundation; get it right before anything else.
3. **`data.py`.** Fetch OHLCV via `ccxt.binance().fetch_ohlcv('BTC/USDT', timeframe, since, limit)`. Cache results to `~/.cache/btc-sonify/` as parquet. Handle pagination (Binance returns max 1000 candles per call).
4. **`mapping.py`.** The OHLCV → MIDI event list. Pure function: takes a DataFrame and a config, returns a list of `(channel, note, velocity, start_tick, duration_ticks)` tuples. Test with the fixture CSV.
5. **`midi_writer.py`.** Takes the event list and writes a `.mid` file. Two channels: melody (program 0, Acoustic Grand) and harmony (program 48, String Ensemble 1). Include a tempo meta-message.
6. **`cli.py`.** Wire everything together. Progress bar during fetch, summary stats after writing (notes written, duration, output path).
7. **`render.py`.** Optional WAV render via FluidSynth. Catch the import error gracefully if FluidSynth isn't installed and tell the user how to install it.
8. **README.** Explain the project, the mapping, the philosophy, with example invocations and a "what BTC sounds like in different scales" section. Link to a few sample MP3s the owner can host on Cloudflare R2 or similar.

After v1 is shipped and listened to, the symphonic additions slot in as 9–11 (see "Symphonic mapping additions" below):

9. **v1.1** — Bass drone (channel 2) + melody legato bridging + **JSON sidecar** + **`--export-stems`**. The sidecar and stems are pure additions to v1's output and should land here so every later release benefits from them.
10. **v1.2** — Sustained pad (channel 3) with structural triggers + **MIDI marker events** at every trigger fire (swing pivots, vol regime shifts, MA crossovers). Markers and triggers share the same detection code, so they ship together.
11. **v1.3** — Expression envelope (CC11) + texture gating + rubato (within-movement tempo breathing) + `--preset` flag. *Rubato shipped — see `btc_sonify/rubato.py`; CC11/gating still pending.*
12. **v1.4** — `--export-html` self-contained visual companion: candlestick chart synced to MIDI playback, event markers as chart annotations. Reads the v1.1 sidecar — no re-derivation of data.

## Testing notes

- The fixture CSV should be ~30 candles of real BTC daily data (any 30-day window). Tests should verify deterministic mapping: same input + same config = same MIDI events.
- Test edge cases: a doji (open == close), a 100%-body candle (no wicks), the first candle (no prior context), volume = 0 (shouldn't crash, should clamp to min velocity).
- Don't write tests against live API calls. Mock `ccxt` in `data.py` tests, or skip them and rely on fixture-based mapping tests.

## Style

- Type hints everywhere. `from __future__ import annotations` at the top of each module.
- Dataclasses for config, not dicts.
- Docstrings on public functions explaining *what* and *why*, not just *what*. The mapping logic especially benefits from comments explaining the musical rationale.
- No premature abstraction. If something is used once, inline it. Refactor when there's a second use case.
- Use `rich.console.Console` for all user-facing output. No bare `print`.

## Things to NOT do

- Don't add a web UI. CLI only for v1.
- Don't add real-time streaming ("listen to BTC live"). Out of scope, easy to add later, would balloon the dependency tree.
- Don't add machine learning. The mapping is the art; ML would obscure it.
- Don't try to be clever about timing — every candle gets equal **duration in ticks** (i.e. one beat in the score). Variable per-candle duration was considered and rejected because it breaks the audit trail (different candles consuming different tick counts) and makes the output harder to follow as music. *Rubato — modulating real-time playback speed via meta-track `set_tempo` events — is a different beast and IS allowed* (see v1.3 below): it leaves the per-candle tick count untouched, so 1 candle = 1 beat is preserved, while still giving the listener a piece that breathes.
- Don't pull in `music21`. It's a heavy dependency for what we need; `mido` + custom scale logic is leaner.

## Symphonic mapping additions (v1.1 → v1.3)

The v1 spec above is intentionally pointillistic — every candle is a discrete attack, equal in length, and there is no sustained voice. Played back, this can feel monotonous: bing, bang, ping, pang. The additions below add **ground, atmosphere, and long-arc dynamics** so the output reads as a piece of music rather than a metronome with pitches.

These are **not** part of v1. Ship v1 first, listen to it, then layer these in. Each numbered release is audibly distinct from the last so you can decide whether the added complexity earns its keep.

### New channels

| Ch | Voice | GM Program | Role |
|----|-------|------------|------|
| 0  | Melody | 0 — Acoustic Grand | (v1) per-candle close |
| 1  | Harmony | 48 — String Ensemble 1 | (v1) per-candle chord |
| 2  | **Bass drone** | 42 — Cello | Long-timeframe foundation |
| 3  | **Pad** | 89 — Pad 2 (warm) *or* 48 — Strings | Sustained atmosphere |
| 9  | (reserved) | — | Optional percussion in v2 |

All triggers below are deterministic functions of the OHLCV series. The 1:1 "every candle = one auditable note" purity becomes 1:N — call this out in the README so the mapping stays honest.

### v1.1 — Bass drone + legato bridging

Smallest change, biggest perceived improvement.

**Bass drone (channel 2).** Holds a single low sustained note. Re-articulates only on regime change, not per candle.
- *Pitch*: root of the scale, transposed down 1–2 octaves. Steps up one scale degree when price crosses the 50-candle SMA from below; down when it crosses from above. Configurable: `--bass-period 50`.
- *Velocity*: fixed 60. Steady, never marcato.
- *Duration*: holds until the next crossing event. Typically 5–30 candles per bass note.

**Melody legato bridging (channel 0).** When 3+ consecutive same-direction candles occur:
- Force `articulation = legato` for the entire run (overrides the body_ratio rule).
- Set duration of all but the last note to **101% of beat** (1-tick overlap) so consecutive notes tie cleanly without re-attack gaps.
- Suppress grace notes inside the run except on the first and last candles. Long trends should *flow*, not chatter.

### v1.2 — Sustained pad

**Pad (channel 3).** Long-form chord that re-voices on **structural events**, not per candle.

Triggers (any of):
1. *Swing pivot confirmed*: candle's high is the highest of the last 10 and the next 3 candles all close lower (or vice versa for lows). Non-causal, but acceptable — we have the whole dataset; this is composition, not forecasting.
2. *Volatility regime shift*: rolling 20-candle stdev of returns crosses ±50% of its 100-candle trailing mean.
3. *SMA crossover*: fast (20) crosses slow (50).

On trigger: emit a 3- or 4-note voicing of the chord implied by the current candle's close (reuse the v1 range→chord logic, but always at least a triad). Voicing held until the next trigger.

- *Velocity*: 45 baseline, modulated by the v1.3 expression envelope.
- *Range*: voiced in the octave above middle C — a separate register from melody so it doesn't muddy.

### v1.3 — Expression envelope + texture gating + rubato

This release ships the two phrasing axes together: **dynamics over phrases** (CC11 swells) and **timing over phrases** (rubato tempo breathing). They share trigger geometry and feel half-done in isolation.

**Expression envelope (CC11 on channels 0 and 3).** A continuous controller curve that gives the piece dynamics over phrases, not just per note.

Per candle, compute:
```
conviction[i]      = z_score( volume[i] * abs(return[i]) , window=20 )
trend_strength[i]  = abs(ema20[i] - ema50[i]) / close[i]
envelope[i]        = smooth( 0.6 * conviction + 0.4 * trend_strength , window=5 )
```

Map `envelope[i]` to CC11 in the range 40–127. Emit on every candle for channels 0 (melody) and 3 (pad). Per-candle velocity stays as-is; CC11 multiplies on top, so you get crescendos *into* big moves and tapers afterward without losing per-candle definition.

Send each CC11 change 1–2 ticks before the corresponding note-on so the swell is heard.

**Texture gating.** Mute the pad (`channel 3 volume → 0` via CC7) when:
- Volatility is in the bottom 25% of the dataset's distribution, AND
- No structural pad trigger has fired in the last 8 candles.

Result: quiet consolidation periods drop to bass + sparse melody, then the pad swells back in when something interesting happens. This is what gives a symphony its *breath* — not every section is full ensemble.

**Rubato (within-movement tempo breathing).** *Implemented — see `btc_sonify/rubato.py`.* Symphony mode already emits one labelled `TempoMarker` per movement (a step function across months). Rubato adds smoothed, within-movement tempo modulation so the piece breathes inside each section. Per-candle factor in `[rubato_min_factor, rubato_max_factor]` (default `[0.65, 1.20]` — lopsided toward slower because rubato is mostly *taking time*) blends four signals:

1. **Approach** — rallentando into structural events (local pivot, vol regime shift, EMA20/50 cross) within the next `rubato_approach_window` candles.
2. **Climax** — held suspension AT the structural event itself.
3. **Trend** — accelerando during runs of 3+ same-direction candles, capped at 8 candles, gated on EMA20-EMA50 agreement.
4. **Vol bias** — slower in low-vol consolidation, faster in high-vol expansion.

Combination is smoothed across `rubato_smoothing_window` candles (`center=True` so the curve anticipates upcoming events) then quantized to `rubato_quantize_step`-BPM buckets to keep the meta track readable (~30–80 markers per 5-year run, not thousands). Default-on for `classical` and `cinematic` palettes; default-off for `synthwave` and `electronic` (genre wants grid). CLI `--rubato/--no-rubato` overrides either way.

Audit invariant preserved: rubato modulates only meta-track `set_tempo` events; the per-candle tick count is unchanged, so same OHLCV + same config → byte-identical MIDI.

### Presets

Eight new tunables across these three releases is a lot of CLI surface. Group them under `--preset`:

- `--preset minimal` → v1 behavior only (melody + harmony).
- `--preset symphonic` → all layers active, defaults as above. Becomes the new default once v1.3 ships.
- `--preset analytical` → bass drone + melody only, no pad, no envelope. For users who want to *hear the data* without orchestral sweetening.

Individual flags (`--bass-period`, `--pad-trigger-vol-threshold`, etc.) override the preset.

### What to skip until v2

- **Brass stabs on extreme-range candles.** Tempting but risks novelty-instrument cheese on a free soundfont.
- **Percussion / timpani on capitulation candles.** Same reason — drums on GeneralUser GS are usable but unflattering.
- **Movement detection / rests between sections.** Get moment-to-moment texture right first; form comes after.
- **Portamento on legato runs.** Sounds great with the right patch, awful with most. Skip until a soundfont is chosen.

### Soundfont note

Strings and pads are unforgiving — a bad soundfont will make v1.2+ sound worse than v1, not better. Recommend **FluidR3 GM** in the README as a free upgrade over GeneralUser GS once the user gets to v1.2.

## The output is source material, not a finished work

A multi-year BTC sonification is best understood as **mineable raw stock**, not a finished composition. Played end-to-end — even with the symphonic layers above — hours of grid-locked notes will feel monotonous. That isn't a defect; it's the nature of the artifact. The value comes from a producer or curator extracting the moments that actually sing: the eight bars where the COVID bottom resolved, the swelling passage during the run to $69k, the eerie consolidation before a halving.

This reframe changes a few design decisions. None of these are huge work — most are cheap additions that dramatically increase what the file is *useful for*.

### MIDI markers at structural events

Whenever the v1.2 pad logic detects a structural trigger (swing pivot, volatility regime shift, MA crossover), also emit a **MIDI marker meta-event** at that bar. A producer opening the `.mid` in Logic, Ableton, or Reaper gets a navigable timeline — "jump to next marker" instead of "scrub through 8 hours." Marker text should be human-readable: `swing_low_2020-03-12`, `vol_spike_2021-05-19`, `ma_cross_up_2023-01-14`.

Implementation: `mido.MetaMessage('marker', text=label, time=tick)`. Trivial to add, transformative for usability. Worth implementing alongside v1.2 (or earlier — see below).

### JSON sidecar

Alongside `output.mid`, write `output.json`:

```json
{
  "config": { /* full RunConfig used */ },
  "source": {
    "symbol": "BTC/USDT",
    "timeframe": "1d",
    "start": "2020-01-01",
    "end": "2024-12-31",
    "candle_count": 1825
  },
  "bars": [
    {"bar": 1, "tick_start": 0, "candle_index": 0, "date": "2020-01-01", "close": 7195.24, "event": null},
    {"bar": 253, "tick_start": 241920, "candle_index": 252, "date": "2020-09-09", "close": 10131.34, "event": "ma_cross_down"}
  ],
  "events": [
    {"bar": 71, "type": "swing_low", "candle_index": 70, "date": "2020-03-12", "label": "covid_crash_bottom"}
  ]
}
```

This is what makes the MIDI **legible**. Without it, bar 1247 is just bar 1247. With it, bar 1247 is "the day Bitcoin closed above $30k for the first time in 2023." It's also the data layer for any visual companion, and the bridge to content — a tweet, a chapter title, a video annotation.

### Stems export

Add `--export-stems` that writes one `.mid` per channel (`output.melody.mid`, `output.bass.mid`, `output.pad.mid`, etc.) in addition to the combined file. Producers want isolated tracks they can route to different instruments and process independently; a single combined file forces them to split it manually in their DAW.

### Visuals — promoted from stretch goal to v1.4

The original stretch goals listed "a static HTML output that renders the chart and the score side-by-side, synced." With the sidecar, this stops being a parallel re-derivation of the data and becomes a thin presentation layer: load the JSON, render a candlestick chart (TradingView Lightweight Charts or similar — both free, both lightweight), sync the playhead to MIDI bar positions, render event markers as labelled flags on the chart.

Target v1.4 deliverable: `btc-sonify --export-html output.html` produces a self-contained file with embedded MIDI playback (Web MIDI API + a JS soundfont player like `soundfont-player`), a synced candlestick chart, and clickable event markers that scrub the playhead. This is the artifact you'd actually share — music *and* data in one frame, the way a documentary is more than its score.

## Stretch goals (after v1.3 ships)

- A `--mode symphony` flag that splits the dataset into movements at major regime changes (detected via volatility clustering), with 2-bar rests between them.
- Just-intonation tuning option, where consonance correlates with Fibonacci retracement levels.
- Multi-asset mode: BTC as melody, ETH as counterpoint, on the same timeline.
- A static HTML output that renders the chart and the score side-by-side, synced.

These are notes for later. Ignore them during v1.

## First message to send Claude Code

> Read CLAUDE.md. Then set up the project skeleton from step 1 of the build order — pyproject.toml, the directory structure, an empty CLI entry point that responds to `--help`, and a README stub. Use uv for dependency management. Don't implement the mapping or fetching yet; we'll do that in subsequent steps. When done, show me the file tree and the output of `btc-sonify --help`.
