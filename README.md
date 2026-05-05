# btc-sonify

A Python CLI tool that converts Bitcoin OHLCV candlestick data into music. Provide a date range, timeframe, and musical scale; the tool outputs a MIDI file (and optionally a rendered WAV) where each candle becomes a note with articulation, dynamics, and ornamentation derived from the candle's properties. This is a data sonification project, not a generative-music toy — the same candles in the same scale always produce the same MIDI. Musicality comes from a thoughtful mapping, not randomness.

## Status

Step 1 of 8 complete: project skeleton. **Not yet functional** — running `btc-sonify` currently prints the parsed arguments and exits.

## Install

Requires Python 3.11+ and [uv](https://github.com/astral-sh/uv).

```bash
uv venv
uv pip install -e ".[dev]"
```

For optional WAV rendering (requires FluidSynth via Homebrew):

```bash
brew install fluid-synth
uv pip install -e ".[dev,audio]"
```

## Usage

```bash
# Basic
btc-sonify --start 2020-01-01 --end 2024-12-31 --timeframe 1d

# All options
btc-sonify \
  --start 2020-01-01 \
  --end 2024-12-31 \
  --timeframe 1d \
  --scale phrygian \
  --root A \
  --octaves 3 \
  --bpm 120 \
  --note-value quarter \
  --output ./output/btc-2020-2024.mid \
  --render-wav \
  --soundfont ./soundfonts/GeneralUser.sf2
```

Run `btc-sonify --help` for the full option list.

## Roadmap

- [x] **1.** Project skeleton + `pyproject.toml`
- [ ] **2.** `scales.py` — scale definitions + pitch quantization
- [ ] **3.** `data.py` — OHLCV fetching via ccxt with parquet cache
- [ ] **4.** `mapping.py` — OHLCV → MIDI event list (the core mapping)
- [ ] **5.** `midi_writer.py` — write `.mid` files via mido
- [ ] **6.** `cli.py` — wire everything together with progress bars
- [ ] **7.** `render.py` — optional WAV render via FluidSynth
- [ ] **8.** Full README with mapping philosophy and audio samples

See `CLAUDE.md` for the full mapping spec and project handoff.
