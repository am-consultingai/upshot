"""File jobs: their store, one FIFO with meeting jobs, and the worker that runs both (D86)."""

from __future__ import annotations

import json
import shutil
import sqlite3
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.asr.fake import FakeAsr
from app.audio.ingest import UnsupportedAudio
from app.db import migrate as migrations
from app.db.dao import Connection
from app.errors import Cancelled
from app.events import EventBus
from app.pipeline.activity import FakeActivity, FakeRecorderState
from app.pipeline.queue import Job
from app.pipeline.worker import Worker
from app.transcription.scheduler import Scheduler
from app.transcription.store import (
    CANCELLED,
    DONE,
    FAILED,
    PENDING,
    RUNNING,
    Transcription,
    TranscriptionStore,
)
from app.transcription.types import Options
from tests.fixtures.meetings import Harness, harness, speechish

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def store_for(h: Harness, events: EventBus | None = None) -> TranscriptionStore:
    return TranscriptionStore(h.queue.conn, h.root, h.clock, events=events)


def add(store: TranscriptionStore, name: str = "clip.wav", path: str = "/nowhere/clip.wav",
        **options: object) -> Transcription:  # fmt: skip
    return store.create(
        source_name=name,
        source_kind="path",
        source_path=path,
        options=Options(**options),  # type: ignore[arg-type]
        client="api",
    )


