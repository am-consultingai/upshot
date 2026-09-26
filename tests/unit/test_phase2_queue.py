from __future__ import annotations

import random
import sqlite3
import threading
from pathlib import Path

import pytest

from app.clock import FakeClock, parse_iso
from app.config import default_config
from app.db.dao import Dao, connect
from app.pipeline.activity import FakeActivity, FakeRecorderState
from app.pipeline.fake_stages import FakeStages
from app.pipeline.queue import JobQueue
from app.pipeline.states import PIPELINE, JobStage, MeetingState
from app.pipeline.worker import Worker


class Harness:
    def __init__(self, tmp_path: Path, *, policy: str = "asap") -> None:
        self.path = tmp_path / "index.db"
        self.conn = connect(self.path)
        self.clock = FakeClock()
        self.dao = Dao(self.conn, self.clock)
        self.dao.seed_ids(99)
        self.queue = JobQueue(self.conn, self.clock, random.Random(7))
        self.config = default_config(job_policy=policy)
        self.stages = FakeStages()
        self.recorder = FakeRecorderState(False)
        self.activity = FakeActivity(False)
        self.tmp_path = tmp_path
        self.worker = Worker(
            dao=self.dao,
            queue=self.queue,
            config=self.config,
            stages=self.stages.registry(),  # type: ignore[arg-type]
            clock=self.clock,
            recorder=self.recorder,
            activity=self.activity,
        )

    def meeting(self, state: MeetingState = MeetingState.RECORDED):  # type: ignore[no-untyped-def]
        m = self.dao.insert_meeting(
            folder=self.tmp_path / "meetings" / "m1", source="manual", state=MeetingState.RECORDING
        )
        for step in PIPELINE:
            if step is MeetingState.RECORDING:
                continue
            if self.dao.require_meeting(m.id).state == str(state):
                break
            self.dao.set_state(m.id, step)
        return self.dao.require_meeting(m.id)


