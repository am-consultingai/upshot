"""Retention: the raw audio goes after N days, the transcript stays (D38).

``retention.audio_days`` has shipped in the default config since the first release and
nothing ever read it (D27). A key that promises deletion and does not delete is a trust
problem — and the mirror of it, a sweep that deletes silently, is worse. So this module
splits in two: :func:`plan` decides and explains, :func:`sweep` acts. ``GET /api/retention``
serves the plan without touching anything, which means the policy can be inspected on a
real machine before it is believed.

Two clocks, deliberately separate:

- ``retention.audio_days`` (30) removes the raw WAVs. A 45-minute meeting is ~85 MB of
  audio against a few kilobytes of transcript, so this is the one that earns its keep.
- ``retention.transcript_days`` (``null``) removes the meeting entirely. Off by default:
  transcripts and summaries are kept indefinitely (DESIGN.md §17).

The audio is the only copy of a meeting until the transcript exists, so a meeting that has
not been transcribed keeps its audio however old it is — and the sweep says so by name
rather than skipping it silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.clock import Clock, parse_iso
from app.config import Config
from app.db.dao import Dao, Meeting
from app.log import get
from app.meetings import MeetingService, audio_bytes, tree_bytes
from app.pipeline.queue import JobQueue
from app.pipeline.states import JobState, MeetingState

log = get(__name__)

#: A meeting in one of these is not finished with its own folder yet.
BUSY_STATES = frozenset({MeetingState.ARMED, MeetingState.RECORDING})

#: How much of the transcript has to exist before the audio is expendable.
TRANSCRIPT_NAME = "transcript.md"


@dataclass(frozen=True)
class Candidate:
    meeting_id: str
    title: str | None
    folder: Path
    age_days: float
    bytes: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "meeting_id": self.meeting_id,
            "title": self.title,
            "folder": str(self.folder),
            "age_days": round(self.age_days, 1),
            "bytes": self.bytes,
        }


@dataclass(frozen=True)
class Plan:
    """What a sweep would do right now, and what it would decline to do."""

    audio_days: int | None
    transcript_days: int | None
    audio: tuple[Candidate, ...] = ()
    meetings: tuple[Candidate, ...] = ()
    spared: tuple[tuple[str, str], ...] = ()

    @property
    def bytes(self) -> int:
        return sum(item.bytes for item in (*self.audio, *self.meetings))

    def __bool__(self) -> bool:
        return bool(self.audio or self.meetings)

    def as_dict(self) -> dict[str, Any]:
        return {
            "audio_days": self.audio_days,
            "transcript_days": self.transcript_days,
            "audio": [item.as_dict() for item in self.audio],
            "meetings": [item.as_dict() for item in self.meetings],
            "spared": [{"meeting_id": mid, "reason": why} for mid, why in self.spared],
            "bytes": self.bytes,
        }


@dataclass
class SweepResult:
    plan: Plan
    audio_removed: int = 0
    meetings_removed: int = 0
    bytes_freed: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "audio_removed": self.audio_removed,
            "meetings_removed": self.meetings_removed,
            "bytes_freed": self.bytes_freed,
            "errors": self.errors,
            "plan": self.plan.as_dict(),
        }


def days_of(value: Any) -> int | None:
    """``null`` and ``0`` both mean *never delete*; anything unparseable does too."""
    if value is None:
        return None
    try:
        days = int(value)
    except (TypeError, ValueError):
        return None
    return days if days > 0 else None


def age_days(meeting: Meeting, now: datetime) -> float:
    """How long ago the meeting ended — or started, for one that never ended cleanly."""
    stamp = meeting.ended_at or meeting.started_at
    try:
        when = parse_iso(stamp)
    except ValueError:  # pragma: no cover - every timestamp is written by app.clock.iso
        return 0.0
    return (now - when).total_seconds() / 86400.0


def _blocked(
    meeting: Meeting, *, meetings: MeetingService, queue: JobQueue, recorder: Any
) -> str | None:
    """Why this meeting must be left alone, or ``None`` when it may be swept."""
    if meeting.meeting_state in BUSY_STATES:
        return "still recording"
    if (
        recorder is not None
        and getattr(recorder, "committed", False)
        and getattr(recorder, "meeting_id", None) == meeting.id
    ):
        return "still recording"
    if not meetings.inside_root(meeting.path):
        return "outside the data folder"
    if any(
        job.job_state in (JobState.PENDING, JobState.RUNNING)
        for job in queue.for_meeting(meeting.id)
    ):
        return "a job is still queued"
    return None


def plan(
    *,
    dao: Dao,
    queue: JobQueue,
    meetings: MeetingService,
    config: Config,
    clock: Clock,
    recorder: Any = None,
) -> Plan:
    """What the policy would remove, without removing anything."""
    audio_days = days_of(config.get("retention.audio_days"))
    transcript_days = days_of(config.get("retention.transcript_days"))
    empty = Plan(audio_days, transcript_days)
    if audio_days is None and transcript_days is None:
        return empty

    now = clock.now()
    doomed_audio: list[Candidate] = []
    doomed_meetings: list[Candidate] = []
    spared: list[tuple[str, str]] = []

    for meeting in dao.list_meetings(limit=100_000):
        age = age_days(meeting, now)
        old_enough_to_purge = transcript_days is not None and age >= transcript_days
        old_enough_to_strip = audio_days is not None and age >= audio_days
        if not (old_enough_to_purge or old_enough_to_strip):
            continue
        folder = Path(meeting.path)
        reason = _blocked(meeting, meetings=meetings, queue=queue, recorder=recorder)
        if reason is not None:
            spared.append((meeting.id, reason))
            continue
        if old_enough_to_purge:
            doomed_meetings.append(
                Candidate(meeting.id, meeting.title, folder, age, tree_bytes(folder))
            )
            continue
        size = audio_bytes(folder)
        if size == 0:
            continue  # already swept, or never had any; not worth reporting
        if not (folder / TRANSCRIPT_NAME).exists():
            spared.append((meeting.id, "not transcribed yet — the audio is the only copy"))
            continue
        doomed_audio.append(Candidate(meeting.id, meeting.title, folder, age, size))

    return Plan(
        audio_days=audio_days,
        transcript_days=transcript_days,
        audio=tuple(doomed_audio),
        meetings=tuple(doomed_meetings),
        spared=tuple(spared),
    )


def sweep(
    *,
    dao: Dao,
    queue: JobQueue,
    meetings: MeetingService,
    config: Config,
    clock: Clock,
    recorder: Any = None,
    events: Any = None,
) -> SweepResult:
    """Apply the policy. Every failure is per-meeting: one bad folder is not a stoppage."""
    decided = plan(
        dao=dao, queue=queue, meetings=meetings, config=config, clock=clock, recorder=recorder
    )
    result = SweepResult(plan=decided)
    now = clock.now()

    for candidate in decided.audio:
        try:
            meeting = dao.require_meeting(candidate.meeting_id)
            result.bytes_freed += meetings.drop_audio(meeting, when=now)
        except Exception as exc:
            log.warning("retention could not remove audio for %s: %s", candidate.meeting_id, exc)
            result.errors.append(f"{candidate.meeting_id}: {exc}")
            continue
        result.audio_removed += 1
        if events is not None:
            events.publish("meeting", meeting_id=candidate.meeting_id, action="audio_deleted")

    for candidate in decided.meetings:
        try:
            meeting = dao.require_meeting(candidate.meeting_id)
            meetings.purge(meeting)
        except Exception as exc:
            log.warning("retention could not delete %s: %s", candidate.meeting_id, exc)
            result.errors.append(f"{candidate.meeting_id}: {exc}")
            continue
        result.meetings_removed += 1
        result.bytes_freed += candidate.bytes
        if events is not None:
            events.publish("meeting", meeting_id=candidate.meeting_id, action="deleted")

    if result.audio_removed or result.meetings_removed:
        log.info(
            "retention swept %d audio folder(s) and %d meeting(s), freeing %.1f MB",
            result.audio_removed,
            result.meetings_removed,
            result.bytes_freed / 1e6,
        )
    return result


def sweep_services(services: Any) -> SweepResult:
    """The sweep as the running application calls it."""
    return sweep(
        dao=services.dao,
        queue=services.queue,
        meetings=services.meetings,
        config=services.config,
        clock=services.clock,
        recorder=services.recorder,
        events=services.events,
    )
