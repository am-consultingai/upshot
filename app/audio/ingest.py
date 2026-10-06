"""Importing an existing recording (DESIGN.md §7, "Imported").

The file becomes ordinary chunk files on the ``them`` track, so an imported meeting runs
exactly the same pipeline as a recorded one — single-track, so speakers come from the LLM
rather than from the track split.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import time
import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.audio.writer import ChunkRecord, ChunkWriter
from app.config import Config
from app.log import get

log = get(__name__)

WAV_SUFFIXES = {".wav", ".wave"}


class UnsupportedAudio(ValueError):
    """The file is not a WAV and no ffmpeg is available to convert it."""


@dataclass(frozen=True)
class Imported:
    records: list[ChunkRecord]
    duration_s: int
    converted: bool


#: A conversion of even a day-long recording finishes in minutes; past this, ffmpeg hangs.
FFMPEG_TIMEOUT_S = 1800


def ffmpeg_path(config: Config | None = None) -> str | None:
    configured = config.get("ffmpeg_path") if config else None
    if configured and Path(str(configured)).exists():
        return str(configured)
    from app import paths

    bundled = paths.resource("ffmpeg.exe")
    if bundled.exists():
        return str(bundled)
    return shutil.which("ffmpeg")


#: How often a cancellable conversion asks whether to stop.
POLL_S = 1.0

#: The containers ffmpeg may open: ordinary audio and video files. Any container that
#: names further files to read (an HLS or DASH playlist, a concat list, an image pattern)
#: is left out, because it would reach a file anywhere, a network share included, which
#: the path rules refuse (D86; the PR #1 review). ``mov`` covers mp4, m4a and 3gp, and
#: ``matroska`` covers webm.
FORMATS: tuple[str, ...] = (
    "wav", "w64", "mp3", "aac", "flac", "ogg", "matroska", "mov", "avi", "asf", "amr",
    "aiff", "au", "caf", "mpeg", "mpegts", "flv", "wv", "ape", "tta", "ac3", "eac3",
    "dts", "truehd", "mlp", "gsm", "voc", "xwma", "rm",
)  # fmt: skip
#: Before every ``-i``: local files only (R1), in one of :data:`FORMATS`.
SAFE_INPUT_ARGS: tuple[str, ...] = (
    "-protocol_whitelist", "file", "-format_whitelist", ",".join(FORMATS),
)  # fmt: skip


def to_wav(
    source: Path,
    target: Path,
    *,
    config: Config | None = None,
    rate: int = 16000,
    input_args: Sequence[str] = (),
    extra_args: Sequence[str] = (),
    stop_check: Callable[[], None] | None = None,
) -> Path:
    """Convert anything ffmpeg understands into 16 kHz mono WAV.

    ``input_args`` go before ``-i`` (``-protocol_whitelist file``), ``extra_args`` after it
    (``-vn``, so a video track is never decoded). With ``stop_check``, ffmpeg runs in the
    background and ``stop_check`` is called about once a second; whatever it raises kills
    ffmpeg, removes the partial WAV and propagates (a cancelled file transcription, D86).
    """
    binary = ffmpeg_path(config)
    if binary is None:
        raise UnsupportedAudio(
            f"{source.name} is not a WAV and ffmpeg was not found — convert it first, "
            "or set ffmpeg_path"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        binary, "-y", *SAFE_INPUT_ARGS, *input_args, "-i", str(source), *extra_args,
        "-ac", "1", "-ar", str(rate), str(target),
    ]  # fmt: skip
    if stop_check is not None:
        return _to_wav_cancellable(command, source, target, stop_check)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            timeout=FFMPEG_TIMEOUT_S,
            # The frozen app has no console, so each child would flash one of its own.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise UnsupportedAudio(
            f"ffmpeg took more than {FFMPEG_TIMEOUT_S} s to convert {source.name}"
        ) from exc
    if result.returncode != 0 or not target.exists():
        raise _conversion_failed(source, result.stderr)
    return target


def _conversion_failed(source: Path, stderr: bytes) -> UnsupportedAudio:
    text = stderr.decode(errors="replace")
    if "does not contain any stream" in text or "matches no streams" in text:
        return UnsupportedAudio(f"{source.name} has no audio")
    return UnsupportedAudio(f"ffmpeg could not convert {source.name}: {text[-300:]}")


def _to_wav_cancellable(
    command: list[str], source: Path, target: Path, stop_check: Callable[[], None]
) -> Path:
    """``to_wav`` with ffmpeg in the background, polled so that it can be stopped."""
    # stderr to a file, not a pipe: ffmpeg writes progress there for as long as it runs,
    # and an unread pipe fills and stalls it.
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=errors,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        deadline = time.monotonic() + FFMPEG_TIMEOUT_S
        try:
            while True:
                try:
                    returncode = process.wait(timeout=POLL_S)
                    break
                except subprocess.TimeoutExpired:
                    stop_check()
                    if time.monotonic() > deadline:
                        raise UnsupportedAudio(
                            f"ffmpeg took more than {FFMPEG_TIMEOUT_S} s to convert {source.name}"
                        ) from None
        except BaseException:
            process.kill()
            process.wait()
            target.unlink(missing_ok=True)
            raise
        errors.seek(0)
        stderr = errors.read()
    if returncode != 0 or not target.exists():
        target.unlink(missing_ok=True)
        raise _conversion_failed(source, stderr)
    return target


def wav_duration_s(path: Path) -> float:
    """From the header alone: a long file's samples are never loaded to learn its length."""
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())


