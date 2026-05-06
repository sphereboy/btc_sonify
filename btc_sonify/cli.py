"""Command-line entry point for btc-sonify.

Wires the pure layers — fetch → map → write → render — into a single
deterministic command. Two arrangement modes:

- **plain** (default): one tempo, one key, melody + harmony.
- **symphony**: split the timeline into movements at major price-action
  pivots, modulate root by a perfect fifth between movements, bump
  tempo on high-volatility movements, add a percussion track.
"""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from btc_sonify.bass import map_bass
from btc_sonify.config import PALETTES, RunConfig
from btc_sonify.data import DEFAULT_EXCHANGE, DEFAULT_SYMBOL, fetch_ohlcv
from btc_sonify.mapping import map_candles_to_events
from btc_sonify.midi_writer import TempoChange, write_midi
from btc_sonify.percussion import MovementOffset, map_percussion
from btc_sonify.scales import SCALES
from btc_sonify.symphony import detect_movements, map_symphony
from btc_sonify.visualize import write_visualization
from btc_sonify.voice import map_voice

app = typer.Typer(
    add_completion=False,
    help="Convert Bitcoin OHLCV candlestick data into MIDI music.",
)


@app.command()
def sonify(
    start: str = typer.Option(..., "--start", help="Start date (YYYY-MM-DD)."),
    end: str = typer.Option(..., "--end", help="End date (YYYY-MM-DD)."),
    timeframe: str = typer.Option(
        "1d", "--timeframe", help="Candle timeframe: 1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w."
    ),
    scale: str = typer.Option(
        "phrygian",
        "--scale",
        help=(
            "Musical scale: major, minor, pentatonic_major, pentatonic_minor, "
            "dorian, phrygian, hijaz, blues. In symphony mode this only takes "
            "effect if explicitly set; otherwise per-movement scales are auto-picked."
        ),
    ),
    root: str = typer.Option("A", "--root", help="Root note (A, A#, B, C, ...)."),
    octaves: int = typer.Option(3, "--octaves", help="Number of octaves to span."),
    bpm: int = typer.Option(120, "--bpm", help="Tempo in beats per minute."),
    note_value: str = typer.Option(
        "quarter",
        "--note-value",
        help="Note duration per candle: quarter, eighth, half.",
    ),
    output: str = typer.Option(
        "./output/btc.mid", "--output", help="Output .mid file path."
    ),
    mode: str = typer.Option(
        "plain",
        "--mode",
        help="Arrangement: plain (single tempo + key) or symphony (movements + drums).",
    ),
    movements: int | None = typer.Option(
        None,
        "--movements",
        help="Symphony only: force exactly N equal-sized movements (default: auto-detect).",
    ),
    palette: str = typer.Option(
        "classical",
        "--palette",
        help="Instrument palette: classical, synthwave, cinematic, electronic.",
    ),
    rubato: bool | None = typer.Option(
        None,
        "--rubato/--no-rubato",
        help=(
            "Enable within-movement tempo breathing (rallentando into "
            "structural events, accelerando through trends). Default is "
            "palette-dependent: on for classical/cinematic, off for "
            "synthwave/electronic. Symphony mode only."
        ),
    ),
    visualize: bool = typer.Option(
        False,
        "--visualize",
        is_flag=True,
        help="Also write an interactive HTML visualizer next to the .mid.",
    ),
    audio_file: str | None = typer.Option(
        None,
        "--audio-file",
        help="Audio filename to embed in the visualizer (default: <output>.mp3).",
    ),
    render_wav: bool = typer.Option(
        False,
        "--render-wav",
        is_flag=True,
        help="Render to WAV via FluidSynth (requires --soundfont).",
    ),
    soundfont: str | None = typer.Option(
        None, "--soundfont", help="Path to .sf2 soundfont file."
    ),
    exchange: str = typer.Option(
        DEFAULT_EXCHANGE,
        "--exchange",
        help="ccxt exchange ID (binanceus default; binance.com is geo-blocked from US).",
    ),
    symbol: str = typer.Option(
        DEFAULT_SYMBOL, "--symbol", help="Trading pair, e.g. BTC/USDT or BTC/USD."
    ),
    no_cache: bool = typer.Option(
        False, "--no-cache", is_flag=True, help="Bypass the on-disk OHLCV cache."
    ),
) -> None:
    """Render BTC OHLCV candles in the given range as a MIDI file."""
    console = Console()

    # --- Validate inputs early so we fail before hitting the network ----
    if scale not in SCALES:
        console.print(f"[red]Unknown scale {scale!r}. Choose from: {', '.join(sorted(SCALES))}[/red]")
        raise typer.Exit(code=2)
    if note_value not in ("quarter", "eighth", "half"):
        console.print(f"[red]--note-value must be quarter, eighth, or half (got {note_value!r}).[/red]")
        raise typer.Exit(code=2)
    if mode not in ("plain", "symphony"):
        console.print(f"[red]--mode must be 'plain' or 'symphony' (got {mode!r}).[/red]")
        raise typer.Exit(code=2)
    if palette not in PALETTES:
        console.print(f"[red]Unknown palette {palette!r}. Choose from: {', '.join(PALETTES)}[/red]")
        raise typer.Exit(code=2)
    if render_wav and not soundfont:
        console.print("[red]--render-wav requires --soundfont (path to a .sf2 file).[/red]")
        raise typer.Exit(code=2)
    if movements is not None and mode != "symphony":
        console.print("[red]--movements is only valid with --mode symphony.[/red]")
        raise typer.Exit(code=2)

    base_config = RunConfig(
        scale=scale, root=root, octaves=octaves, bpm=bpm, note_value=note_value,
    ).with_palette(PALETTES[palette])
    # CLI --rubato/--no-rubato overrides the palette default when supplied.
    if rubato is not None:
        base_config = base_config.with_rubato(rubato)
    user_specified_scale = scale != "phrygian"  # default; symphony picks per-movement

    # --- Fetch OHLCV with a progress bar ------------------------------
    console.print(f"[cyan]Fetching {symbol} {timeframe} from {exchange} ({start} → {end})…[/cyan]")
    df = _fetch_with_progress(
        console=console,
        start=start, end=end, timeframe=timeframe,
        symbol=symbol, exchange_id=exchange, use_cache=not no_cache,
    )
    if df.empty:
        console.print("[yellow]No candles returned for the given range. Aborting.[/yellow]")
        raise typer.Exit(code=1)
    console.print(f"  → {len(df)} candles fetched.")

    # --- Map to MIDI events ------------------------------------------
    if mode == "symphony":
        (events, tempo_changes, percussion_events, bass_events, voice_events,
         rendered_movements) = _map_symphony_pipeline(
            console, df, base_config,
            forced_movements=movements,
            user_specified_scale=user_specified_scale,
        )
        title = f"BTC Symphony {start} to {end}"
        include_percussion = True
    else:
        console.print(f"[cyan]Mapping candles to MIDI events ({scale}, root {root}, {octaves} octaves, {palette} palette)…[/cyan]")
        events = map_candles_to_events(df, base_config)
        bass_events = map_bass(df, base_config)
        voice_events = map_voice(df, base_config)
        tempo_changes = None
        percussion_events = []
        rendered_movements = None
        title = None
        include_percussion = False

    melody_count = sum(1 for e in events if e.channel == base_config.melody_channel)
    harmony_count = sum(1 for e in events if e.channel == base_config.harmony_channel)
    bass_count = len(bass_events)
    voice_count = len(voice_events)
    drum_count = len(percussion_events)
    all_events = events + bass_events + voice_events + percussion_events
    include_bass = bass_count > 0
    include_voice = voice_count > 0

    # --- Write the .mid file -----------------------------------------
    output_path = Path(output)
    console.print(f"[cyan]Writing MIDI → {output_path}…[/cyan]")
    write_midi(
        all_events, output_path, base_config,
        tempo_changes=tempo_changes,
        include_percussion=include_percussion,
        include_bass=include_bass,
        include_voice=include_voice,
        title=title,
    )

    # --- Optional HTML visualizer ------------------------------------
    html_path: Path | None = None
    if visualize:
        html_path = output_path.with_suffix(".html")
        # Default audio filename: <stem>.mp3 next to the html (the user
        # exports this from Logic / fluidsynth and drops it in this dir).
        audio_filename = audio_file or (output_path.stem + ".mp3")
        viz_title = title or f"BTC Sonify {start} to {end}"
        console.print(f"[cyan]Writing visualizer → {html_path}…[/cyan]")
        write_visualization(
            df=df,
            rendered_movements=rendered_movements,
            base_config=base_config,
            output_path=html_path,
            audio_path=audio_filename,
            title=viz_title,
            palette_name=palette,
        )

    # --- Optional WAV render -----------------------------------------
    wav_path: Path | None = None
    if render_wav:
        from btc_sonify.render import (
            FluidSynthMissingError,
            render_wav as do_render,
        )
        wav_path = output_path.with_suffix(".wav")
        console.print(f"[cyan]Rendering WAV → {wav_path}…[/cyan]")
        try:
            do_render(output_path, Path(soundfont), wav_path)
        except (FluidSynthMissingError, FileNotFoundError, RuntimeError) as exc:
            console.print(f"[yellow]WAV render skipped: {exc}[/yellow]")
            wav_path = None

    # --- Summary panel -----------------------------------------------
    last_tick = max(e.start_tick + e.duration_ticks for e in all_events)
    seconds = last_tick / base_config.ppq * (60 / base_config.bpm)
    pitches = [e.note for e in events]  # exclude drums from pitch range stat
    pitch_range = (min(pitches), max(pitches)) if pitches else (0, 0)

    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("mode",          mode)
    table.add_row("palette",       palette)
    table.add_row("candles",       f"{len(df)}")
    if rendered_movements is not None:
        table.add_row("movements",   f"{len(rendered_movements)}")
    table.add_row("melody notes",  f"{melody_count}")
    table.add_row("harmony notes", f"{harmony_count}")
    if bass_count:
        table.add_row("bass notes", f"{bass_count}")
    if voice_count:
        table.add_row("voice notes", f"{voice_count}")
    if drum_count:
        table.add_row("drum hits", f"{drum_count}")
    table.add_row("pitch range",   f"MIDI {pitch_range[0]}..{pitch_range[1]}")
    table.add_row("duration",      f"{seconds:.1f}s")
    table.add_row("output",        str(output_path))
    if wav_path is not None:
        table.add_row("wav",       str(wav_path))
    if html_path is not None:
        table.add_row("visualizer", str(html_path))

    console.print(Panel.fit(
        table, title="[green]btc-sonify[/green]", border_style="green",
    ))

    if rendered_movements is not None:
        mtable = Table(title="Movements", show_header=True, header_style="bold")
        mtable.add_column("#")
        mtable.add_column("label")
        mtable.add_column("candles", justify="right")
        mtable.add_column("return", justify="right")
        mtable.add_column("scale")
        mtable.add_column("root")
        mtable.add_column("BPM", justify="right")
        for r in rendered_movements:
            mtable.add_row(
                str(r.movement.index + 1),
                r.movement.label,
                str(r.movement.end_idx - r.movement.start_idx + 1),
                f"{r.movement.avg_return_pct:+.1f}%",
                r.config.scale,
                r.config.root,
                str(r.config.bpm),
            )
        console.print(mtable)


