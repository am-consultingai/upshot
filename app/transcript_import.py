"""Bring in a transcript that was produced somewhere else, as a finished meeting.

The transcribe and assemble stages are skipped rather than run: the text already exists,
and re-deriving it would spend minutes of GPU arriving at an answer we were handed. The
meeting lands in ``TRANSCRIBED``, which is exactly where the summarize stage picks up.

The recording, if there is one, is ingested only so the player has something to play.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from app import meta
from app.asr.backend import Segment, TranscriptFile
from app.db.dao import Turn
from app.log import get
from app.pipeline.states import MeetingState
from app.services import Services

log = get(__name__)

#: A transcript with no speaker labels cannot be given real ones. THEM is the honest
#: default: it means "someone other than the recorder", which is true of most of a
#: meeting and never claims a specific person said a specific thing.
DEFAULT_SPEAKER = "THEM"


@dataclass(frozen=True)
class ImportedTranscript:
    meeting_id: str
    turns: int
    audio_seconds: float


def lines_of(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def spread(lines: list[str], total_s: float) -> list[int]:
    """Start times in ms, spread across the recording in proportion to line length.

    A transcript exported as plain text has no timestamps, and the summary schema wants
    one per turn so a reader can click back into the audio. Proportional placement is a
    guess, but a defensible one — a long line did take longer to say than a short one —
    and it is far closer than spacing every line equally.
    """
    if not lines:
        return []
    weights = [max(len(line), 1) for line in lines]
    total_weight = sum(weights)
    starts: list[int] = []
    running = 0
    for weight in weights:
        starts.append(int(running / total_weight * total_s * 1000))
        running += weight
    return starts


def markdown(lines: list[str], starts: list[int], speaker: str) -> str:
    out = []
    for line, start in zip(lines, starts, strict=True):
        minutes, seconds = divmod(start // 1000, 60)
        out.append(f"**[{minutes:02d}:{seconds:02d}] {speaker}:** {line}")
    return "\n\n".join(out) + "\n"


def import_transcript(
    services: Services,
    *,
    text: str,
    title: str,
    started_at: datetime,
    duration_s: int,
    audio: Path | None = None,
    language: str = "he",
    speaker: str = DEFAULT_SPEAKER,
) -> ImportedTranscript:
    """Create a TRANSCRIBED meeting from text, with the recording attached if given."""
    lines = lines_of(text)
    if not lines:
        raise ValueError("the transcript is empty")

    meeting = services.meetings.create(
        source="imported", started_at=started_at, title=title, title_source="import"
    )
    folder = meeting.path
    folder.mkdir(parents=True, exist_ok=True)

    audio_seconds = float(duration_s)
    if audio is not None:
        from app.audio.ingest import ingest

        imported = ingest(Path(audio), folder, config=services.config)
        audio_seconds = float(imported.duration_s)
        log.info("ingested %s (%.0fs)", audio, audio_seconds)

    # Timestamps follow the audio, not the calendar slot: they are for clicking back into
    # the recording, and a 30-minute booking that holds 17 minutes of audio would put
    # every one of them past the end of the file.
    starts = spread(lines, audio_seconds)
    (folder / "transcript.md").write_text(markdown(lines, starts, speaker), encoding="utf-8")
    TranscriptFile(
        language=language,
        model={"name": "imported", "device": "none", "compute": "none"},
        segments=[
            Segment(
                id=index,
                track="me" if speaker == "ME" else "them",
                speaker=speaker,
                start=start / 1000,
                end=(next_start if next_start else start + 4000) / 1000,
                text=line,
            )
            for index, (line, start, next_start) in enumerate(
                zip(lines, starts, [*starts[1:], 0], strict=True)
            )
        ],
    ).write(folder / "transcript.json")
    services.dao.index_turns(
        meeting.id,
        [
            Turn(index, speaker, start, line)
            for index, (line, start) in enumerate(zip(lines, starts, strict=True))
        ],
    )

    services.meetings.finish(
        meeting.id,
        ended_at=started_at + timedelta(seconds=duration_s),
        duration_s=duration_s,
        enqueue=False,  # the whole point: nothing gets re-transcribed
    )
    services.dao.update_meeting(meeting.id, language=language)
    services.dao.set_state(meeting.id, MeetingState.TRANSCRIBING)
    updated = services.dao.set_state(meeting.id, MeetingState.TRANSCRIBED)
    meta.mirror(updated)
    log.info("imported %s as %s (%d turns)", title, meeting.id, len(lines))
    return ImportedTranscript(meeting.id, len(lines), audio_seconds)
