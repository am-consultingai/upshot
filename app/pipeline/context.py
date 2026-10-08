"""What a stage is handed. Kept in its own module so stages never import the worker."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.clock import Clock
from app.config import Config
from app.db.dao import Dao, Meeting
from app.errors import Cancelled, Preempted

if TYPE_CHECKING:  # pragma: no cover
    from app.pipeline.queue import Job, JobQueue


@dataclass
class StageContext:
    meeting: Meeting
    dao: Dao
    queue: JobQueue
    config: Config
    clock: Clock
    job: Job
    should_yield: Callable[[], bool] = lambda: False
    #: The user pressed the button. Stages must redo their work rather than deciding for
    #: themselves that what is already on disk will do.
    force: bool = False
    services: Any = None
    metrics: dict[str, Any] = field(default_factory=dict)
    #: Set by a stage that completed without moving the meeting forward — a draft
    #: delivery finishes its job but must leave the meeting at RENDERED.
    hold_state: bool = False
    #: ``(phase, fraction)``: how far the stage has got (``app/pipeline/progress.py``).
    #: Set by the worker for the stage that reports; anywhere else it goes nowhere.
    report: Callable[[str, float], None] = lambda _phase, _fraction: None

    @property
    def folder(self) -> Path:
        return Path(self.meeting.folder)

    def checkpoint(self) -> None:
        """Called between units of work. Raises :class:`Cancelled` when the meeting is being
        deleted, :class:`Preempted` when a meeting starts."""
        self.stop_if_deleted()
        if self.should_yield():
            raise Preempted(f"{self.job.stage} yielded to the recorder")

    def stop_if_deleted(self) -> None:
        """For work that cannot be resumed part-way (a track mid-transcription): stop only
        for a deletion, never for the recorder."""
        if self.queue.deleting(self.meeting.id):
            raise Cancelled(f"{self.job.stage} stopped: the meeting is being deleted")

    def refresh(self) -> Meeting:
        self.meeting = self.dao.require_meeting(self.meeting.id)
        return self.meeting
