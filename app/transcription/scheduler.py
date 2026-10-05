"""One worker, two queues, first in, first out (R5, D86).

Meeting jobs keep their own order among themselves (``priority, id``); file jobs are
ordered by ``queued_at``. Between the two heads, the earlier FIFO key goes first, and a
meeting wins a tie. A meeting's key is its ``jobs.queued_at``, set when it entered the
queue and inherited by its later stages, so a meeting being transcribed finishes its
summary before a file queued after it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.clock import parse_iso
from app.pipeline.queue import Job, JobQueue
from app.transcription.store import Transcription, TranscriptionStore

#: Peek and claim race only with an API thread cancelling or deleting; a few tries is
#: plenty, and bounding it keeps a pathological loop out of the worker.
CLAIM_TRIES = 5


@dataclass(frozen=True, slots=True)
class Head:
    item: Job | Transcription
    key: datetime


def _key(item: Job | Transcription) -> datetime:
    return parse_iso(item.fifo_key)


class Scheduler:
    def __init__(self, queue: JobQueue, store: TranscriptionStore) -> None:
        self.queue = queue
        self.store = store

    def next_head(self) -> Head | None:
        """The job that would run next, without claiming it."""
        meeting = self.queue.peek()
        file = self.store.peek()
        if meeting is None and file is None:
            return None
        if file is None:
            assert meeting is not None
            return Head(meeting, _key(meeting))
        if meeting is None:
            return Head(file, _key(file))
        meeting_key, file_key = _key(meeting), _key(file)
        if file_key < meeting_key:
            return Head(file, file_key)
        return Head(meeting, meeting_key)

    def claim_next_any(self) -> Job | Transcription | None:
        """Claim the head, or the next head if another thread took this one away."""
        for _ in range(CLAIM_TRIES):
            head = self.next_head()
            if head is None:
                return None
            item = head.item
            claimed = (
                self.queue.claim(item.id) if isinstance(item, Job) else self.store.claim(item.id)
            )
            if claimed is not None:
                return claimed
        return None

    def depth(self) -> int:
        """Pending and running work of both kinds: what makes the tray say "busy"."""
        return self.queue.depth() + self.store.depth()

    def position(self, job: Transcription) -> int | None:
        """1 for the next to run; ``None`` once it is not waiting. Counts what is ahead of
        it in either queue, by key, including work held back by a backoff."""
        if job.state != "pending":
            return None
        key = _key(job)
        files = sum(1 for other in self.store.pending() if (_key(other), other.id) < (key, job.id))
        meetings = sum(1 for stamp in self.queue.pending_keys() if parse_iso(stamp) <= key)
        running = 1 if self.store.running() is not None or self._meeting_running() else 0
        return files + meetings + running + 1

    def files_ahead(self, meeting_job: Job) -> int:
        """File jobs that will run before this meeting job: the meeting page's "N files
        ahead in the queue"."""
        key = _key(meeting_job)
        return sum(1 for other in self.store.pending() if _key(other) < key)

    def _meeting_running(self) -> bool:
        return bool(self.queue.counts().get("running", 0))
