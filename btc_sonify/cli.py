"""Command-line entry point for btc-sonify.

For build step 1 the option surface is fully defined but the body is a
placeholder — running the command prints the parsed arguments so the CLI
shape can be sanity-checked. Real wiring lands in step 6.
"""
from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel

app = typer.Typer(
    add_completion=False,
    help="Convert Bitcoin OHLCV candlestick data into MIDI music.",
)


@app.command()
def sonify(
    start: str = typer.Option(..., "--start", help="Start date (YYYY-MM-DD)."),
    end: str = typer.Option(..., "--end", help="End date (YYYY-MM-DD)."),
    timeframe: str = typer.Option(
        "1d", "--timeframe", help="Candle timeframe: 1h, 4h, 1d, 1w."
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
) -> None:
    """Render BTC OHLCV candles in the given range as a MIDI file."""
    console = Console()
    args = {
        "start": start,
        "end": end,
        "timeframe": timeframe,
        "scale": scale,
        "root": root,
        "octaves": octaves,
        "bpm": bpm,
        "note_value": note_value,
        "output": output,
        "render_wav": render_wav,
        "soundfont": soundfont,
    }
    body = "\n".join(f"  {k:<12} = {v!r}" for k, v in args.items())
    console.print(
        Panel.fit(
            f"[yellow]Not yet implemented (build step 1 of 8)[/yellow]\n\n"
            f"Parsed arguments:\n{body}",
            title="btc-sonify",
            border_style="yellow",
        )
    )
    raise typer.Exit(code=0)
