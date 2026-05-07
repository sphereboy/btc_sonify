"""HTML visualizer.

Generates a single self-contained HTML file that plays a user-provided
audio rendering of the .mid alongside a synced candlestick chart, a
movement timeline, and a "now playing" indicator. The page embeds all
candle and movement data as JSON literals — no external data fetches,
no build step. Drop the .mp3/.wav next to the .html, open in a browser.

Why HTML rather than MP4: shareable as a hosted URL on R2, interactive
(scrub, click movements to seek), runs anywhere a browser does, and
doesn't depend on fluidsynth being installed locally. We can do an
MP4 export later for social platforms, but the interactive HTML is the
better "send a teammate this link" artifact.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from btc_sonify.config import RunConfig
from btc_sonify.symphony import RenderedMovement
from btc_sonify.timeline import (
    TimelineCandle,
    TimelineMovement,
    compute_timeline,
)

# Re-exported for backward compatibility — older imports still resolve.
__all__ = [
    "TimelineCandle",
    "TimelineMovement",
    "compute_timeline",
    "write_visualization",
]


def _candle_payload(df: pd.DataFrame) -> list[dict]:
    """Compact dict-of-arrays would be smaller, but we want each entry
    to read like a candle for clarity. Numbers are rounded to 2 decimals
    to keep the JSON small."""
    timestamps = pd.to_datetime(df["timestamp"], utc=True)
    out = []
    for i, row in df.reset_index(drop=True).iterrows():
        out.append({
            "i": int(i),
            "t": timestamps.iloc[i].strftime("%Y-%m-%d"),
            "o": round(float(row["open"]), 2),
            "h": round(float(row["high"]), 2),
            "l": round(float(row["low"]), 2),
            "c": round(float(row["close"]), 2),
            "v": round(float(row["volume"]), 2),
        })
    return out


def write_visualization(
    df: pd.DataFrame,
    rendered_movements: list[RenderedMovement] | None,
    base_config: RunConfig,
    output_path: Path,
    audio_path: str,
    title: str,
    palette_name: str,
) -> Path:
    """Write the HTML visualizer next to the MIDI file. Returns the
    HTML path. ``audio_path`` should be a relative URL to the audio
    file the user will drop next to the HTML (e.g. "btc.mp3").
    """
    candles, movements = compute_timeline(rendered_movements, df, base_config)

    payload = {
        "title": title,
        "palette": palette_name,
        "audio": audio_path,
        "duration_s": (movements[-1].end_s if movements else 0),
        "n_candles": len(df),
        "candles_meta": _candle_payload(df),
        "candles_timeline": [
            {"i": c.idx, "s": round(c.start_s, 3), "d": round(c.duration_s, 3),
             "m": c.movement}
            for c in candles
        ],
        "movements": [
            {"i": m.index, "label": m.label, "dir": m.direction,
             "scale": m.scale, "root": m.root, "bpm": m.bpm,
             "s0": round(m.start_s, 3), "s1": round(m.end_s, 3),
             "i0": m.start_idx, "i1": m.end_idx}
            for m in movements
        ],
    }

    html = _render_html(payload)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path


def _render_html(payload: dict) -> str:
    """Inline the HTML/CSS/JS as a single string. We could move this to
    a separate template file later — for now keeping it here means the
    visualizer ships as one self-contained module."""
    data_json = json.dumps(payload, separators=(",", ":"))
    return _HTML_TEMPLATE.replace("__PAYLOAD__", data_json)


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BTC Sonify</title>
<style>
  :root {
    --bg: #0a0a14;
    --bg-2: #14141f;
    --fg: #e6e6f0;
    --dim: #6b7280;
    --grid: #1d1d2c;
    --bull: #22d3a4;
    --bear: #ff5277;
    --side: #fbbf24;
    --accent: #00ffe1;
    --accent-2: #ff00d4;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text",
                 "Helvetica Neue", Arial, sans-serif;
    background: var(--bg);
    color: var(--fg);
    font-size: 14px;
    min-height: 100vh;
  }
  .mono { font-family: "SF Mono", ui-monospace, "JetBrains Mono",
                       Menlo, Consolas, monospace; }
  header {
    padding: 24px 32px 16px;
    border-bottom: 1px solid var(--grid);
  }
  h1 {
    margin: 0 0 6px;
    font-size: 22px;
    font-weight: 600;
    letter-spacing: -0.01em;
  }
  .subtitle {
    color: var(--dim);
    font-size: 13px;
  }
  .subtitle .pal {
    color: var(--accent);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-size: 11px;
  }
  main {
    padding: 16px 32px 24px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  .audio-row {
    display: flex;
    gap: 16px;
    align-items: center;
  }
  audio {
    flex: 1;
    height: 32px;
  }
  .chart-wrap {
    position: relative;
    width: 100%;
    height: 360px;
    background: var(--bg-2);
    border-radius: 6px;
    overflow: hidden;
  }
  canvas {
    display: block;
    width: 100%;
    height: 100%;
    cursor: crosshair;
  }
  .movement-strip {
    display: flex;
    width: 100%;
    height: 28px;
    border-radius: 4px;
    overflow: hidden;
    background: var(--bg-2);
    font-size: 11px;
  }
  .movement-strip > div {
    display: flex;
    align-items: center;
    justify-content: center;
    border-right: 1px solid var(--bg);
    cursor: pointer;
    padding: 0 6px;
    color: var(--fg);
    text-align: center;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
    transition: opacity 0.15s;
  }
  .movement-strip > div:last-child { border-right: none; }
  .movement-strip > div.bull { background: rgba(34,211,164,0.18); }
  .movement-strip > div.bear { background: rgba(255,82,119,0.22); }
  .movement-strip > div.sideways { background: rgba(251,191,36,0.18); }
  .movement-strip > div.active.bull { background: rgba(34,211,164,0.55); }
  .movement-strip > div.active.bear { background: rgba(255,82,119,0.65); }
  .movement-strip > div.active.sideways { background: rgba(251,191,36,0.55); }
  .movement-strip > div:hover { opacity: 0.85; }
  .now-playing {
    background: var(--bg-2);
    border-radius: 6px;
    padding: 14px 18px;
    display: grid;
    grid-template-columns: minmax(220px, 1fr) auto auto auto auto;
    gap: 14px 28px;
    align-items: baseline;
  }
  .now-playing .label {
    color: var(--dim);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 2px;
  }
  .now-playing .value {
    font-size: 15px;
    color: var(--fg);
  }
  .now-playing .movement-name {
    font-size: 18px;
    font-weight: 500;
    color: var(--accent);
  }
  .now-playing .change.up { color: var(--bull); }
  .now-playing .change.down { color: var(--bear); }
  footer {
    padding: 12px 32px 24px;
    color: var(--dim);
    font-size: 11px;
    text-align: right;
  }
</style>
</head>
<body>
<header>
  <h1 id="title">BTC Sonify</h1>
  <div class="subtitle">
    <span class="pal" id="palette-name">classical</span>
    &nbsp;·&nbsp;
    <span id="meta-stats"></span>
  </div>
</header>

<main>
  <div class="audio-row">
    <audio id="audio" controls preload="metadata"></audio>
  </div>

  <div class="movement-strip" id="movement-strip"></div>

  <div class="chart-wrap">
    <canvas id="chart"></canvas>
  </div>

  <div class="now-playing">
    <div>
      <div class="label">Now playing</div>
      <div class="movement-name" id="np-movement">—</div>
    </div>
    <div>
      <div class="label">Date</div>
      <div class="value mono" id="np-date">—</div>
    </div>
    <div>
      <div class="label">Close</div>
      <div class="value mono" id="np-price">—</div>
    </div>
    <div>
      <div class="label">Δ Day</div>
      <div class="value mono change" id="np-change">—</div>
    </div>
    <div>
      <div class="label">Scale · BPM</div>
      <div class="value mono" id="np-scale">—</div>
    </div>
  </div>
</main>

<footer>btc-sonify · click any candle to seek · click any movement chip to jump</footer>

<script>
const DATA = __PAYLOAD__;

// ---- Setup -----------------------------------------------------------
const audio = document.getElementById('audio');
audio.src = DATA.audio;

document.getElementById('title').textContent = DATA.title;
document.getElementById('palette-name').textContent = DATA.palette;
const dur = DATA.duration_s;
const mins = Math.floor(dur / 60), secs = Math.round(dur % 60);
document.getElementById('meta-stats').textContent =
  `${DATA.n_candles} candles · ${DATA.movements.length} movements · ${mins}:${String(secs).padStart(2, '0')}`;

// ---- Movement strip --------------------------------------------------
const stripEl = document.getElementById('movement-strip');
const totalDur = DATA.duration_s || 1;
DATA.movements.forEach(m => {
  const el = document.createElement('div');
  el.className = m.dir;
  el.style.flex = String(Math.max(0.001, m.s1 - m.s0));
  el.title = `${m.label} · ${m.scale} ${m.root} · ${m.bpm} BPM · ${m.dir}`;
  // Show roman numeral when narrow, full label when wide
  el.textContent = m.label.split('.')[0] + '.';
  el.dataset.idx = m.i;
  el.addEventListener('click', () => {
    audio.currentTime = m.s0;
    if (audio.paused) audio.play();
  });
  stripEl.appendChild(el);
});

// ---- Chart -----------------------------------------------------------
const canvas = document.getElementById('chart');
const ctx = canvas.getContext('2d');

function resizeCanvas() {
  const dpr = window.devicePixelRatio || 1;
  const r = canvas.getBoundingClientRect();
  canvas.width = Math.round(r.width * dpr);
  canvas.height = Math.round(r.height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
window.addEventListener('resize', () => { resizeCanvas(); draw(); });
resizeCanvas();

const candles = DATA.candles_meta;
const padding = { top: 18, right: 56, bottom: 22, left: 6 };
const minPrice = Math.min(...candles.map(c => c.l));
const maxPrice = Math.max(...candles.map(c => c.h));
const priceRange = maxPrice - minPrice;

function chartCoords() {
  const r = canvas.getBoundingClientRect();
  return {
    w: r.width, h: r.height,
    iw: r.width - padding.left - padding.right,
    ih: r.height - padding.top - padding.bottom,
  };
}

function priceToY(price, c) {
  return padding.top + c.ih * (1 - (price - minPrice) / priceRange);
}

function idxToX(i, c) {
  return padding.left + (i + 0.5) / candles.length * c.iw;
}

function timeToX(t, c) {
  // Find candle at time t and interpolate
  if (t <= DATA.candles_timeline[0].s) return idxToX(0, c);
  if (t >= DATA.duration_s) return idxToX(candles.length - 1, c);
  // binary search
  const tl = DATA.candles_timeline;
  let lo = 0, hi = tl.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (tl[mid].s + tl[mid].d <= t) lo = mid + 1;
    else hi = mid;
  }
  const cnd = tl[lo];
  const frac = (t - cnd.s) / cnd.d;
  return idxToX(cnd.i, c) + frac * (c.iw / candles.length);
}

function xToTime(x, c) {
  // Convert pixel x → audio time via candle index lookup
  const norm = (x - padding.left) / c.iw;
  const i = Math.max(0, Math.min(candles.length - 1, Math.floor(norm * candles.length)));
  const cnd = DATA.candles_timeline[i] || DATA.candles_timeline[DATA.candles_timeline.length - 1];
  return cnd.s + cnd.d * 0.5;
}

function draw() {
  const c = chartCoords();
  ctx.clearRect(0, 0, c.w, c.h);

  // Background grid
  ctx.strokeStyle = 'rgba(255,255,255,0.04)';
  ctx.lineWidth = 1;
  for (let i = 1; i < 5; i++) {
    const y = padding.top + (c.ih * i / 5);
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(c.w - padding.right, y);
    ctx.stroke();
  }

  // Movement bands (subtle backdrop)
  for (const m of DATA.movements) {
    const x0 = idxToX(m.i0, c) - (c.iw / candles.length / 2);
    const x1 = idxToX(m.i1, c) + (c.iw / candles.length / 2);
    const colors = {
      bull: 'rgba(34,211,164,0.04)',
      bear: 'rgba(255,82,119,0.06)',
      sideways: 'rgba(251,191,36,0.04)',
    };
    ctx.fillStyle = colors[m.dir];
    ctx.fillRect(x0, padding.top, x1 - x0, c.ih);
  }

  // Candles
  const candleWidth = Math.max(1, c.iw / candles.length * 0.85);
  for (const cnd of candles) {
    const x = idxToX(cnd.i, c);
    const yo = priceToY(cnd.o, c);
    const yc = priceToY(cnd.c, c);
    const yh = priceToY(cnd.h, c);
    const yl = priceToY(cnd.l, c);
    const isGreen = cnd.c >= cnd.o;
    ctx.strokeStyle = isGreen ? '#22d3a4' : '#ff5277';
    ctx.fillStyle = isGreen ? 'rgba(34,211,164,0.85)' : 'rgba(255,82,119,0.85)';
    // Wick
    ctx.beginPath();
    ctx.moveTo(x, yh);
    ctx.lineTo(x, yl);
    ctx.stroke();
    // Body
    const bodyTop = Math.min(yo, yc);
    const bodyH = Math.max(1, Math.abs(yc - yo));
    ctx.fillRect(x - candleWidth / 2, bodyTop, candleWidth, bodyH);
  }

  // Y-axis labels (price)
  ctx.fillStyle = '#6b7280';
  ctx.font = '10px ui-monospace, monospace';
  ctx.textAlign = 'left';
  ctx.textBaseline = 'middle';
  for (let i = 0; i <= 5; i++) {
    const price = minPrice + (priceRange * (5 - i) / 5);
    const y = padding.top + c.ih * i / 5;
    ctx.fillText('$' + Math.round(price).toLocaleString(), c.w - padding.right + 6, y);
  }

  // Date labels (top axis, every ~7th of the way)
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  for (let i = 0; i < 7; i++) {
    const idx = Math.round(candles.length * i / 6);
    if (idx >= candles.length) continue;
    const x = idxToX(idx, c);
    ctx.fillText(candles[idx].t, x, 4);
  }

  // Playhead
  const t = audio.currentTime || 0;
  const px = timeToX(t, c);
  // Halo
  const grad = ctx.createLinearGradient(px - 30, 0, px + 30, 0);
  grad.addColorStop(0, 'rgba(0,255,225,0)');
  grad.addColorStop(0.5, 'rgba(0,255,225,0.18)');
  grad.addColorStop(1, 'rgba(0,255,225,0)');
  ctx.fillStyle = grad;
  ctx.fillRect(px - 30, padding.top, 60, c.ih);
  // Line
  ctx.strokeStyle = '#00ffe1';
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(px, padding.top);
  ctx.lineTo(px, c.h - padding.bottom);
  ctx.stroke();
}

// ---- Now-playing update ---------------------------------------------
function findCandleAtTime(t) {
  const tl = DATA.candles_timeline;
  if (t <= tl[0].s) return tl[0];
  if (t >= tl[tl.length - 1].s + tl[tl.length - 1].d) return tl[tl.length - 1];
  let lo = 0, hi = tl.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (tl[mid].s + tl[mid].d <= t) lo = mid + 1;
    else hi = mid;
  }
  return tl[lo];
}

function findMovementAtTime(t) {
  for (const m of DATA.movements) {
    if (t >= m.s0 && t < m.s1) return m;
  }
  return DATA.movements[DATA.movements.length - 1];
}

function fmtPrice(p) {
  return '$' + p.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function updateUI() {
  const t = audio.currentTime || 0;
  const cnd = findCandleAtTime(t);
  const meta = candles[cnd.i];
  const mov = findMovementAtTime(t);

  document.getElementById('np-movement').textContent = mov.label;
  document.getElementById('np-date').textContent = meta.t;
  document.getElementById('np-price').textContent = fmtPrice(meta.c);

  const change = ((meta.c - meta.o) / meta.o) * 100;
  const chEl = document.getElementById('np-change');
  chEl.textContent = (change >= 0 ? '+' : '') + change.toFixed(2) + '%';
  chEl.className = 'value mono change ' + (change >= 0 ? 'up' : 'down');

  document.getElementById('np-scale').textContent =
    `${mov.scale} ${mov.root} · ${mov.bpm} BPM`;

  // Highlight active movement chip
  const chips = stripEl.children;
  for (let i = 0; i < chips.length; i++) {
    chips[i].classList.toggle('active', Number(chips[i].dataset.idx) === mov.i);
  }
}

// ---- Animation loop --------------------------------------------------
function frame() {
  draw();
  updateUI();
  requestAnimationFrame(frame);
}

// ---- Click-to-seek ---------------------------------------------------
canvas.addEventListener('click', (e) => {
  const rect = canvas.getBoundingClientRect();
  const x = e.clientX - rect.left;
  const c = chartCoords();
  audio.currentTime = xToTime(x, c);
  if (audio.paused) audio.play();
});

frame();
</script>
</body>
</html>
"""