def _map_symphony_pipeline(
    console: Console,
    df,
    base_config: RunConfig,
    forced_movements: int | None,
    user_specified_scale: bool,
):
    """Run the symphony orchestration: detect → map melodies/harmonies →
    bass → percussion. Returns events, tempo changes, percussion events,
    bass events, and the list of RenderedMovements for the summary
    panel."""
    console.print("[cyan]Detecting movements via peak-trough segmentation…[/cyan]")
    movements = detect_movements(df, movements=forced_movements)
    console.print(f"  → {len(movements)} movements detected.")

    events, tempo_markers, rendered = map_symphony(
        df, base_config, movements, user_specified_scale=user_specified_scale,
    )
    tempo_changes = [
        TempoChange(tick=t.tick, bpm=t.bpm, label=t.label) for t in tempo_markers
    ]

    # Per-movement offsets carry the per-movement RunConfig so bass can
    # quantize against each movement's scale/root.
    offsets = [
        MovementOffset(
            start_idx=r.movement.start_idx,
            end_idx=r.movement.end_idx,
            tick_offset=r.tick_offset,
            config=r.config,
        )
        for r in rendered
    ]
    percussion_events = map_percussion(df, base_config, movement_offsets=offsets)
    bass_events = map_bass(df, base_config, movement_offsets=offsets)
    voice_events = map_voice(df, base_config, movement_offsets=offsets)

    return (
        events, tempo_changes, percussion_events, bass_events, voice_events, rendered,
    )


def _fetch_with_progress(
    *,
    console: Console,
    start: str,
    end: str,
    timeframe: str,
    symbol: str,
    exchange_id: str,
    use_cache: bool,
):
    """Wrap fetch_ohlcv with a rich Progress bar that ticks per page."""
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )
    task_id = None

    def on_page(page_size: int, total: int) -> None:
        nonlocal task_id
        if task_id is None:
            task_id = progress.add_task("Fetching pages", total=None)
        progress.update(task_id, advance=page_size, completed=total)

    with progress:
        return fetch_ohlcv(
            start=start,
            end=end,
            timeframe=timeframe,
            symbol=symbol,
            exchange_id=exchange_id,
            use_cache=use_cache,
            on_page=on_page,
        )


if __name__ == "__main__":
    app()
