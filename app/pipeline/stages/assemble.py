"""Two independent segment lists become one speaker-tagged timeline (§8).

Order of operations is the spec's: tag → stable merge → echo suppression → coalesce →
emit. Overlapping speech is preserved as two adjacent turns; the timestamps make the
overlap visible and the LLM handles it fine.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app import meta
from app.asr.backend import Segment, TranscriptFile, renumber, sort_segments
from app.db.dao import Turn as DbTurn
from app.log import get
from app.pipeline.artifacts import up_to_date
from app.pipeline.context import StageContext
from app.pipeline.stages.transcribe import load_segments, segments_path

log = get(__name__)

ECHO_SIMILARITY = 85
ECHO_WINDOW_S = 1.0
COALESCE_GAP_S = 2.0


@dataclass(frozen=True)
class Turn:
    speaker: str
    start: float
    end: float
    text: str

    @property
    def at_ms(self) -> int:
        return round(self.start * 1000)


def timestamp(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60:02d}:{total % 60:02d}"


def _overlaps(a: Segment, b: Segment, window: float = ECHO_WINDOW_S) -> bool:
    return a.start - window < b.end and b.start - window < a.end


def similarity(a: str, b: str) -> float:
    from rapidfuzz import fuzz

    return float(fuzz.ratio(a, b))


def suppress_echo(
    segments: Sequence[Segment], *, threshold: float = ECHO_SIMILARITY
) -> tuple[list[Segment], int]:
    """Your voice leaking into the loopback track is dropped from ``them``, not from ``me``."""
    mine = [segment for segment in segments if segment.track == "me"]
    dropped: set[int] = set()
    for segment in segments:
        if segment.track != "them" or segment.id in dropped:
            continue
        for own in mine:
            if not _overlaps(own, segment):
                continue
            if similarity(own.text, segment.text) >= threshold:
                dropped.add(segment.id)
                break
    kept = [segment for segment in segments if segment.id not in dropped]
    return kept, len(dropped)


def coalesce(segments: Sequence[Segment], *, gap_s: float = COALESCE_GAP_S) -> list[Turn]:
    turns: list[Turn] = []
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        if turns:
            last = turns[-1]
            if last.speaker == segment.speaker and segment.start - last.end < gap_s:
                turns[-1] = Turn(
                    last.speaker, last.start, max(last.end, segment.end), f"{last.text} {text}"
                )
                continue
        turns.append(Turn(segment.speaker, segment.start, segment.end, text))
    return turns


def render_markdown(turns: Sequence[Turn], *, title: str | None = None) -> str:
    lines: list[str] = []
    if title:
        lines.append(f"# {title}")
        lines.append("")
    for turn in turns:
        lines.append(f"**[{timestamp(turn.start)}] {turn.speaker}:** {turn.text}")
    return "\n".join(lines) + "\n"


def transcript_paths(folder: Path) -> tuple[Path, Path]:
    return Path(folder) / "transcript.json", Path(folder) / "transcript.md"


def run(ctx: StageContext) -> None:
    folder = ctx.folder
    source = segments_path(folder)
    transcript_json, transcript_md = transcript_paths(folder)
    if up_to_date(transcript_json, [source]) and up_to_date(transcript_md, [source]):
        log.info("transcript artifacts are current; skipping")
        return
    if not source.exists():
        raise FileNotFoundError(f"{source} is missing — run the transcribe stage first")

    segments, payload = load_segments(folder)
    ordered = sort_segments(segments)
    kept, echo_suppressed = suppress_echo(ordered)
    numbered = renumber(kept)
    turns = coalesce(numbered)

    transcript = TranscriptFile(
        language=str(payload.get("language", ctx.config.default_language)),
        model=dict(payload.get("model", {})),
        segments=numbered,
    )
    transcript.write(transcript_json)
    transcript_md.write_text(render_markdown(turns, title=ctx.meeting.title), encoding="utf-8")

    ctx.dao.index_turns(
        ctx.meeting.id,
        [
            DbTurn(seq=index, speaker=turn.speaker, at_ms=turn.at_ms, text=turn.text)
            for index, turn in enumerate(turns)
        ],
    )
    duration_s = round(max((segment.end for segment in numbered), default=0.0))
    ctx.dao.update_meeting(ctx.meeting.id, duration_s=duration_s)
    meta.mirror(
        ctx.refresh(),
        echo_suppressed=echo_suppressed,
        turns=len(turns),
        segments=len(numbered),
        duration_s=duration_s,
    )
    ctx.metrics.update(
        {"turns": len(turns), "echo_suppressed": echo_suppressed, "segments": len(numbered)}
    )
    log.info(
        "assembled %d segments into %d turns (%d echo copies dropped)",
        len(numbered),
        len(turns),
        echo_suppressed,
    )


def load_turns(folder: Path) -> list[Turn]:
    """Read back the assembled timeline — used by summarize and by the API."""
    transcript_json, _ = transcript_paths(folder)
    payload: dict[str, Any] = json.loads(transcript_json.read_text(encoding="utf-8"))
    segments = TranscriptFile.read(transcript_json).segments
    assert payload is not None
    return coalesce(segments)
