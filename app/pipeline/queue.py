"""The job queue is a table and the worker is a loop (TECHNICAL-DESIGN.md §6).

``claim_next`` is one ``UPDATE … RETURNING`` guarded by the WAL, so several workers (or
several threads) racing for the same row cannot both win.
"""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from app.clock import Clock, SystemClock, iso, parse_iso
from app.log import get
from app.pipeline.states import (
    STAGE_DONE_STATE,
    STAGE_ORDER,
    JobStage,
    JobState,
    next_stage,
)

log = get(__name__)

MAX_ATTEMPTS = 5
MAX_BACKOFF_S = 3600
JITTER = 0.10

JOB_COLUMNS = (
    "id",
    "meeting_id",
    "stage",
    "state",
    "attempts",
    "not_before",
    "priority",
    "last_error",
    "started_at",
    "finished_at",
    "created_at",
    "updated_at",
)


@dataclass(frozen=True, slots=True)
class Job:
    id: int
    meeting_id: str
    stage: str
    state: str
    attempts: int
    not_before: str | None
    priority: int
    last_error: str | None
    started_at: str | None
    finished_at: str | None
    created_at: str
    updated_at: str

    @property
    def job_state(self) -> JobState:
        return JobState(self.state)


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(**{key: row[key] for key in JOB_COLUMNS})


def backoff_seconds(attempts: int, rng: random.Random) -> float:
    """Exponential, capped at an hour, jittered ±10 %."""
    base = float(min(2**attempts, MAX_BACKOFF_S))
    return base * (1.0 + rng.uniform(-JITTER, JITTER))


