"""Speaker diarization — splitting each track into its voices (DESIGN.md §11, D85).

Two-track capture already gives ME vs THEM as a hardware fact; this subdivides each
track. One voice on a track keeps the track's own label (``ME``, ``THEM``); two or more
are numbered (``ME_1``, ``ME_2``; ``THEM_1``, ``THEM_2``). Always on: the models come
with the installer, and a machine without them transcribes without speaker turns.

ONNX rather than pyannote/torch — the reasoning and the measured costs are in
DECISIONS.md D28. The seam is the ``Diarizer`` protocol, so a torch implementation can be
added later as one file and one config value.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import numpy as np

from app.asr.backend import Segment
from app.config import Config
from app.log import get

log = get(__name__)

ME = "ME"
THEM = "THEM"


def track_of(slot: str) -> str:
    """Which recorded track a speaker slot came from: ``ME``/``ME_n`` is the microphone."""
    return "me" if slot == ME or slot.startswith(f"{ME}_") else "them"


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


def merge_minor_speakers(
    turns: Sequence[SpeakerTurn], *, min_share: float = 0.05, min_seconds: float = 20.0
) -> list[SpeakerTurn]:
    """Fold voices that barely spoke into the one that spoke most.

    For the microphone track: what is left of the far side after echo subtraction, a
    cough, a door — the clusterer calls each of these a voice, and a one-person meeting
    would show a second person in the room. A voice under ``min_seconds`` or under
    ``min_share`` of the track's talk is not one. The voice that spoke most always stays.
    """
    totals: dict[int, float] = {}
    for turn in turns:
        totals[turn.speaker] = totals.get(turn.speaker, 0.0) + turn.duration
    if len(totals) <= 1:
        return normalize_turns(turns)
    dominant = min(totals.items(), key=lambda item: (-item[1], item[0]))[0]
    floor = max(min_seconds, min_share * sum(totals.values()))
    minor = {speaker for speaker, total in totals.items() if total < floor and speaker != dominant}
    return normalize_turns(
        [
            SpeakerTurn(turn.start, turn.end, dominant if turn.speaker in minor else turn.speaker)
            for turn in turns
        ]
    )


#: Turns shorter than this are not embedded on their own: "okay", "right", a laugh.
CENTROID_MIN_TURN_S = 1.0
#: Less than this embeds as NaN or as an empty vector.
CENTROID_MIN_SAMPLES_S = 0.1


def speaker_centroids(
    turns: Sequence[SpeakerTurn],
    samples: np.ndarray,
    rate: int,
    embed: Callable[[np.ndarray], np.ndarray],
) -> dict[int, np.ndarray]:
    """Each voice's mean embedding, unit length: its turns of a second or more, by length.

    A voice made only of shorter turns is embedded once, as their concatenation, because
    the embedding of half a second of speech says little about who spoke it.
    """
    by_speaker: dict[int, list[SpeakerTurn]] = {}
    for turn in turns:
        by_speaker.setdefault(turn.speaker, []).append(turn)

    def piece(turn: SpeakerTurn) -> np.ndarray:
        return samples[int(turn.start * rate) : int(turn.end * rate)]

    centroids: dict[int, np.ndarray] = {}
    for speaker, own in by_speaker.items():
        long = [turn for turn in own if turn.duration >= CENTROID_MIN_TURN_S]
        if long:
            vectors = [_unit(embed(piece(turn))) for turn in long]
            weights = [turn.duration for turn in long]
        else:
            joined = np.concatenate([piece(turn) for turn in own])
            # A few milliseconds embed as NaN or as nothing (sherpa-onnx's stream is not ready)
            vectors = [_unit(embed(joined))] if len(joined) >= CENTROID_MIN_SAMPLES_S * rate else []
            weights = [1.0] * len(vectors)
        kept = [
            (v, w) for v, w in zip(vectors, weights, strict=True) if v.size and np.isfinite(v).all()
        ]
        if not kept or len({v.size for v, _ in kept}) > 1:
            continue  # no centroid: the voice is left alone
        mean = np.average([v for v, _ in kept], axis=0, weights=[w for _, w in kept])
        centroids[speaker] = _unit(mean)
    return centroids


def merge_similar_speakers(
    turns: Sequence[SpeakerTurn], centroids: dict[int, np.ndarray], *, bound: float = 0.6
) -> list[SpeakerTurn]:
    """Join voices whose centroids are at least ``bound`` alike (cosine), closest first.

    sherpa-onnx clusters short segments, whose embeddings are noisy, so on a long track one
    person comes back as many clusters whose centroids nearly agree: a 27-minute 1:1 call
    gave 16 far-side voices (ClickUp z8tj1hfdwp). A joined voice's centroid is the
    talk-weighted mean of its parts, and it keeps the id of the part that spoke more.
    Measured bound in DECISIONS.md D91.
    """
    talk: dict[int, float] = {}
    for turn in turns:
        talk[turn.speaker] = talk.get(turn.speaker, 0.0) + turn.duration
    live = {
        speaker: _unit(centroids[speaker])
        for speaker in talk
        if speaker in centroids and np.isfinite(centroids[speaker]).all()
    }
    if len({vector.size for vector in live.values()}) > 1:
        return normalize_turns(turns)
    owner = {speaker: speaker for speaker in talk}
    while len(live) > 1:
        ids = sorted(live)
        similarity, first, second = max(
            (float(live[a] @ live[b]), a, b) for i, a in enumerate(ids) for b in ids[i + 1 :]
        )
        if similarity < bound:
            break
        keep, gone = (first, second) if talk[first] >= talk[second] else (second, first)
        live[keep] = _unit(live[keep] * talk[keep] + live.pop(gone) * talk[gone])
        talk[keep] += talk.pop(gone)
        for speaker, current in owner.items():
            if current == gone:
                owner[speaker] = keep
    return normalize_turns(
        [SpeakerTurn(turn.start, turn.end, owner[turn.speaker]) for turn in turns]
    )


def _unit(vector: np.ndarray) -> np.ndarray:
    vector = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0 else vector


def label_track(
    segments: Sequence[Segment], turns: Sequence[SpeakerTurn], *, track: str, base: str
) -> list[Segment]:
    """One voice keeps the track's own label; two or more are numbered from 1."""
    if speaker_count(turns) <= 1:
        return list(segments)
    return assign_speakers(segments, turns, track=track, base=base)


