"""Importing an existing recording (DESIGN.md §7, "Imported").

The file becomes ordinary chunk files on the ``them`` track, so an imported meeting runs
exactly the same pipeline as a recorded one — single-track, so speakers come from the LLM
rather than from the track split.
"""

from __future__ import annotations

import shutil
import subprocess
import wave
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


def to_wav(source: Path, target: Path, *, config: Config | None = None, rate: int = 16000) -> Path:
    """Convert anything ffmpeg understands into 16 kHz mono WAV."""
    binary = ffmpeg_path(config)
    if binary is None:
        raise UnsupportedAudio(
            f"{source.name} is not a WAV and ffmpeg was not found — convert it first, "
            "or set ffmpeg_path"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            [binary, "-y", "-i", str(source), "-ac", "1", "-ar", str(rate), str(target)],
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
        raise UnsupportedAudio(
            f"ffmpeg could not convert {source.name}: "
            f"{result.stderr.decode(errors='replace')[-300:]}"
        )
    return target


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
