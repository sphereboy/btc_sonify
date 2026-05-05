"""Tests for render.py.

We never actually invoke fluidsynth in tests — that would require both
the binary and a soundfont, neither of which is appropriate for CI. The
subprocess call is mocked; we verify the command shape and the error
paths.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from btc_sonify.render import (
    DEFAULT_SAMPLE_RATE,
    FluidSynthMissingError,
    render_wav,
)


def _touch(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")
    return p


def test_missing_midi_raises(tmp_path):
    sf = _touch(tmp_path / "sf.sf2")
    with pytest.raises(FileNotFoundError, match="MIDI file"):
        render_wav(tmp_path / "missing.mid", sf, tmp_path / "out.wav")


def test_missing_soundfont_raises(tmp_path):
    mid = _touch(tmp_path / "song.mid")
    with pytest.raises(FileNotFoundError, match="Soundfont"):
        render_wav(mid, tmp_path / "missing.sf2", tmp_path / "out.wav")


def test_missing_fluidsynth_binary_raises(tmp_path):
    mid = _touch(tmp_path / "song.mid")
    sf = _touch(tmp_path / "sf.sf2")
    with patch("btc_sonify.render.shutil.which", return_value=None):
        with pytest.raises(FluidSynthMissingError, match="brew install"):
            render_wav(mid, sf, tmp_path / "out.wav")


def test_calls_fluidsynth_with_expected_args(tmp_path):
    mid = _touch(tmp_path / "song.mid")
    sf = _touch(tmp_path / "sf.sf2")
    wav = tmp_path / "out.wav"

    class FakeProc:
        returncode = 0
        stderr = ""
        stdout = ""

    with patch("btc_sonify.render.shutil.which", return_value="/usr/local/bin/fluidsynth"), \
         patch("btc_sonify.render.subprocess.run", return_value=FakeProc()) as run:
        render_wav(mid, sf, wav)

    cmd = run.call_args.args[0]
    assert cmd[0] == "fluidsynth"
    assert "-ni" in cmd
    assert "-F" in cmd
    f_idx = cmd.index("-F")
    assert cmd[f_idx + 1] == str(wav)
    assert "-r" in cmd
    r_idx = cmd.index("-r")
    assert cmd[r_idx + 1] == str(DEFAULT_SAMPLE_RATE)
    # Soundfont before MIDI per fluidsynth's CLI grammar
    assert cmd[-2] == str(sf)
    assert cmd[-1] == str(mid)


def test_nonzero_exit_raises_with_stderr(tmp_path):
    mid = _touch(tmp_path / "song.mid")
    sf = _touch(tmp_path / "sf.sf2")

    class FailedProc:
        returncode = 1
        stderr = "oh no, soundfont parse error"
        stdout = ""

    with patch("btc_sonify.render.shutil.which", return_value="/usr/local/bin/fluidsynth"), \
         patch("btc_sonify.render.subprocess.run", return_value=FailedProc()):
        with pytest.raises(RuntimeError, match="exited 1"):
            render_wav(mid, sf, tmp_path / "out.wav")


def test_creates_wav_parent_directory(tmp_path):
    mid = _touch(tmp_path / "song.mid")
    sf = _touch(tmp_path / "sf.sf2")
    nested = tmp_path / "renders" / "deep" / "out.wav"

    class FakeProc:
        returncode = 0
        stderr = ""
        stdout = ""

    with patch("btc_sonify.render.shutil.which", return_value="/usr/local/bin/fluidsynth"), \
         patch("btc_sonify.render.subprocess.run", return_value=FakeProc()):
        render_wav(mid, sf, nested)
    assert nested.parent.is_dir()


def test_custom_sample_rate(tmp_path):
    mid = _touch(tmp_path / "song.mid")
    sf = _touch(tmp_path / "sf.sf2")

    class FakeProc:
        returncode = 0
        stderr = ""
        stdout = ""

    with patch("btc_sonify.render.shutil.which", return_value="/usr/local/bin/fluidsynth"), \
         patch("btc_sonify.render.subprocess.run", return_value=FakeProc()) as run:
        render_wav(mid, sf, tmp_path / "out.wav", sample_rate=48000)
    cmd = run.call_args.args[0]
    r_idx = cmd.index("-r")
    assert cmd[r_idx + 1] == "48000"