def write_wav(path: Path, seconds: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(speechish(seconds).tobytes())
    return path


# ----------------------------------------------------------------------- the migration


def test_migration_0010_keys_existing_meeting_jobs_by_when_they_were_created(
    tmp_path: Path,
) -> None:
    older = tmp_path / "migrations"
    older.mkdir()
    for path in migrations.migrations_dir().glob("*.sql"):
        if int(path.name[:4]) <= 9:
            shutil.copy(path, older / path.name)
    conn = sqlite3.connect(
        str(tmp_path / "index.db"), isolation_level=None, check_same_thread=False,
        factory=Connection,
    )  # fmt: skip
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    migrations.migrate(conn, older)
    conn.execute(
        "INSERT INTO meetings(id, folder, source, state, started_at, profile, created_at, "
        "updated_at) VALUES ('m1', '/x', 'manual', 'ready', 't', 'auto', 'now', 'now')"
    )
    conn.execute(
        "INSERT INTO jobs(meeting_id, stage, state, attempts, priority, created_at, updated_at) "
        "VALUES ('m1', 'transcribe', 'pending', 0, 100, '2026-10-01T09:00:00.000+03:00', 'x')"
    )

    migrations.migrate(conn)

    assert migrations.current_version(conn) == 10
    row = conn.execute("SELECT queued_at FROM jobs").fetchone()
    assert row["queued_at"] == "2026-10-01T09:00:00.000+03:00"
    conn.execute("SELECT * FROM transcriptions").fetchall()


# ----------------------------------------------------------------------- the store


def test_a_job_is_created_waiting_and_claimed_once(tmp_path: Path) -> None:
    store = store_for(harness(tmp_path))
    job = add(store, language="he")
    assert job.id.startswith("tr_") and len(job.id) == 15
    assert job.state == PENDING and job.parsed_options.language == "he"
    claimed = store.claim(job.id)
    assert claimed is not None and claimed.state == RUNNING
    assert store.claim(job.id) is None, "a second claim loses"


def test_a_job_cancelled_between_peek_and_claim_is_not_run(tmp_path: Path) -> None:
    store = store_for(harness(tmp_path))
    job = add(store)
    assert store.peek() == job
    store.cancel(job.id)  # an API thread, in between
    assert store.claim(job.id) is None
    assert store.require(job.id).state == CANCELLED


def test_a_failure_backs_off_and_a_permanent_one_does_not(tmp_path: Path) -> None:
    h = harness(tmp_path)
    store = store_for(h)
    job = store.claim(add(store).id)
    assert job is not None
    retried = store.fail(job, RuntimeError("the GPU fell over"))
    assert (retried.state, retried.attempts) == (PENDING, 1)
    assert retried.not_before is not None
    assert store.peek() is None, "backing off"
    h.clock.advance(10)
    assert store.peek() is not None

    again = store.claim(job.id)
    assert again is not None
    failed = store.fail(again, UnsupportedAudio("clip.mp4 has no audio"), permanent=True)
    assert failed.state == FAILED and "has no audio" in (failed.last_error or "")


def test_preemption_counts_no_attempt_and_keeps_the_place(tmp_path: Path) -> None:
    h = harness(tmp_path)
    store = store_for(h)
    job = add(store)
    claimed = store.claim(job.id)
    assert claimed is not None
    h.clock.advance(60)
    released = store.release(claimed)
    assert (released.state, released.attempts, released.queued_at) == (PENDING, 0, job.queued_at)


def test_cancel_of_a_running_job_stops_it_at_the_next_check(tmp_path: Path) -> None:
    store = store_for(harness(tmp_path))
    job = add(store)
    store.claim(job.id)
    store.stop_check(job.id)  # nothing asked yet
    assert store.cancel(job.id).state == RUNNING, "the worker stops it, not the API"
    with pytest.raises(Cancelled):
        store.stop_check(job.id)
    assert store.mark_cancelled(job.id).state == CANCELLED


def test_delete_removes_the_row_and_the_folder_or_hands_off(tmp_path: Path) -> None:
    store = store_for(harness(tmp_path))
    waiting, running = add(store), add(store)
    for job in (waiting, running):
        (store.folder(job.id) / "input").mkdir(parents=True)
    store.claim(running.id)

    assert store.delete(waiting.id) is True
    assert store.get(waiting.id) is None and not store.folder(waiting.id).exists()

    assert store.delete(running.id) is False, "a running job is a hand-off"
    assert store.get(running.id) is not None
    with pytest.raises(Cancelled):
        store.stop_check(running.id)
    assert store.finish_delete(running.id) is True
    assert store.get(running.id) is None and not store.folder(running.id).exists()


def test_a_removal_never_leaves_the_transcriptions_folder(tmp_path: Path) -> None:
    store = store_for(harness(tmp_path))
    assert not store.inside_root(store.root / ".." / "m1")
    assert not store.inside_root(store.root)
    assert store.inside_root(store.folder("tr_abc"))


def test_retry_puts_it_at_the_back_of_the_queue(tmp_path: Path) -> None:
    h = harness(tmp_path)
    store = store_for(h)
    job = add(store)
    store.cancel(job.id)
    h.clock.advance(120)
    retried = store.retry(job.id)
    assert retried.state == PENDING and retried.queued_at > job.queued_at
    with pytest.raises(ValueError, match="only a failed or cancelled"):
        store.retry(job.id)


def test_crash_recovery_resets_running_and_drops_partial_uploads(tmp_path: Path) -> None:
    store = store_for(harness(tmp_path))
    job = add(store)
    store.claim(job.id)
    store.incoming().mkdir(parents=True)
    (store.incoming() / "half-an-upload").write_bytes(b"x" * 10)
    assert store.reset_running() == 1
    assert store.require(job.id).state == PENDING
    assert list(store.incoming().iterdir()) == []


def test_recent_is_newest_first_and_pages(tmp_path: Path) -> None:
    h = harness(tmp_path)
    store = store_for(h)
    ids = []
    for _ in range(3):
        ids.append(add(store).id)
        h.clock.advance(1)
    listed = store.recent()
    assert [job.id for job in listed] == ids[::-1]
    assert [job.id for job in store.recent(before=listed[0].created_at)] == ids[1::-1]
    assert store.recent(state=DONE) == []


def test_progress_events_are_at_most_one_a_second(tmp_path: Path) -> None:
    h = harness(tmp_path)
    events = EventBus()
    store = store_for(h, events)
    job = add(store)
    store.claim(job.id)
    before = len(list(events.replay()))
    for _ in range(10):
        store.progress(job.id, "transcribe", 0.5)
    h.clock.advance(1.0)
    store.progress(job.id, "transcribe", 0.6)
    sent = [e for e in events.replay() if e.type == "transcription"][before:]
    assert len(sent) == 2
    assert store.require(job.id).progress == pytest.approx(0.6)


# ----------------------------------------------------------------------- one FIFO (R5)


def test_files_and_meetings_run_in_the_order_they_were_queued(tmp_path: Path) -> None:
    h = harness(tmp_path)
    store = store_for(h)
    scheduler = Scheduler(h.queue, store)
    first = add(store)
    h.clock.advance(1)
    meeting = h.meeting()
    h.queue.enqueue(meeting.id, "transcribe")
    h.clock.advance(1)
    second = add(store)

    order = []
    while (claimed := scheduler.claim_next_any()) is not None:
        order.append(claimed.id if isinstance(claimed, Transcription) else claimed.meeting_id)
        if isinstance(claimed, Job):
            h.queue.complete(claimed)
        else:
            store.complete(claimed.id, duration_s=1, language=None, language_conf=None, model={})
    assert order == [first.id, meeting.id, second.id]


def test_a_meetings_later_stages_keep_its_place(tmp_path: Path) -> None:
    h = harness(tmp_path)
    store = store_for(h)
    scheduler = Scheduler(h.queue, store)
    meeting = h.meeting()
    h.queue.enqueue(meeting.id, "transcribe")
    h.clock.advance(1)
    file = add(store)
    transcribing = scheduler.claim_next_any()
    assert isinstance(transcribing, Job)
    h.clock.advance(600)  # the meeting took ten minutes to transcribe
    h.queue.complete(transcribing)
    h.queue.enqueue_next_stage(transcribing)

    following = scheduler.claim_next_any()
    assert isinstance(following, Job) and following.meeting_id == meeting.id
    assert following.stage != "transcribe", "its summary, before the file queued after it"
    assert following.fifo_key == transcribing.fifo_key
    assert store.require(file.id).state == PENDING


def test_an_old_meetings_rerun_queues_behind_waiting_files(tmp_path: Path) -> None:
    """Keyed by its earliest job row, a month-old meeting's re-run would have jumped
    ahead of every waiting file (revision 4)."""
    h = harness(tmp_path)
    store = store_for(h)
    scheduler = Scheduler(h.queue, store)
    meeting = h.meeting()
    job = h.queue.enqueue(meeting.id, "transcribe")
    h.queue.complete(h.queue.claim(job.id))  # type: ignore[arg-type]
    h.clock.advance(30 * 86400)
    file = add(store)
    h.clock.advance(1)
    h.queue.retry(meeting.id, "transcribe")  # "Transcribe again"

    first = scheduler.claim_next_any()
    assert isinstance(first, Transcription) and first.id == file.id


def test_a_tie_goes_to_the_meeting(tmp_path: Path) -> None:
    h = harness(tmp_path)
    store = store_for(h)
    file = add(store)
    meeting = h.meeting()
    h.queue.enqueue(meeting.id, "transcribe", queued_at=file.queued_at)
    claimed = Scheduler(h.queue, store).claim_next_any()
    assert isinstance(claimed, Job)


def test_keys_are_compared_as_times_not_strings(tmp_path: Path) -> None:
    """Across a DST change the offset in the string changes: 01:30+03:00 is earlier than
    01:10+02:00, though it sorts after it as text."""
    h = harness(tmp_path)
    store = store_for(h)
    file = add(store)
    store.conn.execute(
        "UPDATE transcriptions SET queued_at='2026-10-25T01:10:00.000+02:00' WHERE id=?",
        (file.id,),
    )
    meeting = h.meeting()
    h.queue.enqueue(meeting.id, "transcribe", queued_at="2026-10-25T01:30:00.000+03:00")
    claimed = Scheduler(h.queue, store).claim_next_any()
    assert isinstance(claimed, Job), "the meeting was queued 40 minutes before the file"


def test_a_backing_off_head_lets_the_next_one_run(tmp_path: Path) -> None:
    h = harness(tmp_path)
    store = store_for(h)
    early = store.claim(add(store).id)
    assert early is not None
    store.fail(early, RuntimeError("try later"))
    h.clock.advance(0.5)
    meeting = h.meeting()
    h.queue.enqueue(meeting.id, "transcribe")
    claimed = Scheduler(h.queue, store).claim_next_any()
    assert isinstance(claimed, Job), "the file is backing off; the meeting is due"


def test_positions_count_both_kinds(tmp_path: Path) -> None:
    h = harness(tmp_path)
    store = store_for(h)
    scheduler = Scheduler(h.queue, store)
    first = add(store)
    h.clock.advance(1)
    meeting = h.meeting()
    meeting_job = h.queue.enqueue(meeting.id, "transcribe")
    h.clock.advance(1)
    last = add(store)
    assert scheduler.position(store.require(first.id)) == 1
    assert scheduler.position(store.require(last.id)) == 3
    assert scheduler.files_ahead(meeting_job) == 1
    assert scheduler.depth() == 3
    store.cancel(first.id)
    assert scheduler.position(store.require(first.id)) is None


# ----------------------------------------------------------------------- the worker


class Services:
    def __init__(self) -> None:
        self.asr = FakeAsr()
        self.classifier = None
        self.events = None


def worker_for(h: Harness, store: TranscriptionStore, *, recording: bool = False,
               policy: str = "asap", stages=None) -> Worker:  # type: ignore[no-untyped-def]  # fmt: skip
    h.config.set("job_policy", policy)
    h.config.set("asr.backend", "fake")
    return Worker(
        dao=h.dao,
        queue=h.queue,
        config=h.config,
        stages=stages or {},
        clock=h.clock,
        recorder=FakeRecorderState(recording),
        activity=FakeActivity(False),
        services=Services(),
        transcriptions=store,
        scheduler=Scheduler(h.queue, store),
    )


@needs_ffmpeg
def test_the_worker_transcribes_a_file_and_drops_the_upload(tmp_path: Path) -> None:
    h = harness(tmp_path)
    store = store_for(h)
    worker = worker_for(h, store)
    job = store.create(source_name="שיחה.wav", source_kind="upload", source_path="",
                       options=Options(), client="ui")  # fmt: skip
    source = write_wav(store.folder(job.id) / "input" / "source.wav", 20)
    store.conn.execute("UPDATE transcriptions SET source_path=? WHERE id=?", (str(source), job.id))

    assert worker.run_once() is True

    done = store.require(job.id)
    assert done.state == DONE and done.progress == 1.0
    assert done.duration_s == pytest.approx(20, abs=0.1) and done.language == "he"
    result = json.loads((store.folder(job.id) / "result.json").read_text(encoding="utf-8"))
    assert result["source_name"] == "שיחה.wav" and result["segments"]
    assert not (store.folder(job.id) / "input").exists(), "keep_input is off by default"
    assert not (store.folder(job.id) / "audio.wav").exists()
    assert h.dao.list_meetings(limit=10, include_hidden=True) == [], "no meeting, ever (R4)"


@needs_ffmpeg
def test_a_path_source_is_never_removed(tmp_path: Path) -> None:
    h = harness(tmp_path)
    store = store_for(h)
    source = write_wav(tmp_path / "mine" / "clip.wav", 10)
    add(store, path=str(source))
    worker_for(h, store).run_once()
    assert source.exists()


@needs_ffmpeg
def test_a_recording_stops_a_file_job_between_phases_and_it_resumes(tmp_path: Path) -> None:
    h = harness(tmp_path)
    store = store_for(h)
    worker = worker_for(h, store, policy="after_meeting")
    job = add(store, path=str(write_wav(tmp_path / "clip.wav", 20)))
    recorder = worker.recorder
    assert isinstance(recorder, FakeRecorderState)
    seen: list[str] = []
    original = store.progress

    def progress(tid: str, phase: str, value: float) -> None:
        seen.append(phase)
        if phase == "language":
            recorder.active = True  # a meeting starts while the language is decided
        original(tid, phase, value)

    store.progress = progress  # type: ignore[method-assign]
    assert worker.run_once() is True
    released = store.require(job.id)
    assert released.state == PENDING and released.attempts == 0
    assert "transcribe" not in seen, "stopped at the boundary, before ASR began"
    assert worker.waiting_reason() == "recording"
    assert worker.run_once() is False, "it waits while the meeting records"

    recorder.active = False
    store.progress = original  # type: ignore[method-assign]
    assert worker.run_once() is True
    assert store.require(job.id).state == DONE


@needs_ffmpeg
def test_a_cancel_mid_job_leaves_it_cancelled(tmp_path: Path) -> None:
    h = harness(tmp_path)
    store = store_for(h)
    job = add(store, path=str(write_wav(tmp_path / "clip.wav", 60)))
    original = store.progress

    def progress(tid: str, phase: str, value: float) -> None:
        if phase == "transcribe":
            store.cancel(tid)
        original(tid, phase, value)

    store.progress = progress  # type: ignore[method-assign]
    worker_for(h, store).run_once()
    assert store.require(job.id).state == CANCELLED


@needs_ffmpeg
def test_a_delete_mid_job_removes_it_when_the_engine_lets_go(tmp_path: Path) -> None:
    h = harness(tmp_path)
    store = store_for(h)
    job = add(store, path=str(write_wav(tmp_path / "clip.wav", 60)))
    original = store.progress

    def progress(tid: str, phase: str, value: float) -> None:
        if phase == "transcribe":
            assert store.delete(tid) is False
        original(tid, phase, value)

    store.progress = progress  # type: ignore[method-assign]
    worker_for(h, store).run_once()
    assert store.get(job.id) is None
    assert not store.folder(job.id).exists()


@needs_ffmpeg
def test_an_undecodable_file_fails_at_once(tmp_path: Path) -> None:
    h = harness(tmp_path)
    store = store_for(h)
    bad = tmp_path / "notes.mp3"
    bad.write_text("not audio at all", encoding="utf-8")
    job = add(store, path=str(bad))
    worker_for(h, store).run_once()
    failed = store.require(job.id)
    assert failed.state == FAILED and failed.attempts == 1, "permanent: no retries"


def test_a_missing_path_fails_at_once(tmp_path: Path) -> None:
    h = harness(tmp_path)
    store = store_for(h)
    job = add(store, path=str(tmp_path / "gone.wav"))
    worker_for(h, store).run_once()
    assert store.require(job.id).state == FAILED


@needs_ffmpeg
def test_the_worker_alternates_kinds_by_key(tmp_path: Path) -> None:
    h = harness(tmp_path)
    store = store_for(h)
    ran: list[str] = []
    worker = worker_for(h, store, stages={"noop": lambda ctx: ran.append(ctx.meeting.id)})
    file = add(store, path=str(write_wav(tmp_path / "clip.wav", 5)))
    h.clock.advance(1)
    meeting = h.meeting()
    h.queue.enqueue(meeting.id, "noop")
    assert worker.run_once() is True
    assert store.require(file.id).state == DONE and ran == []
    assert worker.run_once() is True
    assert ran == [meeting.id]


def test_waiting_reasons_follow_the_policy(tmp_path: Path) -> None:
    h = harness(tmp_path)
    store = store_for(h)
    worker = worker_for(h, store, policy="after_meeting", recording=True)
    assert worker.waiting_reason() == "recording"
    worker = worker_for(h, store, policy="when_idle")
    worker.activity = FakeActivity(True)
    assert worker.waiting_reason() == "policy:when_idle"
    worker = worker_for(h, store, policy="scheduled")
    h.clock.set(datetime(2026, 10, 5, 14, 0, tzinfo=timezone(timedelta(hours=3))))
    assert worker.waiting_reason() == "policy:scheduled"
    worker = worker_for(h, store, policy="asap", recording=True)
    assert worker.waiting_reason() == "queue"


def test_a_restart_recovers_a_running_file_job(tmp_path: Path) -> None:
    h = harness(tmp_path)
    store = store_for(h)
    job = add(store)
    store.claim(job.id)
    worker_for(h, store)
    assert store.require(job.id).state == PENDING
