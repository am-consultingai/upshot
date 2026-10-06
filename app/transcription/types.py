"""What a file transcription takes and gives back (D86).

Defined on their own, before the engine, so the renderers (``render.py``) and the store
can be written against them. :meth:`FileTranscript.as_result` is the ``result.json``
contract that ``GET /api/v1/transcriptions/{id}/result?format=json`` serves: version 1, and
stable — anything added later is a new key, never a changed one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.asr.backend import Segment, Word

RESULT_VERSION = 1

#: The engine's steps, in order. A starting recording stops a file job only between two
#: of them, never inside one (plan §4.2).
PHASES: tuple[str, ...] = ("decode", "check", "language", "transcribe", "diarize")

#: How much of a job each phase is, for one progress figure. Transcription dominates;
#: diarization runs at 10–12× real time (D85), decoding at hundreds.
PHASE_WEIGHTS: dict[str, float] = {
    "decode": 0.05,
    "check": 0.0,
    "language": 0.05,
    "transcribe": 0.75,
    "diarize": 0.15,
}

#: The words a subtitle cue may hold, by default and at most (plan §4.3).
DEFAULT_WORDS_PER_CUE = 7
MAX_WORDS_PER_CUE = 20
#: Characters of a prompt (Whisper's ``initial_prompt``).
MAX_PROMPT_CHARS = 1000


def overall_progress(phase: str, fraction: float) -> float:
    """``fraction`` of ``phase`` as a fraction of the whole job, 0–1."""
    done = 0.0
    for name in PHASES:
        if name == phase:
            return round(min(1.0, done + PHASE_WEIGHTS[name] * max(0.0, min(1.0, fraction))), 4)
        done += PHASE_WEIGHTS[name]
    raise ValueError(f"unknown phase {phase!r}")


@dataclass(frozen=True, slots=True)
class Options:
    """What the caller asked for. ``language`` is ``auto`` or a Whisper code."""

    language: str = "auto"
    diarize: bool = True
    prompt: str = ""
    max_words_per_cue: int = DEFAULT_WORDS_PER_CUE

    def as_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "diarize": self.diarize,
            "prompt": self.prompt,
            "max_words_per_cue": self.max_words_per_cue,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Options:
        return cls(
            language=str(payload.get("language", "auto")),
            diarize=bool(payload.get("diarize", True)),
            prompt=str(payload.get("prompt", "")),
            max_words_per_cue=int(payload.get("max_words_per_cue", DEFAULT_WORDS_PER_CUE)),
        )


@dataclass(frozen=True)
class FileTranscript:
    """One file, transcribed. Speakers are ``S1``, ``S2``… in order of first appearance;
    a file has no "me". No speech at all is an empty transcript, not an error."""

    duration_s: float
    segments: tuple[Segment, ...]
    #: None when nothing was said: there is no language to name.
    language: str | None
    language_conf: float | None
    #: ``classifier``, ``override`` (the caller named the language), or ``none``.
    language_source: str
    model: dict[str, Any] = field(default_factory=dict)
    diarized: bool = False
    #: Why something the caller asked for did not happen, in plain words.
    notes: tuple[str, ...] = ()

    @property
    def speakers(self) -> tuple[str, ...]:
        seen: list[str] = []
        for segment in self.segments:
            if segment.speaker not in seen:
                seen.append(segment.speaker)
        return tuple(seen)

    def as_result(self, *, id: str, source_name: str) -> dict[str, Any]:
        """The ``result.json`` contract (version 1)."""
        return {
            "version": RESULT_VERSION,
            "id": id,
            "source_name": source_name,
            "duration_s": round(self.duration_s, 3),
            "language": self.language,
            "language_conf": self.language_conf,
            "language_source": self.language_source,
            "model": dict(self.model),
            "speakers": list(self.speakers),
            "diarized": self.diarized,
            "notes": list(self.notes),
            "segments": [
                {
                    "id": segment.id,
                    "start": round(segment.start, 3),
                    "end": round(segment.end, 3),
                    "speaker": segment.speaker,
                    "text": segment.text,
                    "words": [word.as_dict() for word in segment.words],
                }
                for segment in self.segments
            ],
        }

    @classmethod
    def from_result(cls, payload: Mapping[str, Any]) -> FileTranscript:
        """Back from ``result.json``: the renderers work on this, never on the engine."""
        segments = tuple(
            Segment(
                id=int(item["id"]),
                track="them",
                speaker=str(item["speaker"]),
                start=float(item["start"]),
                end=float(item["end"]),
                text=str(item["text"]),
                words=tuple(
                    Word(str(w["w"]), float(w["s"]), float(w["e"]), float(w.get("p", 1.0)))
                    for w in item.get("words", ())
                ),
            )
            for item in payload.get("segments", ())
        )
        conf = payload.get("language_conf")
        return cls(
            duration_s=float(payload.get("duration_s", 0.0)),
            segments=segments,
            language=payload.get("language"),
            language_conf=None if conf is None else float(conf),
            language_source=str(payload.get("language_source", "none")),
            model=dict(payload.get("model") or {}),
            diarized=bool(payload.get("diarized", False)),
            notes=tuple(str(note) for note in payload.get("notes", ())),
        )