def diarize_track(
    diarizer: Diarizer,
    audio: np.ndarray,
    rate: int,
    segments: Sequence[Segment],
    *,
    track: str,
    base: str,
    config: Config,
) -> tuple[list[Segment], dict[str, int] | None]:
    """Split one whole track into its voices and label its segments (D29, D85).

    Shared by the meeting's transcribe stage and the file engine (D86), so both diarize
    alike. Returns the relabelled segments and ``{"speakers", "turns"}``, or the segments
    unchanged and ``None`` when the diarizer failed: a transcript is never lost for want
    of speaker turns. The microphone's quiet clusters are folded into its main voice
    first, and the far side's crumbs into its.
    """
    minutes = len(audio) / rate / 60
    if minutes > LONG_TRACK_MINUTES:
        log.warning("diarizing %.0f minutes in one pass; this is memory-hungry", minutes)
    try:
        turns = diarizer.diarize(audio, rate)
    except Exception as exc:
        log.warning("could not diarize the %s track; leaving it whole: %s", track, exc)
        return list(segments), None
    if track == "me":
        turns = merge_minor_speakers(
            turns,
            min_share=float(config.get("asr.diarization_mic_min_share", 0.05)),
            min_seconds=float(config.get("asr.diarization_mic_min_seconds", 20.0)),
        )
    else:
        # The far side keeps a brief voice, but not a crumb under 2 % of the talk (D91).
        turns = merge_minor_speakers(
            turns,
            min_share=float(config.get("asr.diarization_far_min_share", 0.02)),
            min_seconds=0.0,
        )
    voices = len({turn.speaker for turn in turns})
    log.info("diarization: %d voice(s) on the %s track", voices, track)
    found = {"speakers": voices, "turns": len(turns)}
    return label_track(segments, turns, track=track, base=base), found


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
        #: As OnnxDiarizer's: called with the fraction of the track done.
        self.on_progress: Callable[[float], None] | None = None

    def diarize(self, samples: np.ndarray, rate: int) -> list[SpeakerTurn]:
        self.calls += 1
        duration = len(samples) / rate
        turns: list[SpeakerTurn] = []
        index = 0
        start = 0.0
        while start < duration:
            end = min(duration, start + self.turn_s)
            turns.append(SpeakerTurn(start, end, index % self.speakers))
            if self.on_progress is not None:
                self.on_progress(end / duration)
            index += 1
            start = end
        return turns

    def unload(self) -> None:
        return None