def read_mono(path: Path, rate: int = 16000) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        source_rate = handle.getframerate()
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        raw = handle.readframes(handle.getnframes())
    if width != 2:
        raise UnsupportedAudio(f"{path.name} is {width * 8}-bit; only 16-bit WAV is supported")
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
    if channels > 1:
        audio = audio[: len(audio) // channels * channels].reshape(-1, channels).mean(axis=1)
    if source_rate != rate:
        import soxr

        audio = np.asarray(soxr.resample(audio / 32768.0, source_rate, rate)) * 32768.0
    return np.clip(audio, -32768, 32767).astype(np.int16)


def ingest(
    source: Path,
    folder: Path,
    *,
    config: Config | None = None,
    track: str = "them",
) -> Imported:
    """Turn an uploaded file into chunk files plus a manifest under ``folder``."""
    rate = config.sample_rate if config else 16000
    converted = False
    wav = source
    if source.suffix.lower() not in WAV_SUFFIXES:
        wav = to_wav(
            source, folder / "import" / f"{source.stem}.converted.wav", config=config, rate=rate
        )
        converted = True
    try:
        samples = read_mono(wav, rate)
    except wave.Error as exc:
        if converted:
            raise UnsupportedAudio(f"{source.name}: {exc}") from exc
        wav = to_wav(
            source, folder / "import" / f"{source.stem}.converted.wav", config=config, rate=rate
        )
        converted = True
        samples = read_mono(wav, rate)

    writer = ChunkWriter(
        folder,
        tracks=(track,),
        rate=rate,
        chunk_s=float(config.chunk_s) if config else 60.0,
    )
    writer.write_pcm(track, samples)
    records = writer.close()
    duration_s = round(len(samples) / rate)
    log.info("imported %s → %d chunk(s), %d s", source.name, len(records), duration_s)
    return Imported(records=records, duration_s=duration_s, converted=converted)


#: ``Duration: 01:02:03.45`` in ffmpeg's description of its input.
_DURATION = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
#: How long describing a file may take; a local file is described in well under a second.
PROBE_TIMEOUT_S = 60


@dataclass(frozen=True)
class Probe:
    """What ffmpeg says a file holds, before anything is decoded (D86)."""

    duration_s: float | None
    has_audio: bool


def probe(source: Path, *, config: Config | None = None) -> Probe:
    """Describe ``source`` with ``ffmpeg -i`` and no output: the build ships no ffprobe.

    Local files in ordinary containers only (:data:`SAFE_INPUT_ARGS`), as every
    conversion reads them. Raises
    :class:`UnsupportedAudio` for anything ffmpeg cannot open; a file it opens with no
    audio stream comes back with ``has_audio=False``.
    """
    binary = ffmpeg_path(config)
    if binary is None:
        raise UnsupportedAudio("ffmpeg was not found, so this file cannot be read")
    try:
        result = subprocess.run(
            [binary, "-hide_banner", *SAFE_INPUT_ARGS, "-i", str(source)],
            capture_output=True,
            check=False,
            timeout=PROBE_TIMEOUT_S,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise UnsupportedAudio(f"ffmpeg took too long to read {source.name}") from exc
    text = result.stderr.decode(errors="replace")
    if "Input #0" not in text:
        raise UnsupportedAudio(f"{source.name} is not audio or video ffmpeg can read")
    match = _DURATION.search(text)
    duration = None
    if match:
        hours, minutes, seconds = match.groups()
        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    has_audio = re.search(r"Stream #\S+.*: Audio:", text) is not None
    return Probe(duration_s=duration, has_audio=has_audio)
