# btc-sonify

> Listen to Bitcoin.

A Python CLI that turns Bitcoin's price history into music. Each daily candle becomes a note: pitch from the close, loudness from the volume, articulation from the body, ornaments from the wicks, harmony from the range. The same data always produces the same MIDI — this is **data sonification**, not generative music. Musicality comes from a thoughtful mapping, not randomness.

```bash
btc-sonify --start 2020-01-01 --end 2024-12-31 --scale phrygian
# 1827 candles → 15 minutes of haunted Bitcoin
```

## Why

Markets and music share a vocabulary: tension and release, motif and variation, the ratios that the ear finds consonant (3:2, 4:3, 1.618). A candlestick chart already encodes contour, dynamics, and rhythm — it's been a musical score the whole time. We just hadn't been listening.

The mapping here is deliberately strict. There's no random walk, no neural net, no "stylized improvisation." Twelve well-defined rules turn OHLCV into MIDI. Bull markets sound like ascending lines; capitulation candles slam like staccato chords; dojis trill in indecision. The 2020 halving rally and the 2022 collapse have distinct, recognisable shapes when you put them through the same scale.

Default mode is **Phrygian**. It fits Bitcoin's character — searching, modal, slightly haunted.

## Install

Requires **Python 3.11+** and [uv](https://github.com/astral-sh/uv).

```bash
git clone <this repo>
cd btc_sonify
uv venv
uv pip install -e ".[dev]"
```

Optional, for rendering directly to WAV instead of opening the .mid in a DAW:

```bash
brew install fluid-synth                                     # macOS
# or:  apt install fluidsynth   (Debian/Ubuntu)
# Download a free GeneralUser GS soundfont (.sf2) — e.g.:
# https://schristiancollins.com/generaluser.php
```

## Quickstart

```bash
# Default: phrygian, A3 root, 3 octaves, 120 BPM, daily candles
btc-sonify --start 2020-01-01 --end 2024-12-31

# Q4 2023 bull run, in pentatonic minor at 90 BPM
btc-sonify \
  --start 2023-10-01 --end 2023-12-31 \
  --scale pentatonic_minor --root D --bpm 90 \
  --output output/btc-q4-2023.mid

# Full pipeline including WAV
btc-sonify \
  --start 2020-01-01 --end 2024-12-31 \
  --scale hijaz --octaves 4 \
  --output output/btc-2020-2024.mid \
  --render-wav --soundfont ./soundfonts/GeneralUser.sf2
```

Output is a `.mid` file. Drop it into Logic, Ableton, GarageBand, or your DAW of choice — both tracks are tagged with the right General MIDI program (Acoustic Grand for melody, String Ensemble 1 for harmony) so it sounds reasonable on any GM-compatible synth.

Run `btc-sonify --help` for the full option list.

## The mapping

Every axis below is exposed as a CLI flag or config field, so this is the starting point — not the only point.

| Candle property | → | Musical dimension |
|---|---|---|
| **Close price** (min-max normalized over the dataset) | → | **Pitch** (snapped to scale across N octaves) |
| **Volume** (log-normalized) | → | **Velocity** (loudness, 40–127) |
| **Direction × body ratio** | → | **Articulation** (legato / normal / marcato / staccato) |
| **Body ratio < 0.1** (doji) | → | **Trill** between close and one scale-step above |
| **Upper wick > 2× body** | → | Grace note one scale-step **above** |
| **Lower wick > 2× body** | → | Grace note one scale-step **below** |
| **High–low range** (terciles) | → | **Harmony** chord size: single → diad → triad |
| **Open / High / Low / Close** (within candle) | → | **Within-candle motion** — non-doji bars play 2 or 4 sub-notes traversing the OHLC sequence, so the melody contours *with* the price action instead of sitting on the close |
| **Volume bottom decile** | → | **Rest** — quiet bars drop the melody, harmony pad carries through (the breath) |

The harmony track plays underneath at 60% velocity on a different MIDI channel, so it sustains like a pad while the melody articulates the candle's shape.

A few specifics worth knowing:

- **Why min-max over the whole series, not rolling.** We want the macro shape audible — a 5-year run should *sound like* a five-year run, not five repeats of the same arc.
- **Why log-scale velocity.** Volume distributions are heavy-tailed. Linear normalization makes the median candle whisper-quiet and the rare booms deafening. Log compresses the tail.
- **Why no random.** The same dataset in the same scale always produces the same MIDI. You can A/B two scales on the same range and trust the differences are the scales, not noise.
- **Why scale-aware intervals.** The "fifth" in the harmony chord is *4 scale-degrees up*, not 7 semitones. In phrygian or hijaz that produces a defensibly modal chord; chromatic intervals would clash.
- **Why within-candle motion.** A static one-note-per-candle melody sounds like a metronome with a pitch wheel. Real candles tell a story — open, the wick excursions, the close. Playing those four points in narrative order (green: dipped then rallied; red: probed up then gave back) gives the melody contour *inside* every beat. Same data, ~70% more notes, dramatically more shape.
- **Why humanization.** Real performers are imperfect on purpose. Velocity gets ±8 jitter and onsets ±3 ticks, both deterministically seeded from the candle index. Determinism is preserved (same input → same MIDI) but the lead stops sounding step-sequenced. Set `--no-humanize` if you want the strict mechanical feel.

Full spec lives in [`CLAUDE.md`](CLAUDE.md).

## Scales

Eight scales are built in, each with a different emotional fingerprint:

| Scale | Vibe | Good for |
|---|---|---|
| `major` | Bright, resolved | Sustained bull markets |
| `minor` | Sombre, classical | Bear cycles |
| `pentatonic_major` | Bright, "no wrong notes" | Newcomer-friendly listening |
| `pentatonic_minor` | Bluesy, contemplative | Sideways markets |
| `dorian` | Modal, hopeful-melancholy | Long mid-cycle stretches |
| `phrygian` *(default)* | Searching, haunted | Bitcoin's whole character |
| `hijaz` | Arabic / Middle Eastern, mystical | Anything that wants drama |
| `blues` | Gritty, ambivalent | Capitulation candles, fakeouts |

## Examples

> *Sample MP3s coming — to be hosted on Cloudflare R2.*

- 🔊 *2020–2024 BTC in phrygian* — `[link]`
- 🔊 *The 2022 collapse in minor* — `[link]`
- 🔊 *Q4 2023 ETF rally in hijaz* — `[link]`
- 🔊 *2017 mania in major pentatonic* — `[link]`

Generate your own:

```bash
btc-sonify --start 2022-05-01 --end 2022-12-31 --scale minor --bpm 80
```

## Architecture

```
btc_sonify/
├── scales.py        # Scale definitions and pitch quantization (pure math)
├── data.py          # OHLCV fetching via ccxt + parquet caching
├── config.py        # RunConfig dataclass — every knob in one place
├── mapping.py       # OHLCV DataFrame → list of MIDI events (the heart)
├── midi_writer.py   # Event list → Type-1 SMF via mido
├── render.py        # Optional WAV render via fluidsynth CLI
└── cli.py           # Typer entry point — wires the pipeline
```

Each layer is a pure function with no global state. The CLI is just orchestration — every step is unit-tested in isolation, plus a fixture-based end-to-end suite.

```bash
uv run pytest               # 264 tests, ~1.5s
```

## Palettes

The default sound is classical (acoustic grand + strings + acoustic kit). For modern arrangements, pick a `--palette`:

```bash
btc-sonify --start 2020-01-01 --end 2024-12-31 --mode symphony --palette synthwave
```

Each palette swaps the melody / harmony / drum-kit programs and adds a sub-bass track on a fourth channel:

| Palette | Melody | Harmony | Drums | Bass | Voice |
|---|---|---|---|---|---|
| `classical` *(default)* | Acoustic Grand | String Ensemble | Standard kit | — | Choir Aahs |
| `synthwave` | Sawtooth Lead | Warm Pad | Electronic kit | Synth Bass 1 | Voice Oohs |
| `cinematic` | Synth Brass | Sweep Pad | Power kit | Synth Bass 2 | Pad 4 (choir) |
| `electronic` | Rhodes Electric | Bowed Pad | TR-808 kit | Synth Bass 1 | Synth Voice |

**Voice track** is a sustained "lead vocal" line: it samples a smoothed close price every 4 candles, quantizes to scale, shifts up an octave, and holds the note for the full 4-candle window. Floats above the synth lead the way a vocalist would over an instrumental — same data, but tracing the macro arc instead of every candle's detail. This is the layer that turns "instrumental" into "produced track."

The same musical content (mapping, scale, articulation, ornaments) plays through all four — they're four different lenses on the same sonification. The sub-bass doubles the close note one octave below at 70% melody velocity, sustaining underneath like a synth pad. Classical has no bass on purpose — orchestral arrangements rarely need a sub layer.

You can override individual instruments in your DAW after import: drag any of Logic's stock plug-ins (Alchemy, Sculpture, Drum Machine Designer) onto the right track to push the modernization further than a GM program number can.

## Visualizer

Add `--visualize` to write a self-contained HTML page next to the `.mid`:

```bash
btc-sonify --start 2020-01-01 --end 2026-05-05 --mode symphony \
  --palette synthwave --visualize \
  --output output/btc.mid
# → output/btc.mid + output/btc.html
```

Open the HTML in any browser. You'll see a candlestick chart of the source data with a scrubbing playhead, a clickable movement timeline, and a "now playing" panel showing the current date / price / movement / scale / BPM. Click any candle or any movement chip to seek.

For audio, export your MIDI from Logic / GarageBand / fluidsynth as an `.mp3` (default expected name is `<output-stem>.mp3` — i.e. `btc.mp3` next to `btc.html`) and drop it in the same folder. The `<audio>` tag in the page will pick it up. Override with `--audio-file my-render.mp3` if your filename differs.

The HTML is fully self-contained — embedded candle data + movement metadata + inline CSS/JS, no external dependencies, no build step. Upload it (and the audio file) to Cloudflare R2 or any static host and you've got a shareable URL.

## Symphony mode

For long ranges (multi-year), single-key sonification can blur the macro narrative. `--mode symphony` segments the timeline at major price-action pivots and turns each segment into a labelled movement:

```bash
btc-sonify --start 2020-01-01 --end 2024-12-31 --mode symphony \
  --output output/btc-symphony.mid
```

What changes per movement:

- **Direction-aware scale** — bull movements use Dorian (bright modal), bear use Phrygian (haunted), sideways use Hijaz (ambiguous, modal). If you pass `--scale` explicitly, it's preserved across all movements.
- **Root modulates by a perfect fifth** between adjacent movements (A → E → B → F# → C# → G# → …) — classical tonal motion that gives the symphony shape.
- **Tempo bumps +20%** on movements with above-median realised volatility.
- **One-beat rest** between movements — the "breath" that announces a new section.
- **Crash cymbal** marks each transition (third channel — see below).

Movements are auto-detected via peak-trough segmentation (default 20% excursion threshold). To force a specific count:

```bash
--mode symphony --movements 4
```

Each run prints a movement breakdown showing what was detected and what choices were made:

```
┏━━━━┳━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━┳━━━━━━┳━━━━━┓
┃ #  ┃ label               ┃ candles ┃  return ┃ scale    ┃ root ┃ BPM ┃
┡━━━━╇━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━╇━━━━━━╇━━━━━┩
│ 1  │ I. Bear 2020        │      72 │  -33.0% │ phrygian │ A    │ 144 │
│ 2  │ II. Bull 2020-2021  │     303 │ +741.5% │ dorian   │ E    │ 120 │
│ 3  │ III. Bull 2021      │      45 │  +41.5% │ dorian   │ B    │ 144 │
│ …  │ …                   │     …   │   …     │   …      │ …    │ …   │
└────┴─────────────────────┴─────────┴─────────┴──────────┴──────┴─────┘
```

## Percussion track

Symphony mode adds a fourth track on the GM standard drum channel (channel 9), playing kit-style drums derived from candle properties:

- **Closed hi-hat** on every candle — the steady heartbeat.
- **Kick drum** on top-decile volume candles — punctuates the big bars.
- **Snare** on top-decile range candles — wide-range slaps.
- **Ride bell** on doji candles — indecision shimmer.
- **Crash cymbal** at each movement boundary.

Drums sit at 30–65% of the melody's velocity ceiling so they support rather than bulldoze the harmonic content above.

## All flags

```
--start         Start date (YYYY-MM-DD)
--end           End date (YYYY-MM-DD)
--timeframe     1m, 5m, 15m, 30m, 1h, 4h, 1d (default), 1w
--scale         Scale name (default: phrygian; in symphony mode only
                takes effect if explicitly set)
--root          Root note A..G with optional #/b (default: A)
--octaves       Pitch span in octaves (default: 3)
--bpm           Tempo (default: 120)
--note-value    quarter (default), eighth, half — duration of one candle
--output        Output .mid path (default: ./output/btc.mid)
--mode          plain (default) or symphony — see above
--movements     Symphony only: force exactly N movements (default: auto)
--palette       Instrument palette: classical (default), synthwave,
                cinematic, electronic — see above
--visualize     Also write an interactive HTML visualizer next to the .mid
--audio-file    Audio filename to embed in the visualizer (default:
                <output-stem>.mp3)
--render-wav    Also produce a WAV (requires --soundfont)
--soundfont     Path to .sf2 soundfont
--exchange      ccxt exchange ID (default: binanceus; binance.com is
                geo-blocked from US IPs)
--symbol        Trading pair (default: BTC/USDT)
--no-cache      Bypass the parquet cache and refetch
```

OHLCV data is cached in `~/.cache/btc-sonify/` as parquet files keyed by exchange + symbol + timeframe + date range. Cache hits short-circuit the network entirely. Use `--no-cache` to refetch.

## Caveats

- Defaults to **Binance.US** because binance.com returns HTTP 451 from US IPs. Pass `--exchange kraken` or `--exchange coinbase` for other sources; the candle data will differ slightly between exchanges.
- The cache is keyed by exact date range, so `2020-01-01..2024-12-31` and `2020-01-02..2024-12-31` are independent fetches. This is by design — if you want to extend a range, just re-fetch.
- WAV rendering requires the `fluidsynth` binary on PATH and a soundfont file. Without those, you still get a valid `.mid` to open in any DAW.

## Roadmap

Future ideas (notes for later, not commitments):

- Just-intonation tuning, where consonance correlates with Fibonacci retracement levels
- Multi-asset: BTC as melody, ETH as counterpoint, on the same timeline
- Static HTML output that renders chart and score side-by-side, scrubbing in sync
- MP4 export with the candle chart scrubbing in time with the audio

## License

MIT.
