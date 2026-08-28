"""Two-stage voice activity detection (TECHNICAL-DESIGN.md §4.7).

A cheap energy gate runs always; Silero only verifies what the gate lets through. The
gate is deliberately the *looser* stage — it may never reject a frame Silero accepts.
"""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Protocol

import numpy as np

FRAME_MS = 30
INT16_MAX = 32768.0


def frame_count(samples: int, rate: int, frame_ms: int = FRAME_MS) -> int:
    per_frame = rate * frame_ms // 1000
    return max(0, samples // per_frame)


def to_float(pcm: np.ndarray) -> np.ndarray:
    if pcm.dtype == np.float32:
        return pcm
    return (pcm.astype(np.float32) / INT16_MAX).astype(np.float32)


class Vad(Protocol):
    def voiced_frames(self, pcm: np.ndarray, rate: int = 16000) -> list[bool]: ...


class Verifier(Protocol):
    """The second stage. ``available()`` says whether it can run at all."""

    def available(self) -> bool: ...

    def voiced_frames(self, pcm: np.ndarray, rate: int = 16000) -> list[bool]: ...


class EnergyGate:
    """RMS gate. ~0 CPU, runs on every frame of both tracks."""

    def __init__(self, threshold: float = 0.004, frame_ms: int = FRAME_MS) -> None:
        self.threshold = threshold
        self.frame_ms = frame_ms

    def voiced_frames(self, pcm: np.ndarray, rate: int = 16000) -> list[bool]:
        audio = to_float(np.asarray(pcm))
        per_frame = rate * self.frame_ms // 1000
        count = len(audio) // per_frame
        if count == 0:
            return []
        frames = audio[: count * per_frame].reshape(count, per_frame)
        rms = np.sqrt(np.mean(np.square(frames, dtype=np.float64), axis=1))
        return [bool(value > self.threshold) for value in rms]


class SileroVad:
    """The verification stage. The model ships inside faster-whisper — no download."""

    def __init__(self, threshold: float = 0.5, frame_ms: int = FRAME_MS) -> None:
        self.threshold = threshold
        self.frame_ms = frame_ms
        self._available: bool | None = None

    def available(self) -> bool:
        if self._available is None:
            try:
                from faster_whisper.vad import get_vad_model

                get_vad_model()
                self._available = True
            except Exception:
                self._available = False
        return self._available

    def speech_spans(self, pcm: np.ndarray, rate: int = 16000) -> list[tuple[int, int]]:
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        audio = to_float(np.asarray(pcm))
        options = VadOptions(
            threshold=self.threshold,
            min_speech_duration_ms=0,
            min_silence_duration_ms=200,
            speech_pad_ms=0,
        )
        spans = get_speech_timestamps(audio, options, sampling_rate=rate)
        return [(int(span["start"]), int(span["end"])) for span in spans]

    def voiced_frames(self, pcm: np.ndarray, rate: int = 16000) -> list[bool]:
        per_frame = rate * self.frame_ms // 1000
        count = len(np.asarray(pcm)) // per_frame
        flags = [False] * count
        if count == 0 or not self.available():
            return flags
        for start, end in self.speech_spans(pcm, rate):
            first = start // per_frame
            last = min(count, (end + per_frame - 1) // per_frame)
            for index in range(max(0, first), last):
                flags[index] = True
        return flags


class TwoStageVad:
    """Energy gate, then Silero on what survives it."""

    def __init__(
        self,
        gate: EnergyGate | None = None,
        silero: Verifier | None = None,
        frame_ms: int = FRAME_MS,
    ) -> None:
        self.gate = gate or EnergyGate(frame_ms=frame_ms)
        self.silero = silero or SileroVad(frame_ms=frame_ms)
        self.frame_ms = frame_ms
        self.silero_calls = 0

    def voiced_frames(self, pcm: np.ndarray, rate: int = 16000) -> list[bool]:
        gated = self.gate.voiced_frames(pcm, rate)
        if not any(gated):
            return gated
        if not self.silero.available():
            # No onnxruntime, or no model: degrade to the gate rather than to silence.
            return gated
        self.silero_calls += 1
        verified = self.silero.voiced_frames(pcm, rate)
        if not verified:
            return gated
        return [bool(g and v) for g, v in zip(gated, verified, strict=False)]

    def voiced_ratio(self, pcm: np.ndarray, rate: int = 16000) -> float:
        flags = self.voiced_frames(pcm, rate)
        return (sum(flags) / len(flags)) if flags else 0.0

    def has_speech(self, pcm: np.ndarray, rate: int = 16000, min_s: float = 3.0) -> bool:
        flags = self.voiced_frames(pcm, rate)
        return (sum(flags) * self.frame_ms / 1000.0) >= min_s


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    return np.frombuffer(raw, dtype=np.int16), rate


def has_speech_in_file(path: Path, min_s: float = 3.0, vad: TwoStageVad | None = None) -> bool:
    pcm, rate = read_wav(path)
    return (vad or TwoStageVad()).has_speech(pcm, rate, min_s=min_s)


class GateOnlyVerifier:
    """Verification disabled — selected with ``audio.vad = "energy"``.

    Useful on a machine with no onnxruntime, and in tests whose fixtures are synthetic
    sound rather than real speech.
    """

    def available(self) -> bool:
        return False

    def voiced_frames(self, pcm: np.ndarray, rate: int = 16000) -> list[bool]:
        return []


def make_vad(config: object) -> TwoStageVad:
    """Which VAD is wired — chosen by config, like every other fake in this build."""
    kind = "two_stage"
    getter = getattr(config, "get", None)
    if callable(getter):
        kind = str(getter("audio.vad", "two_stage"))
    if kind == "energy":
        return TwoStageVad(silero=GateOnlyVerifier())
    return TwoStageVad()
