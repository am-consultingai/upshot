"""File jobs: their store, one FIFO with meeting jobs, and the worker that runs both (D86)."""

from __future__ import annotations

import json
import shutil
import sqlite3
import wave
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.asr.fake import FakeAsr
from app.audio.ingest import FfmpegBroken, FfmpegFailed, UnsupportedAudio
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

    assert migrations.current_version(conn) == migrations.discover()[-1].version
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


@pytest.mark.parametrize("ends", ["retry", "preempted"])
def test_a_run_sent_back_to_wait_leaves_nothing_for_the_next_one(tmp_path: Path, ends: str) -> None:
    """A retry or a preemption ends one run without finishing the job. What that run kept
    goes with it: a stale last-sent time held back the next run's first progress event."""
    h = harness(tmp_path)
    events = EventBus()
    store = store_for(h, events)
    job = add(store)
    claimed = store.claim(job.id)
    assert claimed is not None
    store.progress(job.id, "transcribe", 0.3)
    if ends == "retry":
        store.fail(claimed, RuntimeError("the GPU fell over"))
        h.clock.advance(10)
    else:
        store.release(claimed)
    assert job.id not in store._last_event
    assert job.id not in store._started
    assert job.id not in store._time_left

    assert store.claim(job.id) is not None
    before = len(list(events.replay()))
    store.progress(job.id, "transcribe", 0.1)  # less than a second after the last one
    sent = [e for e in events.replay() if e.type == "transcription"][before:]
    assert len(sent) == 1, "the new run's first progress is sent at once"


def test_a_stale_read_after_the_run_ends_keeps_no_time_left(tmp_path: Path) -> None:
    """The API reads a row, the worker finishes the job, then the API asks for its time
    left with the row it read: the estimate is answered but not kept, since nothing
    would ever remove it again."""
    h = harness(tmp_path)
    store = store_for(h)
    job = add(store)
    store.claim(job.id)
    h.clock.advance(30)
    store.progress(job.id, "transcribe", 0.5)
    stale = store.require(job.id)
    assert stale.state == RUNNING
    assert store.eta_s(stale) is not None and job.id in store._time_left
    store.complete(job.id, duration_s=1.0, language="en", language_conf=1.0, model={})
    assert job.id not in store._time_left

    store.eta_s(stale)
    assert job.id not in store._time_left


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


