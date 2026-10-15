"""PyAV is not shipped: faster-whisper gets arrays, and the build refuses GPL libraries.

PyAV's Windows wheel bundles libx264 and libx265 (GPL). Measured before the change
(licence epic, proofs P1-P3): on 16 kHz mono WAVs, ``read_audio`` gives the same samples
as ``faster_whisper.decode_audio``, and the transcripts were identical, hash for hash.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
import wave
from pathlib import Path

import numpy as np
import pytest

from app.asr.wav import read_audio

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_dist  # noqa: E402


def write_wav(path: Path, samples: np.ndarray, rate: int = 16000, channels: int = 1) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(samples.astype("<i2").tobytes())
    return path


# ------------------------------------------------------------------ the reader


def test_16_khz_mono_is_read_as_pyav_read_it(tmp_path: Path) -> None:
    """int16 / 32768, sample for sample: what decode_audio returned for these files."""
    pcm = np.array([0, 1, -1, 32767, -32768, 12345, -54], dtype=np.int16)
    audio = read_audio(write_wav(tmp_path / "a.wav", pcm))
    assert audio.dtype == np.float32
    assert np.array_equal(audio, pcm.astype(np.float32) / 32768.0)


def test_stereo_is_mixed_to_mono(tmp_path: Path) -> None:
    pcm = np.array([1000, 3000, -2000, 0], dtype=np.int16)  # two frames, L/R
    audio = read_audio(write_wav(tmp_path / "s.wav", pcm, channels=2))
    assert np.allclose(audio, [2000 / 32768, -1000 / 32768])


def test_another_rate_is_resampled_to_16_khz(tmp_path: Path) -> None:
    pcm = (np.sin(np.arange(48000) / 10) * 8000).astype(np.int16)  # 1 s at 48 kHz
    audio = read_audio(write_wav(tmp_path / "r.wav", pcm, rate=48000))
    assert audio.dtype == np.float32
    assert len(audio) == 16000


def test_an_empty_file_is_an_empty_array(tmp_path: Path) -> None:
    audio = read_audio(write_wav(tmp_path / "e.wav", np.zeros(0, dtype=np.int16), rate=44100))
    assert audio.dtype == np.float32
    assert audio.size == 0


def test_a_wav_that_is_not_16_bit_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "w.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(3)
        handle.setframerate(16000)
        handle.writeframes(b"\x00" * 30)
    with pytest.raises(ValueError, match="24-bit"):
        read_audio(path)


# ------------------------------------------------------------------ no PyAV


def test_pyav_is_kept_out_of_every_environment() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "av; sys_platform == 'never'" in project["tool"]["uv"]["override-dependencies"]


def test_faster_whisper_imports_without_pyav_and_pyav_cannot_be_used() -> None:
    """In a fresh interpreter: PyAV is not installed, the stand-in makes faster-whisper
    importable, and any use of PyAV raises instead of decoding."""
    code = (
        "import importlib.util, app, faster_whisper, av\n"
        "assert importlib.util.find_spec('av').origin == 'Upshot stand-in'\n"
        "assert not hasattr(av, '__path__') and not hasattr(av, 'open')\n"
        "import pickle; pickle.whichmodule(type('T', (), {'__module__': None}), 'x')\n"
        "try:\n"
        "    faster_whisper.decode_audio('missing.wav')\n"
        "except AttributeError as exc:\n"
        "    assert 'PyAV is not part of Upshot' in str(exc)\n"
        "else:\n"
        "    raise SystemExit('decode_audio ran')\n"
    )
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_nothing_calls_decode_audio() -> None:
    """``decode_audio`` is PyAV; the app and the benchmark scripts decode otherwise.
    (That the transcriber is handed arrays is tested in test_phase5_asr.)"""
    offenders = [
        str(path.relative_to(ROOT))
        for folder in ("app", "scripts")
        for path in (ROOT / folder).rglob("*.py")
        if "decode_audio(" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_the_classifier_reads_speech_without_pyav(tmp_path: Path) -> None:
    """The language classifier's reader, with the real Silero VAD: silence has no speech."""
    from app.asr.classify import speech_of

    speech = speech_of("them", write_wav(tmp_path / "t.wav", np.zeros(32000, dtype=np.int16)))
    assert speech.track == "them"
    assert speech.audio.dtype == np.float32 and speech.audio.size == 0


def test_the_spec_excludes_pyav() -> None:
    spec = (ROOT / "packaging" / "upshot.spec").read_text(encoding="utf-8")
    excludes = re.search(r"^\s*excludes=\[(.*)\],$", spec, re.M)
    assert excludes and '"av"' in excludes.group(1).split(", ")


# ------------------------------------------------------------------ the build guard


def test_a_clean_folder_passes(tmp_path: Path) -> None:
    (tmp_path / "_internal").mkdir()
    (tmp_path / "_internal" / "ffmpeg.exe").write_bytes(b"--enable-version3 --disable-libx264")
    assert check_dist.problems(tmp_path) == []
    assert check_dist.main([str(tmp_path)]) == 0


@pytest.mark.parametrize(
    "name",
    [
        "av.libs/avcodec-62.dll",
        "libx264-165-f3a9.dll",
        "libX265-efe4.dll",
        "x264.dll",
        "postproc-58.dll",
        "libpostproc-58.dll",
    ],
)
def test_a_gpl_library_fails_the_build(tmp_path: Path, name: str) -> None:
    path = tmp_path / "_internal" / name
    path.parent.mkdir(parents=True)
    path.write_bytes(b"MZ")
    assert check_dist.problems(tmp_path)
    assert check_dist.main([str(tmp_path)]) == 1


def test_an_ffmpeg_built_with_enable_gpl_fails_whatever_its_name(tmp_path: Path) -> None:
    (tmp_path / "codec.dll").write_bytes(b"MZ ... --enable-gpl --enable-version3 ...")
    assert check_dist.problems(tmp_path) == ["codec.dll: built with --enable-gpl"]


def test_pyavs_ffmpeg_fails_although_it_never_says_enable_gpl(tmp_path: Path) -> None:
    """The configure line of PyAV 18.1.0's avcodec on Windows: x264 and x265 under
    --enable-version3, with no --enable-gpl anywhere."""
    (tmp_path / "avcodec-62.dll").write_bytes(
        b"MZ --enable-mediafoundation --enable-version3 --disable-alsa --enable-zlib "
        b"--enable-libx264 --enable-libx265 --enable-nvenc ... LGPL version 3 or later"
    )
    assert check_dist.problems(tmp_path) == ["avcodec-62.dll: built with --enable-libx264"]


def test_our_lgpl_ffmpeg_passes(tmp_path: Path) -> None:
    """BtbN's LGPL build names the GPL libraries only to disable them."""
    (tmp_path / "ffmpeg.exe").write_bytes(
        b"--enable-version3 --disable-libx264 --disable-libx265 --disable-libxvid "
        b"--disable-libfdk-aac --enable-libopus"
    )
    assert check_dist.problems(tmp_path) == []


def test_a_flag_split_across_two_chunks_is_found(tmp_path: Path) -> None:
    path = tmp_path / "big.dll"
    path.write_bytes(b"x" * 100 + b"--enable-libx265 " + b"y" * 100)
    assert check_dist.gpl_flag(path, chunk=105) == "--enable-libx265"


def test_an_empty_binary_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "empty.dll").write_bytes(b"")
    assert check_dist.problems(tmp_path) == []
