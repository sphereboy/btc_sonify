"""Optional WAV render via the FluidSynth CLI.

We shell out to the ``fluidsynth`` binary rather than depend on a Python
binding (pyfluidsynth) because:

1. The system library is required either way — there's no pure-Python path.
2. The CLI is stable and trivially installed via Homebrew on macOS.
3. We avoid pyfluidsynth's ABI/wheel headaches, which are notorious.

If the binary or the soundfont is missing we raise informative errors
that tell the user exactly how to fix it. The CLI surface (``--render-wav``)
calls ``render_wav`` after the .mid is written; failures here don't
nullify the MIDI write — they just skip the WAV step.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

DEFAULT_SAMPLE_RATE = 44100


class FluidSynthMissingError(RuntimeError):
    """Raised when the `fluidsynth` binary isn't on PATH."""


def render_wav(
    midi_path: Path,
    soundfont_path: Path,
    wav_path: Path,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
) -> None:
    """Render `midi_path` to `wav_path` using `soundfont_path`.

    Requires the ``fluidsynth`` binary on PATH. On macOS:
    ``brew install fluid-synth``. Other platforms have similar packages
    (apt: fluidsynth, choco: fluidsynth).

    Raises FluidSynthMissingError if the binary isn't found, FileNotFoundError
    if either input file is missing, and RuntimeError if fluidsynth
    returns a non-zero exit code.
    """
    midi_path = Path(midi_path)
    soundfont_path = Path(soundfont_path)
    wav_path = Path(wav_path)

    if not midi_path.exists():
        raise FileNotFoundError(f"MIDI file not found: {midi_path}")
    if not soundfont_path.exists():
        raise FileNotFoundError(
            f"Soundfont not found: {soundfont_path}. "
            f"Free GeneralUser GS soundfonts can be downloaded from "
            f"https://schristiancollins.com/generaluser.php"
        )
    if shutil.which("fluidsynth") is None:
        raise FluidSynthMissingError(
            "The `fluidsynth` binary is not on PATH. "
            "Install with: brew install fluid-synth (macOS), "
            "apt install fluidsynth (Debian/Ubuntu), "
            "or see https://www.fluidsynth.org/"
        )

    wav_path.parent.mkdir(parents=True, exist_ok=True)

    # -ni = no shell, no interactive; -F = output file; -r = sample rate.
    cmd = [
        "fluidsynth",
        "-ni",
        "-F", str(wav_path),
        "-r", str(sample_rate),
        str(soundfont_path),
        str(midi_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"fluidsynth exited {proc.returncode}.\n"
            f"stderr: {proc.stderr.strip()}"
        )