class JobQueue:
    def __init__(
        self,
        conn: sqlite3.Connection,
        clock: Clock | None = None,
        rng: random.Random | None = None,
        *,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> None:
        self.conn = conn
        self.clock = clock or SystemClock()
        self.rng = rng or random.Random()
        self.max_attempts = max_attempts

    # -- writing -----------------------------------------------------------

    def enqueue(
        self,
        meeting_id: str,
        stage: JobStage | str,
        *,
        priority: int = 100,
        not_before: str | None = None,
    ) -> Job:
        """Idempotent: re-enqueuing an existing stage makes it runnable again."""
        now = iso(self.clock.now())
        self.conn.execute(
            "INSERT INTO jobs(meeting_id, stage, state, attempts, not_before, priority, "
            "created_at, updated_at) VALUES (?,?,?,0,?,?,?,?) "
            "ON CONFLICT(meeting_id, stage) DO UPDATE SET state='pending', "
            "not_before=excluded.not_before, priority=excluded.priority, "
            "updated_at=excluded.updated_at",
            (meeting_id, str(stage), JobState.PENDING, not_before, priority, now, now),
        )
        job = self.get_by_stage(meeting_id, str(stage))
        assert job is not None
        return job

    def claim_next(self) -> Job | None:
        now = iso(self.clock.now())
        row = self.conn.execute(
            "UPDATE jobs SET state='running', started_at=?, updated_at=? "
            "WHERE id = (SELECT id FROM jobs WHERE state='pending' "
            "            AND (not_before IS NULL OR not_before <= ?) "
            "            ORDER BY priority, id LIMIT 1) "
            "RETURNING *",
            (now, now, now),
        ).fetchone()
        return _row_to_job(row) if row else None

    def complete(self, job: Job) -> Job:
        now = iso(self.clock.now())
        self.conn.execute(
            "UPDATE jobs SET state='done', finished_at=?, updated_at=?, last_error=NULL "
            "WHERE id = ?",
            (now, now, job.id),
        )
        return self.require(job.id)

    def release(self, job: Job) -> Job:
        """A preempted job goes back to pending **without** counting an attempt."""
        now = iso(self.clock.now())
        self.conn.execute(
            "UPDATE jobs SET state='pending', started_at=NULL, updated_at=? WHERE id = ?",
            (now, job.id),
        )
        return self.require(job.id)

    def fail(self, job: Job, error: BaseException | str, *, permanent: bool = False) -> Job:
        now = self.clock.now()
        attempts = job.attempts + 1
        message = f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else error
        if permanent or attempts >= self.max_attempts:
            self.conn.execute(
                "UPDATE jobs SET state='failed', attempts=?, last_error=?, finished_at=?, "
                "updated_at=?, not_before=NULL WHERE id = ?",
                (attempts, message[:2000], iso(now), iso(now), job.id),
            )
        else:
            delay = backoff_seconds(attempts, self.rng)
            self.conn.execute(
                "UPDATE jobs SET state='pending', attempts=?, last_error=?, not_before=?, "
                "started_at=NULL, updated_at=? WHERE id = ?",
                (
                    attempts,
                    message[:2000],
                    iso(now + timedelta(seconds=delay)),
                    iso(now),
                    job.id,
                ),
            )
        return self.require(job.id)

    def cancel(self, job: Job) -> Job:
        now = iso(self.clock.now())
        self.conn.execute(
            "UPDATE jobs SET state='cancelled', updated_at=? WHERE id = ?", (now, job.id)
        )
        return self.require(job.id)

    def enqueue_next_stage(self, job: Job) -> Job | None:
        """Completing a stage enqueues its successor — and nothing else."""
        try:
            stage = JobStage(job.stage)
        except ValueError:
            return None
        following = next_stage(stage)
        if following is None:
            return None
        return self.enqueue(job.meeting_id, following)

    def reset_running(self) -> int:
        """Crash recovery: anything left ``running`` by a dead process is runnable again."""
        now = iso(self.clock.now())
        cur = self.conn.execute(
            "UPDATE jobs SET state='pending', started_at=NULL, updated_at=? WHERE state='running'",
            (now,),
        )
        count = int(cur.rowcount or 0)
        if count:
            log.info("crash recovery: reset %d running job(s) to pending", count)
        return count

    def retry(self, meeting_id: str, stage: JobStage | str) -> Job:
        """Re-run one stage: pending again, attempts reset."""
        now = iso(self.clock.now())
        self.conn.execute(
            "UPDATE jobs SET state='pending', attempts=0, not_before=NULL, last_error=NULL, "
            "started_at=NULL, finished_at=NULL, updated_at=? WHERE meeting_id=? AND stage=?",
            (now, meeting_id, str(stage)),
        )
        job = self.get_by_stage(meeting_id, str(stage))
        if job is None:
            return self.enqueue(meeting_id, stage)
        return job

    # -- reading -----------------------------------------------------------

    def get(self, job_id: int) -> Job | None:
        row = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_job(row) if row else None

    def require(self, job_id: int) -> Job:
        job = self.get(job_id)
        if job is None:
            raise KeyError(f"no such job: {job_id}")
        return job

    def get_by_stage(self, meeting_id: str, stage: str) -> Job | None:
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE meeting_id = ? AND stage = ?", (meeting_id, stage)
        ).fetchone()
        return _row_to_job(row) if row else None

    def for_meeting(self, meeting_id: str) -> list[Job]:
        rows = self.conn.execute(
            "SELECT * FROM jobs WHERE meeting_id = ? ORDER BY id", (meeting_id,)
        ).fetchall()
        return [_row_to_job(row) for row in rows]

    def counts(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT state, count(*) AS n FROM jobs GROUP BY state").fetchall()
        return {row["state"]: row["n"] for row in rows}

    def depth(self) -> int:
        row = self.conn.execute(
            "SELECT count(*) AS n FROM jobs WHERE state IN ('pending','running')"
        ).fetchone()
        return int(row["n"])

    def next_runnable_at(self) -> str | None:
        row = self.conn.execute(
            "SELECT min(not_before) AS t FROM jobs WHERE state='pending' AND not_before IS NOT NULL"
        ).fetchone()
        return row["t"] if row and row["t"] else None

    def seconds_until_runnable(self) -> float | None:
        when = self.next_runnable_at()
        if when is None:
            return None
        return (parse_iso(when) - self.clock.now()).total_seconds()

    def all_jobs(self) -> list[Job]:
        rows = self.conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()
        return [_row_to_job(row) for row in rows]

    def stage_names(self) -> tuple[str, ...]:
        return tuple(str(stage) for stage in STAGE_ORDER)

    def done_state(self, stage: str) -> Any:
        try:
            return STAGE_DONE_STATE[JobStage(stage)]
        except ValueError:
            return None
