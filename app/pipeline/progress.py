"""How far a running transcription has got, and how long it has left.

A file job's progress is a column (``transcriptions.progress``, D86); a meeting's lives
here, in memory: it describes one run of one stage and means nothing after a restart,
when the stage starts again from the beginning anyway. The meeting's transcribe stage
reports it as the file engine does — phases, weighted, with ``on_segment`` inside the
transcription of each track — and it is pushed as a ``job`` event (``action:
"progress"``) at most once a second, like the ``transcription`` one.

Time left is never a constant per state: it is this run's own pace, elapsed over
progress, and only once there is enough of the run to measure (:func:`eta_seconds`).
That pace is the whole run's average, and it is only re-measured when progress moves
(:class:`TimeLeft`, D94): a long chunk sends no ``on_segment`` for a minute, and a pace
recomputed while it is decoded falls toward zero, so the estimate climbed with the wait
(10% held for 50 s took "time left" from 195 s to 666 s).
Only the transcribe stage reports. Summarizing has no honest percentage: an LLM's time
is not predictable, so that stage is labelled and left indeterminate.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.clock import Clock, iso, parse_iso

#: No time left before this much of the job is done, or this much time has passed: the
#: first seconds are the fast phases (reading the file, finding the language), and a
#: rate measured on them promises a 2-hour file in a minute.
MIN_ETA_PROGRESS = 0.05
MIN_ETA_ELAPSED_S = 15.0

#: At most one progress event a second per meeting, as for file jobs.
EVENT_INTERVAL_S = 1.0

#: The meeting's transcribe stage, in order: the echo measured and the near track
#: cleaned, the language, both tracks transcribed, both diarized.
MEETING_PHASES: tuple[str, ...] = ("prepare", "language", "transcribe", "diarize")

#: As ``PHASE_WEIGHTS`` for a file: transcription dominates, diarization runs at 10–12×
#: real time (D85) over both tracks.
MEETING_WEIGHTS: dict[str, float] = {
    "prepare": 0.03,
    "language": 0.04,
    "transcribe": 0.75,
    "diarize": 0.18,
}


def eta_seconds(progress: float | None, elapsed_s: float) -> int | None:
    """Seconds left at this run's pace, or ``None`` while there is too little to go on.

    Shared by meeting and file jobs, so the two never disagree about what "~5 min left"
    means.
    """
    if progress is None or progress >= 1.0:
        return None
    if progress < MIN_ETA_PROGRESS or elapsed_s < MIN_ETA_ELAPSED_S:
        return None
    return max(0, round(elapsed_s * (1.0 - progress) / progress))


def eta_since(progress: float | None, started_at: str | None, now: datetime) -> int | None:
    """:func:`eta_seconds` for a job that started at ``started_at`` (an ISO stamp)."""
    if not started_at:
        return None
    return eta_seconds(progress, (now - parse_iso(started_at)).total_seconds())


@dataclass
class TimeLeft:
    """One run's time left, held while its progress stands still (D94).

    The estimate is the whole run's average pace (:func:`eta_since`), measured when
    progress moves. While it does not move, the last estimate holds: a pace that falls
    only because no callback came is not news. Once the wait outlasts the held figure
    it grows with the wait, never faster than real time and never by a jump, and never
    past what the plain average would now say.
    """

    progress: float | None = None
    since: datetime | None = None
    held: int | None = None

    def at(self, progress: float | None, started_at: str | None, now: datetime) -> int | None:
        pace = eta_since(progress, started_at, now)
        if pace is None or progress is None:
            self.progress, self.since, self.held = None, None, None
            return pace
        moved = self.progress is None or round(progress, 4) != round(self.progress, 4)
        if moved or self.held is None or self.since is None:
            self.progress, self.since, self.held = progress, now, pace
            return pace
        waited = round((now - self.since).total_seconds())
        return min(pace, max(self.held, waited))


def meeting_progress(phase: str, fraction: float) -> float:
    """``fraction`` of ``phase`` as a fraction of the whole transcribe stage, 0–1."""
    done = 0.0
    for name in MEETING_PHASES:
        if name == phase:
            share = MEETING_WEIGHTS[name] * max(0.0, min(1.0, fraction))
            return round(min(1.0, done + share), 4)
        done += MEETING_WEIGHTS[name]
    raise ValueError(f"unknown phase {phase!r}")


@dataclass
class _Run:
    stage: str
    started_at: str
    phase: str | None = None
    progress: float = 0.0
    last_event: float | None = None
    time_left: TimeLeft = field(default_factory=TimeLeft)


class MeetingProgress:
    """The meeting stages running now and how far each has got. Written by the worker
    thread, read by the API's: under one lock."""

    def __init__(self, clock: Clock, events: Any = None) -> None:
        self.clock = clock
        self.events = events
        self._runs: dict[str, _Run] = {}
        self._lock = threading.Lock()

    def start(self, meeting_id: str, stage: str) -> None:
        with self._lock:
            self._runs[meeting_id] = _Run(stage=stage, started_at=iso(self.clock.now()))

    def report(self, meeting_id: str, phase: str, fraction: float) -> None:
        """Never backwards: a phase that starts at a lower figure than the last one
        reported (it cannot, by the weights; a rounding can) holds the figure still."""
        overall = meeting_progress(phase, fraction)
        now = self.clock.monotonic()
        with self._lock:
            run = self._runs.get(meeting_id)
            if run is None:
                return
            new_phase = run.phase != phase
            run.phase = phase
            run.progress = max(run.progress, overall)
            # A new phase is said at once, so the words change with the work.
            due = new_phase or run.last_event is None or now - run.last_event >= EVENT_INTERVAL_S
            if due:
                run.last_event = now
            snapshot = self._snapshot(run) if due else None
        if snapshot is not None and self.events is not None:
            self.events.publish(
                "job", action="progress", meeting_id=meeting_id, state="running", **snapshot
            )

    def finish(self, meeting_id: str) -> None:
        with self._lock:
            self._runs.pop(meeting_id, None)

    def clear(self) -> None:
        with self._lock:
            self._runs.clear()

    def get(self, meeting_id: str) -> dict[str, Any] | None:
        """``stage``, ``phase``, ``progress`` (0–1) and ``eta_s``; ``None`` when this
        meeting has no stage reporting progress now."""
        with self._lock:
            run = self._runs.get(meeting_id)
            return self._snapshot(run) if run is not None else None

    def _snapshot(self, run: _Run) -> dict[str, Any]:
        return {
            "stage": run.stage,
            "phase": run.phase,
            "progress": round(run.progress, 4),
            "eta_s": run.time_left.at(run.progress, run.started_at, self.clock.now()),
            "started_at": run.started_at,
        }