class OnnxDiarizer:
    """sherpa-onnx: pyannote segmentation + a 3D-Speaker CAM++ embedding, both as ONNX.

    This build already ships onnxruntime for Silero VAD, so diarization adds ~19 MB of
    wheels and ~34 MB of weights rather than a second deep-learning framework. The
    clusterer's voices are then joined by their centroids (``merge_similar_speakers``,
    D91), embedded with the same model.
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
        merge_similarity: float = 0.6,
    ) -> None:
        self.segmentation_model = segmentation_model
        self.embedding_model = embedding_model
        self.num_speakers = num_speakers
        self.threshold = threshold
        self.min_duration_on = min_duration_on
        self.min_duration_off = min_duration_off
        self.num_threads = num_threads
        self.merge_similarity = merge_similarity
        self._pipeline: Any = None
        self._extractor: Any = None
        #: Called with the fraction of the track done, from sherpa-onnx's own count of
        #: the chunks it has segmented and embedded: the meeting's and a file's progress.
        self.on_progress: Callable[[float], None] | None = None

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
        if np.issubdtype(np.asarray(samples).dtype, np.integer):
            # The callers read int16 WAVs (read_wav); the models want -1..1. Unscaled, the
            # embeddings of every voice came out alike: a two-voice dialogue was one voice.
            audio = audio / 32768.0
        if rate != expected:
            import soxr

            audio = np.asarray(soxr.resample(audio, rate, expected), dtype=np.float32)
        hook = self.on_progress
        if hook is None:
            result = pipeline.process(audio).sort_by_start_time()
        else:

            def counted(done: int, total: int) -> int:
                # Called from sherpa-onnx's C++: nothing may raise through it, and a
                # non-zero answer would abort the diarization.
                if total > 0:
                    with contextlib.suppress(Exception):
                        hook(done / total)
                return 0

            result = pipeline.process(audio, callback=counted).sort_by_start_time()
        turns = normalize_turns(
            [SpeakerTurn(float(s.start), float(s.end), int(s.speaker)) for s in result]
        )
        if speaker_count(turns) <= 1 or self.num_speakers > 0:
            return turns  # a count the user set is kept as the clusterer met it
        try:
            centroids = speaker_centroids(
                turns, audio, expected, lambda piece: self._embed(piece, expected)
            )
            return merge_similar_speakers(turns, centroids, bound=self.merge_similarity)
        except Exception:
            log.exception("joining similar voices failed; keeping the clusterer's voices")
            return turns

    def _embed(self, samples: np.ndarray, rate: int) -> np.ndarray:
        if self._extractor is None:
            import sherpa_onnx

            self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(
                sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                    model=self.embedding_model, num_threads=self.num_threads
                )
            )
        stream = self._extractor.create_stream()
        stream.accept_waveform(rate, samples)
        stream.input_finished()
        return np.asarray(self._extractor.compute(stream), dtype=np.float32)

    def unload(self) -> None:
        self._pipeline = None
        self._extractor = None


def make_diarizer(config: Config) -> Diarizer | None:
    """``None`` when diarization is off or its models are not on this machine — the caller
    skips the whole step. The installer fetches them (app/prepare.py); transcription never
    downloads one (R12), and a meeting is never lost for want of speaker turns."""
    kind = str(config.get("asr.diarization", "off"))
    if kind == "off":
        return None
    if kind == "fake":
        return FakeDiarizer(speakers=int(config.get("asr.diarization_fake_speakers", 2)))
    if kind == "onnx":
        from app.asr.models import resolve_diarization

        models = resolve_diarization(config)
        if not models.present:
            log.warning(
                "diarization models are missing (%s, %s); transcribing without speaker turns",
                models.segmentation,
                models.embedding,
            )
            return None
        return OnnxDiarizer(
            str(models.segmentation),
            str(models.embedding),
            num_speakers=int(config.get("asr.diarization_speakers", -1)),
            threshold=float(config.get("asr.diarization_threshold", 0.6)),
            min_duration_on=float(config.get("asr.diarization_min_duration_on", 0.3)),
            min_duration_off=float(config.get("asr.diarization_min_duration_off", 0.5)),
            merge_similarity=float(config.get("asr.diarization_merge_similarity", 0.6)),
        )
    raise ValueError(f"unknown asr.diarization {kind!r}")
