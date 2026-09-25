"""The ASR seam (TECHNICAL-DESIGN.md §7)."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Word:
    w: str
    s: float
    e: float
    p: float = 1.0

    def as_dict(self) -> dict[str, Any]:
        return {"w": self.w, "s": round(self.s, 3), "e": round(self.e, 3), "p": round(self.p, 3)}


@dataclass(frozen=True, slots=True)
class Segment:
    id: int
    track: str
    speaker: str
    start: float
    end: float
    text: str
    words: tuple[Word, ...] = ()
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0

    def shifted(self, offset_s: float, new_id: int | None = None) -> Segment:
        """The same segment on the meeting's timeline rather than the chunk's."""
        return Segment(
            id=self.id if new_id is None else new_id,
            track=self.track,
            speaker=self.speaker,
            start=self.start + offset_s,
            end=self.end + offset_s,
            text=self.text,
            words=tuple(Word(w.w, w.s + offset_s, w.e + offset_s, w.p) for w in self.words),
            avg_logprob=self.avg_logprob,
            no_speech_prob=self.no_speech_prob,
        )

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["start"] = round(self.start, 3)
        payload["end"] = round(self.end, 3)
        payload["words"] = [w.as_dict() for w in self.words]
        return payload


@runtime_checkable
class AsrBackend(Protocol):
    name: str

    def transcribe(
        self,
        wav: Path,
        *,
        language: str = "he",
        initial_prompt: str | None = None,
        word_timestamps: bool = True,
    ) -> list[Segment]: ...

    def unload(self) -> None: ...


@dataclass
class TranscriptFile:
    """``transcript.json`` — the merged, both-track segment list (§3.2)."""

    language: str
    model: dict[str, Any] = field(default_factory=dict)
    segments: list[Segment] = field(default_factory=list)
    version: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "language": self.language,
            "model": self.model,
            "segments": [segment.as_dict() for segment in self.segments],
        }

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    @classmethod
    def read(cls, path: Path) -> TranscriptFile:
        payload = json.loads(path.read_text(encoding="utf-8"))
        segments = [
            Segment(
                id=int(item["id"]),
                track=str(item["track"]),
                speaker=str(item["speaker"]),
                start=float(item["start"]),
                end=float(item["end"]),
                text=str(item["text"]),
                words=tuple(
                    Word(str(w["w"]), float(w["s"]), float(w["e"]), float(w.get("p", 1.0)))
                    for w in item.get("words", [])
                ),
                avg_logprob=float(item.get("avg_logprob", 0.0)),
                no_speech_prob=float(item.get("no_speech_prob", 0.0)),
            )
            for item in payload.get("segments", [])
        ]
        return cls(
            language=str(payload.get("language", "he")),
            model=dict(payload.get("model", {})),
            segments=segments,
            version=int(payload.get("version", 1)),
        )


def sort_segments(segments: Iterable[Segment]) -> list[Segment]:
    """Stable by start time, ties broken deterministically by track then id."""
    return sorted(segments, key=lambda s: (s.start, s.track, s.id))


def renumber(segments: Sequence[Segment]) -> list[Segment]:
    out = []
    for index, segment in enumerate(segments):
        out.append(
            Segment(
                id=index,
                track=segment.track,
                speaker=segment.speaker,
                start=segment.start,
                end=segment.end,
                text=segment.text,
                words=segment.words,
                avg_logprob=segment.avg_logprob,
                no_speech_prob=segment.no_speech_prob,
            )
        )
    return out


def max_consecutive_repeats(segments: Sequence[Segment]) -> int:
    """The Whisper repetition-loop metric: the longest run of identical texts."""
    longest = 0
    run = 0
    previous: str | None = None
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        if text == previous:
            run += 1
        else:
            run = 1
            previous = text
        longest = max(longest, run)
    return longest


def track_of(wav: Path) -> str:
    """Which track a file belongs to.

    Audio is one file per track (``audio/me.wav``), so the stem names the track. The
    parent-folder form is still accepted: fixtures and imported audio use it.
    """
    if wav.stem in ("me", "them"):
        return str(wav.stem)
    if wav.parent.name in ("me", "them"):
        return str(wav.parent.name)
    return "them"
