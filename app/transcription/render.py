"""A finished file transcription in each format it is offered in (D86, plan §4.3).

Everything is rendered on request from ``result.json``, so a different
``max_words_per_cue`` needs no new transcription. ``json`` is the stored result itself:
the stable contract. The others are views of it:

- ``txt``: speaker turns as paragraphs, ``S1: …``; with timestamps, ``[mm:ss] S1: …``.
- ``md``: the meeting transcript's own format (``coalesce`` and ``render_markdown``).
- ``srt``, ``vtt``: subtitle cues from the word timings. A cue never spans two segments,
  and ends at a speaker change, after ``max_words_per_cue`` words, at 42 characters,
  after 6 seconds, or at a pause over 0.6 s. A segment with no word timings is one cue.
  VTT names the voice (``<v S1>``) when more than one person speaks; SRT stays plain
  text, since it is the format burned into video.

Hebrew and every other right-to-left language is written as it is, with no direction
marks: players lay out each cue by its own text.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.asr.backend import Segment, Word
from app.pipeline.stages.assemble import coalesce, render_markdown, timestamp
from app.transcription.types import DEFAULT_WORDS_PER_CUE, MAX_WORDS_PER_CUE, FileTranscript

FORMATS: dict[str, tuple[str, str]] = {
    "json": ("application/json", "json"),
    "txt": ("text/plain; charset=utf-8", "txt"),
    "md": ("text/markdown; charset=utf-8", "md"),
    "srt": ("application/x-subrip; charset=utf-8", "srt"),
    "vtt": ("text/vtt; charset=utf-8", "vtt"),
}

MAX_CUE_CHARS = 42
MAX_CUE_SECONDS = 6.0
MAX_PAUSE_SECONDS = 0.6


@dataclass(frozen=True, slots=True)
class Cue:
    start: float
    end: float
    speaker: str
    text: str


def render(
    payload: Mapping[str, Any],
    fmt: str,
    *,
    max_words_per_cue: int | None = None,
    timestamps: bool = False,
) -> str:
    """``payload`` is a ``result.json``. ``max_words_per_cue`` is the job's own option, which
    the caller passes (it is not in the result); ``None`` means the default."""
    if fmt not in FORMATS:
        raise ValueError(f"unknown format {fmt!r}; one of {', '.join(FORMATS)}")
    if fmt == "json":
        return json.dumps(payload, ensure_ascii=False, indent=1) + "\n"
    transcript = FileTranscript.from_result(payload)
    if fmt == "txt":
        return render_text(transcript.segments, timestamps=timestamps)
    if fmt == "md":
        return render_markdown(coalesce(transcript.segments)) if transcript.segments else ""
    words = clamp_words(DEFAULT_WORDS_PER_CUE if max_words_per_cue is None else max_words_per_cue)
    cues = build_cues(transcript.segments, max_words=words)
    if fmt == "srt":
        return render_srt(cues)
    return render_vtt(cues, voices=len(transcript.speakers) > 1)


def clamp_words(value: int) -> int:
    return max(1, min(MAX_WORDS_PER_CUE, int(value)))


def render_text(segments: Sequence[Segment], *, timestamps: bool = False) -> str:
    turns = coalesce(segments)
    if not turns:
        return ""
    lines = [
        f"[{timestamp(turn.start)}] {turn.speaker}: {turn.text}"
        if timestamps
        else f"{turn.speaker}: {turn.text}"
        for turn in turns
    ]
    return "\n\n".join(lines) + "\n"


# ----------------------------------------------------------------------- cues


def build_cues(segments: Sequence[Segment], *, max_words: int = DEFAULT_WORDS_PER_CUE) -> list[Cue]:
    cues: list[Cue] = []
    for segment in segments:
        words = [word for word in segment.words if word.w.strip()]
        if not words:
            text = segment.text.strip()
            if text:
                cues.append(
                    Cue(segment.start, max(segment.end, segment.start), segment.speaker, text)
                )
            continue
        current: list[Word] = []
        for word in words:
            if current and _breaks(current, word, max_words):
                cues.append(_cue(current, segment.speaker))
                current = []
            current.append(word)
        cues.append(_cue(current, segment.speaker))
    return cues


def _breaks(current: Sequence[Word], word: Word, max_words: int) -> bool:
    if len(current) >= max_words:
        return True
    if len(_join([*current, word])) > MAX_CUE_CHARS:
        return True
    if word.e - current[0].s > MAX_CUE_SECONDS:
        return True
    return word.s - current[-1].e > MAX_PAUSE_SECONDS


def _join(words: Sequence[Word]) -> str:
    return " ".join(word.w.strip() for word in words)


def _cue(words: Sequence[Word], speaker: str) -> Cue:
    start = words[0].s
    return Cue(start, max(words[-1].e, start), speaker, _join(words))


def srt_time(seconds: float) -> str:
    millis = max(0, round(seconds * 1000))
    hours, rest = divmod(millis, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def vtt_time(seconds: float) -> str:
    return srt_time(seconds).replace(",", ".")


def render_srt(cues: Sequence[Cue]) -> str:
    blocks = [
        f"{index}\n{srt_time(cue.start)} --> {srt_time(cue.end)}\n{cue.text}"
        for index, cue in enumerate(cues, start=1)
    ]
    return "\n\n".join(blocks) + "\n" if blocks else ""


def _vtt_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_vtt(cues: Sequence[Cue], *, voices: bool = False) -> str:
    blocks = ["WEBVTT"]
    for cue in cues:
        text = _vtt_escape(cue.text)
        if voices:
            text = f"<v {cue.speaker}>{text}"
        blocks.append(f"{vtt_time(cue.start)} --> {vtt_time(cue.end)}\n{text}")
    return "\n\n".join(blocks) + "\n"


def filename(source_name: str, fmt: str) -> str:
    """``interview.mp4`` → ``interview.srt``."""
    stem = source_name.rsplit(".", 1)[0] if "." in source_name else source_name
    return f"{stem or 'transcript'}.{FORMATS[fmt][1]}"
