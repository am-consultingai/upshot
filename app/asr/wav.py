"""Audio for faster-whisper, read here rather than by PyAV.

faster-whisper decodes a file it is given by path with PyAV, and PyAV's Windows wheel
bundles the GPL encoders libx264 and libx265, which Upshot may not ship (``pyproject.toml``
keeps PyAV out; ``app/__init__.py`` stands in for it). So the transcriber and the language
classifier get arrays instead of paths.

Every WAV they read is 16-bit PCM, mono, at ``audio.sample_rate``: a track the recorder
wrote (``app/audio/writer.py``), its echo-cleaned copy, or a file converted by ffmpeg
(``app/audio/ingest.py``, which also transcription jobs use). At 16 kHz this gives exactly
the samples PyAV gave (int16 / 32768); any other rate is resampled with soxr.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

#: What Whisper and Silero VAD expect.
SAMPLE_RATE = 16_000


def read_audio(path: Path, rate: int = SAMPLE_RATE) -> np.ndarray:
    """``path`` as float32 mono samples at ``rate``, scaled as int16 / 32768."""
    with wave.open(str(path), "rb") as handle:
        width = handle.getsampwidth()
        channels = handle.getnchannels()
        source_rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    if width != 2:
        raise ValueError(f"{path.name} is {width * 8}-bit; Upshot writes 16-bit WAV")
    audio = np.frombuffer(raw, dtype="<i2").astype(np.float32)
    audio /= 32768.0  # in place: a four-hour track is close to a gigabyte as float32
    if channels > 1:
        frames = audio[: len(audio) // channels * channels].reshape(-1, channels)
        audio = frames.mean(axis=1, dtype=np.float32)
    if source_rate != rate and audio.size:
        import soxr

        audio = np.asarray(soxr.resample(audio, source_rate, rate), dtype=np.float32)
    return audio
