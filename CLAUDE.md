# Bitcoin Candlestick Sonification — Project Handoff

## What we're building

A Python CLI tool that converts Bitcoin OHLCV candlestick data into music. The user provides a date range, timeframe, and musical scale; the tool outputs a MIDI file (and optionally a rendered WAV/MP3) where each candle becomes a note with articulation, dynamics, and ornamentation derived from the candle's properties.

This is a **data sonification** project, not a generative-music toy. The mapping should be deterministic and auditable — the same candles in the same scale should always produce the same MIDI. Musicality comes from a thoughtful mapping, not randomness.

## Project owner context

The owner runs multiple SaaS products and has a strong design sensibility. He's also into prediction markets, sacred geometry, and the harmonic ratios (3:2, 4:3, 1.618) that connect music theory and market analysis. Treat this as a real project that might become content — the code should be clean enough to share, the output should be musically defensible, and the README should communicate the *why* as well as the how.

His primary dev environment is Cursor + Claude Code. Python is fine but he's not a Python specialist — favor readable, well-commented code over clever code. He'll run this on macOS (Miami).

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

**Velocity (loudness) ← Volume.** Normalize volume to the MIDI velocity range (40–127, never go below 40 — silence isn't musical). Use a logarithmic scale because volume distributions are heavy-tailed; raw linear mapping would make most notes whisper-quiet with rare booms.

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
- Don't try to be clever about timing — every candle gets equal duration. Variable duration based on volatility was considered and rejected because it makes the output harder to follow as music.
- Don't pull in `music21`. It's a heavy dependency for what we need; `mido` + custom scale logic is leaner.

## Stretch goals (after v1 ships)

- A `--mode symphony` flag that splits the dataset into movements at major regime changes (detected via volatility clustering).
- Just-intonation tuning option, where consonance correlates with Fibonacci retracement levels.
- Multi-asset mode: BTC as melody, ETH as counterpoint, on the same timeline.
- A static HTML output that renders the chart and the score side-by-side, synced.

These are notes for later. Ignore them during v1.

## First message to send Claude Code

> Read CLAUDE.md. Then set up the project skeleton from step 1 of the build order — pyproject.toml, the directory structure, an empty CLI entry point that responds to `--help`, and a README stub. Use uv for dependency management. Don't implement the mapping or fetching yet; we'll do that in subsequent steps. When done, show me the file tree and the output of `btc-sonify --help`.