def _ffmpeg_cannot_start(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.transcription import engine

    def cannot_start(source: Path, *args: object, **kwargs: object) -> Path:
        raise FfmpegFailed(f"ffmpeg could not run to read {source.name} (exit code 0xc0000017)")

    monkeypatch.setattr(engine, "to_wav", cannot_start)


def test_ffmpeg_that_did_not_run_waits_without_using_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ffmpeg that could not start says nothing about the file, and it lasted days on the
    machine that reported it: wait, count no attempt, and say why while waiting. A job
    created long before ffmpeg broke waits like any other."""
    from app.pipeline import worker as worker_module

    _ffmpeg_cannot_start(monkeypatch)
    h = harness(tmp_path)
    store = store_for(h)
    job = add(store, path=str(write_wav(tmp_path / "clip.wav", 2)))
    h.clock.advance(3 * 24 * 3600)
    worker = worker_for(h, store)
    for _ in range(7):  # more than MAX_ATTEMPTS
        assert worker.run_once() is True
        held = store.require(job.id)
        assert held.state == PENDING and held.attempts == 0
        assert "0xc0000017" in (held.last_error or "")
        assert worker.run_once() is False, "it waits before trying again"
        h.clock.advance(worker_module.FFMPEG_DEFER_S + 1)


def test_a_cancel_during_a_failed_ffmpeg_start_is_honoured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.transcription import engine

    h = harness(tmp_path)
    store = store_for(h)
    job = add(store, path=str(write_wav(tmp_path / "clip.wav", 2)))

    def cancelled_then_fails(source: Path, *args: object, **kwargs: object) -> Path:
        store.cancel(job.id)  # the user presses Cancel while ffmpeg is starting
        raise FfmpegFailed("ffmpeg could not run (exit code 0xc0000017)")

    monkeypatch.setattr(engine, "to_wav", cancelled_then_fails)
    worker_for(h, store).run_once()
    assert store.require(job.id).state == CANCELLED


def test_defer_honours_a_cancel_asked_for_while_the_job_ran(tmp_path: Path) -> None:
    """Under the lock cancel takes: a cancel landing just before the deferral wins."""
    h = harness(tmp_path)
    store = store_for(h)
    job = add(store)
    running = store.claim(job.id)
    assert running is not None and running.id == job.id
    store.cancel(job.id)
    until = datetime.fromisoformat("2030-01-01T00:00:00+00:00")
    assert store.defer(running, "FfmpegFailed: no", until=until).state == CANCELLED


def test_an_ffmpeg_that_can_never_run_fails_the_job_at_once_without_a_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.diagnostics import reporter
    from app.transcription import engine

    def broken(source: Path, *args: object, **kwargs: object) -> Path:
        raise FfmpegBroken("ffmpeg cannot run on this computer (exit code 0xc0000135)")

    reported: list[BaseException] = []
    monkeypatch.setattr(engine, "to_wav", broken)
    monkeypatch.setattr(reporter, "report", lambda exc, **kwargs: reported.append(exc))
    h = harness(tmp_path)
    store = store_for(h)
    job = add(store, path=str(write_wav(tmp_path / "clip.wav", 2)))
    worker_for(h, store).run_once()
    failed = store.require(job.id)
    assert failed.state == FAILED and "0xc0000135" in (failed.last_error or "")
    assert reported == []


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


def test_a_file_jobs_time_left_holds_while_its_progress_stands_still(tmp_path: Path) -> None:
    # D94: polled every 2 s while a long chunk is decoded, the time left must not climb.
    h = harness(tmp_path)
    store = store_for(h)
    job = store.claim(add(store).id)
    assert job is not None
    h.clock.advance(21.7)
    store.progress(job.id, "transcribe", 0.10)
    first = store.eta_s(store.require(job.id))
    assert first == 195
    for _ in range(25):
        h.clock.advance(2)
        assert store.eta_s(store.require(job.id)) == first
    store.progress(job.id, "transcribe", 0.38)
    assert store.eta_s(store.require(job.id)) == 117


def test_a_job_whose_folder_cannot_be_made_is_failed_not_left_running(tmp_path: Path) -> None:
    """The PR #1 review: an error before the engine started (the folder's mkdir) escaped
    to the worker's catch-all and left the job running until the next restart."""
    h = harness(tmp_path)
    store = store_for(h)
    job = add(store, path=str(tmp_path / "clip.wav"))
    blocked = store.folder(job.id)
    blocked.parent.mkdir(parents=True, exist_ok=True)
    blocked.write_text("a file where the job's folder goes", encoding="utf-8")

    worker_for(h, store).run_once()

    after = store.require(job.id)
    assert after.state != RUNNING
    assert after.attempts == 1 and "FileExistsError" in (after.last_error or "")


# -- a cancel asked for in one run is not the next run's (z8tj1hwm0d) ---------


def test_a_retry_after_a_cancelled_run_that_failed_runs(tmp_path: Path) -> None:
    h = harness(tmp_path)
    store = store_for(h)
    job = add(store)
    running = store.claim(job.id)
    assert running is not None
    store.cancel(job.id)  # asked for while it ran ...
    assert store.fail(running, "UnsupportedAudio: no", permanent=True).state == CANCELLED
    store.retry(job.id)  # ... and pressed Retry later
    again = store.claim(job.id)
    assert again is not None
    assert store.stopping(job.id) is None, "the old cancel does not stop the new run"


def test_a_cancel_that_came_too_late_is_forgotten_when_the_job_is_done(tmp_path: Path) -> None:
    h = harness(tmp_path)
    store = store_for(h)
    job = add(store)
    assert store.claim(job.id) is not None
    store.cancel(job.id)  # after the run's last check
    done = store.complete(job.id, duration_s=1.0, language="he", language_conf=0.9, model={})
    assert done.state == DONE and store.stopping(job.id) is None


def test_ending_a_cancelled_run_keeps_a_delete_asked_for_meanwhile(tmp_path: Path) -> None:
    h = harness(tmp_path)
    store = store_for(h)
    job = add(store)
    assert store.claim(job.id) is not None
    store.cancel(job.id)
    assert store.delete(job.id) is False  # handed to the worker
    store.end_cancelled(job.id)
    assert store.stopping(job.id) == "delete", "finish_delete still removes it"
    assert store.require(job.id).state == RUNNING


def test_a_run_that_fails_after_a_cancel_is_cancelled_not_requeued(tmp_path: Path) -> None:
    h = harness(tmp_path)
    store = store_for(h)
    job = add(store)
    running = store.claim(job.id)
    assert running is not None
    store.cancel(job.id)
    assert store.fail(running, "RuntimeError: boom").state == CANCELLED
    assert store.stopping(job.id) is None


def test_a_preempted_run_after_a_cancel_is_cancelled(tmp_path: Path) -> None:
    h = harness(tmp_path)
    store = store_for(h)
    job = add(store)
    running = store.claim(job.id)
    assert running is not None
    store.cancel(job.id)
    assert store.release(running).state == CANCELLED


def test_a_delete_asked_for_survives_a_failure(tmp_path: Path) -> None:
    """Only a cancel is forgotten: the worker still owes a delete its removal."""
    h = harness(tmp_path)
    store = store_for(h)
    job = add(store)
    running = store.claim(job.id)
    assert running is not None
    assert store.delete(job.id) is False
    store.fail(running, "boom", permanent=True)
    assert store.stopping(job.id) == "delete"


def test_a_delete_asked_for_as_the_job_is_marked_done_is_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After the run's last chance to stop: the worker was handed it, so it does it."""
    from app.transcription import engine

    def copy(source: Path, target: Path, **kwargs: object) -> Path:  # no ffmpeg needed
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        return target

    monkeypatch.setattr(engine, "to_wav", copy)
    h = harness(tmp_path)
    store = store_for(h)
    job = add(store, path=str(write_wav(tmp_path / "clip.wav", 2)))
    complete = store.complete

    def deleted_just_before(transcription_id: str, **kwargs: object):  # type: ignore[no-untyped-def]
        assert store.delete(transcription_id) is False, "running: handed to the worker"
        return complete(transcription_id, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(store, "complete", deleted_just_before)
    worker = worker_for(h, store)
    worker.run_once()
    assert store.get(job.id) is None
    assert store.stopping(job.id) is None
    assert worker.stats.completed == 0, "deleted as it finished: not counted as done"


# -- a delete whose files are still in use (z8tj1hwqq1) -----------------------


def held_once(store: TranscriptionStore, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The folder's removal fails once, as with a file an antivirus still holds."""
    tried: list[str] = []
    remove = store.remove_folder

    def held(transcription_id: str) -> None:
        tried.append(transcription_id)
        if len(tried) == 1:
            raise PermissionError(13, "The process cannot access the file")
        remove(transcription_id)

    monkeypatch.setattr(store, "remove_folder", held)
    return tried


def test_a_running_job_deleted_with_a_held_file_is_gone_and_removed_later(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness(tmp_path)
    store = store_for(h)
    job = add(store)
    running = store.claim(job.id)
    assert running is not None
    assert store.delete(job.id) is False  # handed to the worker
    tried = held_once(store, monkeypatch)
    assert store.finish_delete(job.id) is True, "gone for the user"
    assert tried == [job.id]
    row = store.get(job.id)
    assert row is not None and row.state == CANCELLED, "no longer running"
    assert store.visible(job.id) is None and store.recent() == []
    assert not store.busy(), "an update is not held back by it"
    assert store.stopping(job.id) is None
    assert store.finish_pending_deletes() == 1
    assert store.get(job.id) is None


def test_a_waiting_job_deleted_with_a_held_file_never_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness(tmp_path)
    store = store_for(h)
    job = add(store)
    held_once(store, monkeypatch)
    assert store.delete(job.id) is False
    assert store.claim(job.id) is None and store.runnable() == []
    with pytest.raises(KeyError):
        store.retry(job.id)
    assert store.finish_pending_deletes() == 1


def test_a_crash_while_a_delete_was_stopping_the_job_does_not_bring_it_back(
    tmp_path: Path,
) -> None:
    h = harness(tmp_path)
    store = store_for(h)
    job = add(store)
    assert store.claim(job.id) is not None
    store.delete(job.id)  # the worker never got to stop it: the app died
    restarted = store_for(h)
    restarted.reset_running()
    row = restarted.get(job.id)
    assert row is not None and row.state == CANCELLED, "not waiting to run again"
    assert restarted.visible(job.id) is None
    assert restarted.finish_pending_deletes() == 1 and restarted.get(job.id) is None


def test_a_file_still_held_waits_for_the_next_try(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness(tmp_path)
    store = store_for(h)
    job = add(store)

    def always_held(transcription_id: str) -> None:
        raise PermissionError(13, "The process cannot access the file")

    monkeypatch.setattr(store, "remove_folder", always_held)
    store.delete(job.id)
    assert store.finish_pending_deletes() == 0
    assert store.get(job.id) is not None and store.visible(job.id) is None


def test_the_worker_tries_again_once_a_minute(tmp_path: Path) -> None:
    from app.pipeline.worker import FINISH_DELETES_S

    h = harness(tmp_path)
    store = store_for(h)
    worker = worker_for(h, store)
    calls: list[int] = []
    store.finish_pending_deletes = lambda: calls.append(1) or 0  # type: ignore[method-assign]
    worker.maybe_finish_deletes()
    worker.maybe_finish_deletes()
    assert calls == [1], "the first call runs, the next waits"
    h.clock.advance(FINISH_DELETES_S)
    worker.maybe_finish_deletes()
    assert calls == [1, 1]


def test_a_file_removed_by_someone_else_mid_removal_is_fine(tmp_path: Path) -> None:
    from app.transcription.store import _gone_is_fine

    _gone_is_fine(None, str(tmp_path / "x"), FileNotFoundError(2, "gone"))
    with pytest.raises(PermissionError):
        _gone_is_fine(None, str(tmp_path / "x"), PermissionError(13, "held"))


def test_a_finished_uploads_input_goes_unless_the_job_was_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness(tmp_path)
    store = store_for(h)
    job = add(store)
    folder = store.folder(job.id) / "input"
    folder.mkdir(parents=True)
    (folder / "clip.wav").write_bytes(b"x")
    store.remove_input(job.id)
    assert not folder.exists(), "a finished upload's copy goes"
    store.remove_input("tr_nosuchjob00")  # deleted meanwhile: nothing to do

    other = add(store)
    kept = store.folder(other.id) / "input"
    kept.mkdir(parents=True)
    (kept / "clip.wav").write_bytes(b"x")
    held_once(store, monkeypatch)
    store.delete(other.id)  # hidden, its folder still there
    store.remove_input(other.id)
    assert kept.exists(), "a deleted job's folder is the delete's to remove, whole"


def test_a_delete_with_a_held_file_as_the_job_finishes_keeps_it_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker's own removal fails: the job must not come back as done."""
    from app.transcription import engine

    def copy(source: Path, target: Path, **kwargs: object) -> Path:  # no ffmpeg needed
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        return target

    monkeypatch.setattr(engine, "to_wav", copy)
    events = EventBus()
    h = harness(tmp_path)
    store = store_for(h, events)
    job = add(store, path=str(write_wav(tmp_path / "clip.wav", 2)))
    finish = store.finish_delete
    pressed: list[bool] = []

    def deleted_after_the_last_check(transcription_id: str) -> bool:
        if not pressed:  # the finally's call; the worker asks again on its way out
            pressed.append(True)
            assert store.delete(transcription_id) is False, "running: handed to the worker"
            held_once(store, monkeypatch)
        return finish(transcription_id)

    monkeypatch.setattr(store, "finish_delete", deleted_after_the_last_check)
    worker = worker_for(h, store)
    worker.run_once()
    row = store.get(job.id)
    assert row is not None and row.state == CANCELLED and store.visible(job.id) is None
    assert worker.stats.completed == 0
    states = [e.payload.get("state") for e in events.history if e.type == "transcription"]
    assert "done" not in states and states[-1] == "deleted"
    assert store.finish_pending_deletes() == 1 and store.get(job.id) is None


def test_completing_a_deleted_job_leaves_it_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness(tmp_path)
    store = store_for(h)
    job = add(store)
    held_once(store, monkeypatch)
    store.delete(job.id)
    row = store.complete(job.id, duration_s=1.0, language="he", language_conf=0.9, model={})
    assert row.state == CANCELLED and store.visible(job.id) is None


def test_a_hidden_job_is_not_retentions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    h = harness(tmp_path)
    store = store_for(h)
    job = add(store)
    held_once(store, monkeypatch)
    store.delete(job.id)
    far = datetime(2099, 1, 1, tzinfo=UTC)
    assert store.expired(0, far) == []


def test_a_hidden_job_left_waiting_by_a_crash_ends_cancelled(tmp_path: Path) -> None:
    """A failure re-queued it just before its removal, then the app died."""
    h = harness(tmp_path)
    store = store_for(h)
    job = add(store)
    store.conn.execute(
        "UPDATE transcriptions SET delete_pending=1 WHERE id=?", (job.id,)
    )  # pending, and deleted
    assert not store.busy() and store.depth() == 0 and store.pending() == []
    restarted = store_for(h)
    restarted.reset_running()
    row = restarted.get(job.id)
    assert row is not None and row.state == CANCELLED


def test_a_handed_off_delete_is_said_at_once_and_progress_goes_quiet(tmp_path: Path) -> None:
    events = EventBus()
    h = harness(tmp_path)
    store = store_for(h, events)
    job = add(store)
    assert store.claim(job.id) is not None
    store.delete(job.id)
    before = len(events.history)
    h.clock.advance(5)
    store.progress(job.id, "transcribe", 0.5)
    states = [e.payload.get("state") for e in events.history if e.type == "transcription"]
    assert states[-1] == "deleted" and len(events.history) == before


def test_a_delete_pressed_between_the_runs_end_and_its_completion_is_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After the finally's finish_delete, before the job is marked done."""
    from app.transcription import engine

    def copy(source: Path, target: Path, **kwargs: object) -> Path:  # no ffmpeg needed
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        return target

    monkeypatch.setattr(engine, "to_wav", copy)
    h = harness(tmp_path)
    store = store_for(h)
    job = add(store, path=str(write_wav(tmp_path / "clip.wav", 2)))
    visible = store.visible
    pressed: list[bool] = []

    def delete_first(transcription_id: str):  # type: ignore[no-untyped-def]
        if not pressed:
            pressed.append(store.delete(transcription_id))
        return visible(transcription_id)

    monkeypatch.setattr(store, "visible", delete_first)
    worker = worker_for(h, store)
    worker.run_once()
    assert pressed == [False], "it was running: handed to the worker"
    assert store.get(job.id) is None, "the worker did it"
    assert not store.busy() and worker.stats.completed == 0


def test_a_job_being_deleted_is_not_in_the_queue_list(tmp_path: Path) -> None:
    h = harness(tmp_path)
    store = store_for(h)
    job = add(store)
    assert store.claim(job.id) is not None
    assert store.running() is not None
    store.delete(job.id)  # the run has not stopped yet
    assert store.running() is None


def test_no_state_is_published_after_a_handed_off_delete(tmp_path: Path) -> None:
    events = EventBus()
    h = harness(tmp_path)
    store = store_for(h, events)
    job = add(store)
    running = store.claim(job.id)
    assert running is not None
    store.delete(job.id)
    store.fail(running, "RuntimeError: boom")  # the run ended badly before its removal
    states = [e.payload.get("state") for e in events.history if e.type == "transcription"]
    assert states[-1] == "deleted"