@pytest.fixture
def h(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


def test_claim_is_exclusive(tmp_path: Path) -> None:
    """Eight threads, eight connections, one job — exactly one winner."""
    path = tmp_path / "index.db"
    conn = connect(path)
    dao = Dao(conn, FakeClock())
    meeting = dao.insert_meeting(folder=tmp_path / "m", source="manual")
    JobQueue(conn, FakeClock()).enqueue(meeting.id, JobStage.TRANSCRIBE)

    results: list[object] = []
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def claim() -> None:
        own = sqlite3.connect(str(path), isolation_level=None, timeout=10)
        own.row_factory = sqlite3.Row
        own.execute("PRAGMA busy_timeout=10000")
        queue = JobQueue(own, FakeClock())
        barrier.wait()
        job = queue.claim_next()
        with lock:
            results.append(job)
        own.close()

    threads = [threading.Thread(target=claim) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    winners = [r for r in results if r is not None]
    assert len(results) == 8
    assert len(winners) == 1
    conn.close()


def test_stage_advances_pipeline(h: Harness) -> None:
    meeting = h.meeting()
    h.worker.stages["transcribe"] = lambda ctx: None
    h.queue.enqueue(meeting.id, JobStage.TRANSCRIBE)
    assert h.worker.run_once() is True
    stages = {job.stage: job.state for job in h.queue.for_meeting(meeting.id)}
    assert stages == {"transcribe": "done", "assemble": "pending"}


def test_transcripts_only_stops_at_the_transcript(h: Harness) -> None:
    """D63: with no AI provider the meeting ends TRANSCRIBED, with no summary job to fail."""
    h.config.set("llm.provider", "none")
    meeting = h.meeting(MeetingState.TRANSCRIBING)
    h.worker.stages["assemble"] = lambda ctx: None
    h.queue.enqueue(meeting.id, JobStage.ASSEMBLE)
    assert h.worker.run_once() is True
    assert h.dao.require_meeting(meeting.id).state == MeetingState.TRANSCRIBED
    assert {job.stage for job in h.queue.for_meeting(meeting.id)} == {"assemble"}


def test_a_sensitive_meeting_is_still_summarized_locally_with_none(h: Harness) -> None:
    h.config.set("llm.provider", "none")
    meeting = h.meeting(MeetingState.TRANSCRIBING)
    h.conn.execute("UPDATE meetings SET sensitive=1 WHERE id=?", (meeting.id,))
    h.worker.stages["assemble"] = lambda ctx: None
    h.queue.enqueue(meeting.id, JobStage.ASSEMBLE)
    assert h.worker.run_once() is True
    assert "summarize" in {job.stage for job in h.queue.for_meeting(meeting.id)}


def test_retry_backoff_grows(h: Harness) -> None:
    meeting = h.meeting()
    h.queue.enqueue(meeting.id, "flaky")
    deltas = []
    for _ in range(3):
        assert h.worker.run_once() is True
        job = h.queue.get_by_stage(meeting.id, "flaky")
        assert job is not None and job.not_before is not None
        deltas.append((parse_iso(job.not_before) - h.clock.now()).total_seconds())
        h.clock.advance(deltas[-1] + 0.1)
    assert h.stages.calls["flaky"] == 3
    for expected, actual in zip((2, 4, 8), deltas, strict=True):
        assert expected * 0.9 <= actual <= expected * 1.1, deltas
    job = h.queue.get_by_stage(meeting.id, "flaky")
    assert job is not None and job.attempts == 3
    # the fourth attempt succeeds
    assert h.worker.run_once() is True
    job = h.queue.get_by_stage(meeting.id, "flaky")
    assert job is not None and job.state == "done"


def test_permanent_after_max_attempts(h: Harness) -> None:
    meeting = h.meeting()
    h.stages.fail_times = 99
    h.queue.enqueue(meeting.id, "flaky")
    for _ in range(5):
        h.worker.run_once()
        h.clock.advance(3700)
    job = h.queue.get_by_stage(meeting.id, "flaky")
    assert job is not None
    assert job.state == "failed"
    assert job.attempts == 5
    assert job.last_error and "flaky failure" in job.last_error
    assert h.dao.require_meeting(meeting.id).state == MeetingState.FAILED
    assert h.dao.require_meeting(meeting.id).error


def test_permanent_error_fails_immediately(h: Harness) -> None:
    meeting = h.meeting()
    h.queue.enqueue(meeting.id, "boom")
    h.worker.run_once()
    job = h.queue.get_by_stage(meeting.id, "boom")
    assert job is not None and job.state == "failed" and job.attempts == 1
    assert h.stages.calls["boom"] == 1


def test_preemption_does_not_count_attempt(h: Harness) -> None:
    h.config.set("job_policy", "after_meeting")
    meeting = h.meeting()
    h.queue.enqueue(meeting.id, "preemptible")
    h.recorder.active = True
    assert h.worker.run_once() is False  # policy refuses to claim while recording
    h.recorder.active = False
    job = h.queue.claim_next()
    assert job is not None
    h.recorder.active = True
    h.worker.execute(job)
    after = h.queue.require(job.id)
    assert after.state == "pending"
    assert after.attempts == 0
    assert h.worker.stats.preempted == 1
    assert h.stages.work_done["preemptible"] == 1  # it yielded after the first unit


def test_crash_recovery_resets_running(h: Harness) -> None:
    meeting = h.meeting()
    job = h.queue.enqueue(meeting.id, "slow")
    claimed = h.queue.claim_next()
    assert claimed is not None and claimed.state == "running"
    # a new worker on the same database is a restart
    restarted = Worker(
        dao=h.dao,
        queue=h.queue,
        config=h.config,
        stages=h.stages.registry(),  # type: ignore[arg-type]
        clock=h.clock,
    )
    assert restarted is not None
    assert h.queue.require(job.id).state == "pending"


def test_idempotent_stage_skips(h: Harness) -> None:
    meeting = h.meeting()
    h.queue.enqueue(meeting.id, "idempotent")
    h.worker.run_once()
    assert h.stages.work_done["idempotent"] == 1
    h.queue.retry(meeting.id, "idempotent")
    h.worker.run_once()
    assert h.stages.calls["idempotent"] == 2
    assert h.stages.work_done["idempotent"] == 1  # output existed and was newer than its input


def test_delivery_failure_does_not_regress_state(h: Harness) -> None:
    meeting = h.meeting(MeetingState.RENDERED)
    h.worker.stages["deliver"] = h.stages.boom
    h.queue.enqueue(meeting.id, JobStage.DELIVER)
    h.worker.run_once()
    job = h.queue.get_by_stage(meeting.id, "deliver")
    assert job is not None and job.state == "failed"
    refreshed = h.dao.require_meeting(meeting.id)
    assert refreshed.state == MeetingState.RENDERED
    assert refreshed.error


def test_policy_gates_execution(tmp_path: Path) -> None:
    h = Harness(tmp_path, policy="when_idle")
    meeting = h.meeting()
    h.queue.enqueue(meeting.id, "slow")
    h.activity.busy = True
    assert h.worker.run_once() is False
    assert h.queue.get_by_stage(meeting.id, "slow").state == "pending"  # type: ignore[union-attr]
    h.activity.busy = False
    assert h.worker.run_once() is True
    assert h.queue.get_by_stage(meeting.id, "slow").state == "done"  # type: ignore[union-attr]


def test_not_before_gates_claiming(h: Harness) -> None:
    meeting = h.meeting()
    h.stages.fail_times = 1
    h.queue.enqueue(meeting.id, "flaky")
    h.worker.run_once()
    assert h.worker.run_once() is False  # still backing off
    h.clock.advance(10)
    assert h.worker.run_once() is True


def test_retry_resets_attempts(h: Harness) -> None:
    meeting = h.meeting()
    h.stages.fail_times = 99
    h.queue.enqueue(meeting.id, "flaky")
    for _ in range(5):
        h.worker.run_once()
        h.clock.advance(3700)
    job = h.queue.retry(meeting.id, "flaky")
    assert job.state == "pending" and job.attempts == 0 and job.last_error is None
