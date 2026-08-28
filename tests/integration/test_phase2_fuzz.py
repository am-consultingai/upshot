"""Phase 2 exit criteria: a randomized 500-job fuzz loses nothing."""

from __future__ import annotations

import random
from pathlib import Path

from app.clock import FakeClock, parse_iso
from app.config import default_config
from app.db.dao import Dao, connect
from app.errors import PermanentError, Preempted
from app.pipeline.queue import JobQueue
from app.pipeline.states import MeetingState
from app.pipeline.worker import Worker

JOBS = 500


def test_fuzz_500_jobs_nothing_lost(tmp_path: Path) -> None:
    rng = random.Random(20260828)
    conn = connect(tmp_path / "index.db")
    clock = FakeClock()
    dao = Dao(conn, clock)
    dao.seed_ids(5)
    queue = JobQueue(conn, clock, random.Random(3))
    config = default_config(job_policy="asap")

    executions: dict[int, int] = {}

    def fuzz(ctx) -> None:  # type: ignore[no-untyped-def]
        executions[ctx.job.id] = executions.get(ctx.job.id, 0) + 1
        roll = rng.random()
        if roll < 0.20:
            raise RuntimeError("transient")
        if roll < 0.25:
            raise Preempted("a meeting started")
        if roll < 0.28:
            raise PermanentError("unrecoverable")

    def build_worker() -> Worker:
        return Worker(
            dao=dao,
            queue=queue,
            config=config,
            stages={"fuzz": fuzz},
            clock=clock,
        )

    for index in range(JOBS):
        meeting = dao.insert_meeting(folder=tmp_path / "meetings" / f"m{index}", source="manual")
        dao.set_state(meeting.id, MeetingState.RECORDED)
        queue.enqueue(meeting.id, "fuzz")

    worker = build_worker()
    steps = 0
    while steps < 20_000:
        steps += 1
        if rng.random() < 0.01:  # a crash: the process dies with a job running
            claimed = queue.claim_next()
            if claimed is not None:
                worker = build_worker()  # restart resets it to pending
                assert queue.require(claimed.id).state == "pending"
                continue
        if worker.run_once():
            continue
        remaining = queue.seconds_until_runnable()
        if remaining is None:
            break
        clock.advance(max(remaining, 0) + 0.001)

    jobs = queue.all_jobs()
    assert len(jobs) == JOBS, "no job duplicated or lost"
    counts = queue.counts()
    assert sum(counts.values()) == JOBS
    assert counts.get("running", 0) == 0, "nothing stuck running"
    assert counts.get("pending", 0) == 0, "nothing stuck pending"
    assert set(counts) <= {"done", "failed"}
    assert counts.get("done", 0) > 0 and counts.get("failed", 0) > 0, counts
    assert len(executions) == JOBS, "every job ran at least once"

    for job in jobs:
        meeting = dao.require_meeting(job.meeting_id)
        if job.state == "failed":
            assert job.last_error
            assert meeting.state == MeetingState.FAILED
        else:
            assert job.finished_at is not None
            assert parse_iso(job.finished_at) <= clock.now()
    conn.close()
