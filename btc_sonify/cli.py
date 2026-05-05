"""Command-line entry point for btc-sonify.

Wires the four pure layers — fetch → map → write → render — into a single
deterministic command. Reads inputs from Typer-parsed flags, builds a
RunConfig, shows a rich progress bar during the network fetch, and
prints a summary panel with note counts and the output path on success.
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

from btc_sonify.config import RunConfig
from btc_sonify.data import DEFAULT_EXCHANGE, DEFAULT_SYMBOL, fetch_ohlcv
from btc_sonify.mapping import map_candles_to_events
from btc_sonify.midi_writer import write_midi
from btc_sonify.scales import SCALES

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
            "dorian, phrygian, hijaz, blues."
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
    if render_wav and not soundfont:
        console.print("[red]--render-wav requires --soundfont (path to a .sf2 file).[/red]")
        raise typer.Exit(code=2)

    config = RunConfig(
        scale=scale, root=root, octaves=octaves, bpm=bpm, note_value=note_value,
    )

    # --- Fetch OHLCV with a progress bar ------------------------------
    console.print(f"[cyan]Fetching {symbol} {timeframe} from {exchange} ({start} → {end})…[/cyan]")
    df = _fetch_with_progress(
        console=console,
        start=start, end=end, timeframe=timeframe,
        symbol=symbol, exchange_id=exchange, use_cache=not no_cache,
    )
    if df.empty:
        console.print(f"[yellow]No candles returned for the given range. Aborting.[/yellow]")
        raise typer.Exit(code=1)
    console.print(f"  → {len(df)} candles fetched.")

    # --- Map to MIDI events ------------------------------------------
    console.print(f"[cyan]Mapping candles to MIDI events ({scale}, root {root}, {octaves} octaves)…[/cyan]")
    events = map_candles_to_events(df, config)
    melody_count = sum(1 for e in events if e.channel == config.melody_channel)
    harmony_count = sum(1 for e in events if e.channel == config.harmony_channel)

    # --- Write the .mid file -----------------------------------------
    output_path = Path(output)
    console.print(f"[cyan]Writing MIDI → {output_path}…[/cyan]")
    write_midi(events, output_path, config)

    # --- Optional WAV render (step 7) -------------------------------
    wav_path: Path | None = None
    if render_wav:
        try:
            from btc_sonify.render import render_wav as do_render
        except ImportError:
            console.print("[yellow]WAV rendering not available — see README for FluidSynth setup.[/yellow]")
        else:
            wav_path = output_path.with_suffix(".wav")
            console.print(f"[cyan]Rendering WAV → {wav_path}…[/cyan]")
            do_render(output_path, Path(soundfont), wav_path)

    # --- Summary panel -----------------------------------------------
    last_tick = max(e.start_tick + e.duration_ticks for e in events)
    seconds = last_tick / config.ppq * (60 / config.bpm)
    pitch_range = (
        min(e.note for e in events),
        max(e.note for e in events),
    )

    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("candles",       f"{len(df)}")
    table.add_row("melody notes",  f"{melody_count}")
    table.add_row("harmony notes", f"{harmony_count}")
    table.add_row("pitch range",   f"MIDI {pitch_range[0]}..{pitch_range[1]}")
    table.add_row("duration",      f"{seconds:.1f}s @ {bpm} BPM")
    table.add_row("output",        str(output_path))
    if wav_path is not None:
        table.add_row("wav",       str(wav_path))

    console.print(Panel.fit(
        table, title="[green]btc-sonify[/green]", border_style="green",
    ))


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
    """Wrap fetch_ohlcv with a rich Progress bar that ticks per page.

    The total candle count is unknown until the fetch completes, so the
    bar shows running totals (M of N format will display the latest
    total) and a spinner — accurate progress without a known target.
    """
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
