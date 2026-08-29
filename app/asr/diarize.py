"""Speaker diarization — splitting ``THEM`` into ``THEM_1/2/3`` (DESIGN.md §11).

Two-track capture already gives ME vs THEM as a hardware fact; this only subdivides the
loopback track. Off by default: it is a second native runtime and an extra download.

ONNX rather than pyannote/torch — the reasoning and the measured costs are in
DECISIONS.md D28. The seam is the ``Diarizer`` protocol, so a torch implementation can be
added later as one file and one config value.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import numpy as np

from app.asr.backend import Segment
from app.config import Config
from app.log import get

log = get(__name__)

THEM = "THEM"
LONG_TRACK_MINUTES = 120


@dataclass(frozen=True, slots=True)
class SpeakerTurn:
    start: float
    end: float
    speaker: int

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def overlap(self, start: float, end: float) -> float:
        return max(0.0, min(self.end, end) - max(self.start, start))


@runtime_checkable
class Diarizer(Protocol):
    name: str

    def diarize(self, samples: np.ndarray, rate: int) -> list[SpeakerTurn]: ...

    def unload(self) -> None: ...


# --------------------------------------------------------------------------- pure


def normalize_turns(turns: Sequence[SpeakerTurn]) -> list[SpeakerTurn]:
    """Relabel speakers 0,1,2… in order of first appearance.

    The clusterer's own ids are arbitrary and sparse — a two-speaker recording can come
    back as ``speaker_0`` and ``speaker_3``, which would render as "THEM_1" and "THEM_4".
    """
    ordered = sorted(turns, key=lambda turn: (turn.start, turn.end))
    mapping: dict[int, int] = {}
    out: list[SpeakerTurn] = []
    for turn in ordered:
        if turn.speaker not in mapping:
            mapping[turn.speaker] = len(mapping)
        out.append(SpeakerTurn(turn.start, turn.end, mapping[turn.speaker]))
    return out


def label_for(speaker: int, base: str = THEM) -> str:
    return f"{base}_{speaker + 1}"


def speaker_count(turns: Sequence[SpeakerTurn]) -> int:
    return len({turn.speaker for turn in turns})


def assign_speakers(
    segments: Sequence[Segment],
    turns: Sequence[SpeakerTurn],
    *,
    track: str = "them",
    base: str = THEM,
) -> list[Segment]:
    """Relabel one track's segments from the diarization turns, by majority overlap.

    A segment that overlaps nothing keeps the speaker it had: an unlabelled turn is
    better than a wrong one, and the two-track split already guarantees it is not ME.
    """
    normalized = normalize_turns(turns)
    if not normalized:
        return list(segments)
    out: list[Segment] = []
    for segment in segments:
        if segment.track != track:
            out.append(segment)
            continue
        totals: dict[int, float] = {}
        for turn in normalized:
            overlap = turn.overlap(segment.start, segment.end)
            if overlap > 0:
                totals[turn.speaker] = totals.get(turn.speaker, 0.0) + overlap
        if not totals:
            out.append(segment)
            continue
        # ties break on the lower speaker id, so the result is deterministic
        best = min(totals.items(), key=lambda item: (-item[1], item[0]))[0]
        out.append(
            Segment(
                id=segment.id,
                track=segment.track,
                speaker=label_for(best, base),
                start=segment.start,
                end=segment.end,
                text=segment.text,
                words=segment.words,
                avg_logprob=segment.avg_logprob,
                no_speech_prob=segment.no_speech_prob,
            )
        )
    return out


def concat_track(chunks: Sequence[tuple[float, np.ndarray]], rate: int) -> np.ndarray:
    """One continuous float32 track from (offset_seconds, int16 samples) chunks.

    Gaps become silence, so the diarizer's timestamps stay on the meeting's timeline.
    """
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    total = max(int(offset * rate) + len(samples) for offset, samples in chunks)
    track = np.zeros(total, dtype=np.float32)
    for offset, samples in chunks:
        start = int(offset * rate)
        track[start : start + len(samples)] = samples.astype(np.float32) / 32768.0
    return track


# --------------------------------------------------------------------------- backends


class FakeDiarizer:
    """Deterministic turns, selected with ``asr.diarization = "fake"``."""

    name = "fake"

    def __init__(self, speakers: int = 2, turn_s: float = 6.0) -> None:
        self.speakers = max(1, speakers)
        self.turn_s = turn_s
        self.calls = 0

    def diarize(self, samples: np.ndarray, rate: int) -> list[SpeakerTurn]:
        self.calls += 1
        duration = len(samples) / rate
        turns: list[SpeakerTurn] = []
        index = 0
        start = 0.0
        while start < duration:
            end = min(duration, start + self.turn_s)
            turns.append(SpeakerTurn(start, end, index % self.speakers))
            index += 1
            start = end
        return turns

    def unload(self) -> None:
        return None


class OnnxDiarizer:
    """sherpa-onnx: pyannote segmentation + a wespeaker embedding, both as ONNX.

    This build already ships onnxruntime for Silero VAD, so diarization adds ~19 MB of
    wheels and ~37 MB of weights rather than a second deep-learning framework.
    """

    name = "onnx"

    def __init__(
        self,
        segmentation_model: str,
        embedding_model: str,
        *,
        num_speakers: int = -1,
        threshold: float = 0.6,
        min_duration_on: float = 0.3,
        min_duration_off: float = 0.5,
        num_threads: int = 2,
    ) -> None:
        self.segmentation_model = segmentation_model
        self.embedding_model = embedding_model
        self.num_speakers = num_speakers
        self.threshold = threshold
        self.min_duration_on = min_duration_on
        self.min_duration_off = min_duration_off
        self.num_threads = num_threads
        self._pipeline: Any = None

    def load(self) -> Any:
        if self._pipeline is not None:
            return self._pipeline
        import sherpa_onnx

        config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                    model=self.segmentation_model
                ),
                num_threads=self.num_threads,
            ),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=self.embedding_model, num_threads=self.num_threads
            ),
            clustering=sherpa_onnx.FastClusteringConfig(
                num_clusters=self.num_speakers, threshold=self.threshold
            ),
            min_duration_on=self.min_duration_on,
            min_duration_off=self.min_duration_off,
        )
        if not config.validate():
            raise RuntimeError(
                "the diarization models are missing or unreadable: "
                f"{self.segmentation_model}, {self.embedding_model}"
            )
        self._pipeline = sherpa_onnx.OfflineSpeakerDiarization(config)
        return self._pipeline

    def diarize(self, samples: np.ndarray, rate: int) -> list[SpeakerTurn]:
        pipeline = self.load()
        expected = int(pipeline.sample_rate)
        audio = np.asarray(samples, dtype=np.float32)
        if rate != expected:
            import soxr

            audio = np.asarray(soxr.resample(audio, rate, expected), dtype=np.float32)
        result = pipeline.process(audio).sort_by_start_time()
        return normalize_turns(
            [SpeakerTurn(float(s.start), float(s.end), int(s.speaker)) for s in result]
        )

    def unload(self) -> None:
        self._pipeline = None


def make_diarizer(config: Config) -> Diarizer | None:
    """``None`` when diarization is off — the caller skips the whole step."""
    kind = str(config.get("asr.diarization", "off"))
    if kind == "off":
        return None
    if kind == "fake":
        return FakeDiarizer(speakers=int(config.get("asr.diarization_fake_speakers", 2)))
    if kind == "onnx":
        from app.asr.models import resolve_diarization

        models = resolve_diarization(config)
        return OnnxDiarizer(
            str(models.segmentation),
            str(models.embedding),
            num_speakers=int(config.get("asr.diarization_speakers", -1)),
            threshold=float(config.get("asr.diarization_threshold", 0.6)),
            min_duration_on=float(config.get("asr.diarization_min_duration_on", 0.3)),
            min_duration_off=float(config.get("asr.diarization_min_duration_off", 0.5)),
        )
    raise ValueError(f"unknown asr.diarization {kind!r}")
