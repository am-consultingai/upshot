"""Percent done and time left, from the transcribe stage to the page and the queue list."""

from __future__ import annotations

import logging
import shutil
import wave
from pathlib import Path
from typing import Any

import pytest

from app.asr.backend import Word
from app.asr.fake import FakeAsr
from app.pipeline.progress import meeting_progress
from app.pipeline.stages import transcribe
from app.pipeline.states import JobStage, MeetingState
from app.transcription.types import Options
from tests.fixtures.api import ApiHarness, build_harness
from tests.fixtures.meetings import harness, speechish, write_chunks


class Services:
    def __init__(self, asr: FakeAsr) -> None:
        self.asr = asr
        self.classifier = None


# ------------------------------------------------------------------ the stage


def test_the_transcribe_stage_reports_every_phase_in_order_up_to_the_whole(
    tmp_path: Path,
) -> None:
    h = harness(tmp_path, asr__backend="fake", audio__vad="energy", asr__diarization="fake")
    meeting = h.meeting()
    write_chunks(meeting.path, seconds=150)
    backend = FakeAsr(language="en")
    ctx = h.context(meeting, services=Services(backend))
    reported: list[tuple[str, float]] = []
    ctx.report = lambda phase, fraction: reported.append((phase, fraction))

    transcribe.run(ctx)

    overall = [meeting_progress(phase, fraction) for phase, fraction in reported]
    assert overall == sorted(overall), "never backwards"
    assert overall[-1] == 1.0
    phases = [phase for phase, _ in reported]
    seen = sorted(set(phases), key=phases.index)
    assert seen == ["prepare", "language", "transcribe", "diarize"]
    # Inside the long phases, not only at their edges: per segment, per diarized turn.
    assert len({f for p, f in reported if p == "transcribe"}) > 4
    assert len({f for p, f in reported if p == "diarize"}) > 4
    assert backend.on_segment is None and backend.stop_check is None, "hooks let go"


def test_without_diarization_the_stage_still_reaches_the_whole(tmp_path: Path) -> None:
    h = harness(tmp_path, asr__backend="fake", audio__vad="energy")
    meeting = h.meeting()
    write_chunks(meeting.path, seconds=60)
    ctx = h.context(meeting, services=Services(FakeAsr(language="en")))
    reported: list[tuple[str, float]] = []
    ctx.report = lambda phase, fraction: reported.append((phase, fraction))
    transcribe.run(ctx)
    assert meeting_progress(*reported[-1]) == 1.0


# ------------------------------------------------------------------ through the worker


class SlowAsr(FakeAsr):
    """Each segment takes two seconds of the fake clock, so the run has a pace."""

    def __init__(self, h: ApiHarness) -> None:
        super().__init__(language="en")
        self.h = h

    def _words(self, text: str, start: float, end: float) -> tuple[Word, ...]:  # type: ignore[override]
        self.h.clock.advance(2.0)
        return FakeAsr._words(text, start, end)


@pytest.fixture
def api(tmp_path: Path, app_home: Path) -> ApiHarness:
    return build_harness(tmp_path, asr__diarization="fake")


def recorded(api: ApiHarness, name: str = "m1", seconds: float = 120.0) -> str:
    svc = api.services
    meeting = svc.dao.insert_meeting(
        folder=svc.config.data_root / name, source="manual", profile="cpu-deferred"
    )
    svc.dao.set_state(meeting.id, MeetingState.RECORDED)
    svc.dao.update_meeting(meeting.id, title=f"Meeting {name}")
    write_chunks(svc.dao.require_meeting(meeting.id).path, seconds=seconds)
    return str(meeting.id)


def test_the_worker_sends_progress_with_time_left_and_lets_go_after(api: ApiHarness) -> None:
    svc = api.services
    svc.asr = SlowAsr(api)
    meeting_id = recorded(api)
    job = svc.queue.enqueue(meeting_id, JobStage.TRANSCRIBE)
    claimed = svc.queue.claim(job.id)
    assert claimed is not None and svc.worker is not None

    svc.worker.execute(claimed)

    sent = [
        e.payload
        for e in svc.events.history
        if e.type == "job" and e.payload.get("action") == "progress"
    ]
    assert sent and all(e["meeting_id"] == meeting_id for e in sent)
    progress = [float(e["progress"]) for e in sent]  # type: ignore[arg-type]
    assert progress == sorted(progress)
    assert any(e["eta_s"] is not None for e in sent), "a measured pace says time left"
    assert svc.progress.get(meeting_id) is None, "the run is over"
    ended = [
        e.payload
        for e in svc.events.history
        if e.type == "job" and "action" not in e.payload and e.payload["stage"] == "transcribe"
    ]
    assert [e["state"] for e in ended] == ["running", "done"]


@pytest.mark.parametrize("step", ["_mark_running", "_announce"])
def test_a_run_that_fails_before_its_stage_leaves_no_progress_behind(
    api: ApiHarness, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    """The entry starts before the worker marks the job running and says so; either of
    those raising must not leave the meeting showing a run that is not happening."""
    svc = api.services
    meeting_id = recorded(api)
    job = svc.queue.enqueue(meeting_id, JobStage.TRANSCRIBE)
    claimed = svc.queue.claim(job.id)
    assert claimed is not None and svc.worker is not None
    started: list[str] = []
    real_start = svc.progress.start

    def start(mid: str, stage: str) -> None:
        started.append(mid)
        real_start(mid, stage)

    def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError(f"{step} failed")

    monkeypatch.setattr(svc.progress, "start", start)
    monkeypatch.setattr(svc.worker, step, boom)
    handlers = list(logging.getLogger().handlers)

    with pytest.raises(RuntimeError, match=step):
        svc.worker.execute(claimed)

    assert started == [meeting_id], "the entry was started"
    assert svc.progress.get(meeting_id) is None, "and ended with the run"
    assert logging.getLogger().handlers == handlers, "the meeting's log handler is gone too"


# ------------------------------------------------------------------ the list


def add(store: Any, name: str, client: str) -> Any:
    return store.create(
        source_name=name,
        source_kind="path",
        source_path=f"/nowhere/{name}",
        options=Options(),
        client=client,
    )


def test_the_active_list_is_both_queues_in_scheduler_order_and_nothing_finished(
    api: ApiHarness,
) -> None:
    svc = api.services
    store = svc.transcriptions
    client = api.client()

    running = recorded(api, "running")
    run_job = svc.queue.enqueue(running, JobStage.TRANSCRIBE)
    assert svc.queue.claim(run_job.id) is not None
    svc.progress.start(running, "transcribe")
    svc.progress.report(running, "transcribe", 0.5)

    api.clock.advance(5)
    claude = add(store, "interview.mp4", "mcp")
    api.clock.advance(5)
    waiting = recorded(api, "waiting")
    svc.queue.enqueue(waiting, JobStage.TRANSCRIBE)
    api.clock.advance(5)
    later = add(store, "later.wav", "api")

    # Finished work of every kind, none of it listed.
    done = add(store, "done.wav", "ui")
    assert store.claim(done.id) is not None
    store.complete(done.id, duration_s=3.0, language="en", language_conf=0.9, model={})
    cancelled = add(store, "cancelled.wav", "ui")
    store.cancel(cancelled.id)
    failed = recorded(api, "failed")
    failed_job = svc.queue.enqueue(failed, JobStage.SUMMARIZE)
    svc.queue.fail(failed_job, "no provider", permanent=True)
    finished = recorded(api, "finished")
    svc.queue.complete(svc.queue.enqueue(finished, JobStage.TRANSCRIBE))

    api.clock.advance(60)
    jobs = client.get("/api/jobs/active").json()["jobs"]

    assert [(j["kind"], j["id"]) for j in jobs] == [
        ("meeting", running),
        ("file", claude.id),
        ("meeting", waiting),
        ("file", later.id),
    ]
    assert [j["position"] for j in jobs] == [None, 2, 3, 4]
    assert [j["state"] for j in jobs] == ["running", "pending", "pending", "pending"]
    first = jobs[0]
    assert (first["title"], first["client"], first["stage"]) == (
        "Meeting running", "meeting", "transcribe",
    )  # fmt: skip
    assert first["phase"] == "transcribe" and first["progress"] == pytest.approx(
        meeting_progress("transcribe", 0.5)
    )
    assert first["eta_s"] is not None and first["eta_s"] > 0
    assert first["cancellable"] is False
    assert jobs[1]["client"] == "mcp" and jobs[1]["cancellable"] is True
    assert jobs[1]["waiting_reason"] == "queue"
    assert jobs[2]["progress"] is None, "a meeting waiting has no figure yet"


def test_a_meeting_on_a_hidden_account_is_left_out_but_keeps_its_place(
    api: ApiHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hidden everywhere (D82), the queue list too; but it runs all the same, so the work
    behind it is counted behind it."""
    svc = api.services
    hidden = recorded(api, "hidden")
    svc.queue.enqueue(hidden, JobStage.TRANSCRIBE)
    api.clock.advance(5)
    file = add(svc.transcriptions, "a.wav", "api")
    shown = svc.dao.visible_meeting
    monkeypatch.setattr(
        svc.dao, "visible_meeting", lambda mid: None if mid == hidden else shown(mid)
    )
    jobs = api.client().get("/api/jobs/active").json()["jobs"]
    assert [(j["id"], j["position"]) for j in jobs] == [(file.id, 2)]


needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@needs_ffmpeg
def test_a_job_sent_by_claude_is_listed_as_claudes(api: ApiHarness, tmp_path: Path) -> None:
    """The MCP bridge submits with ``X-Upshot-Client: mcp``; nothing else is needed for it
    to appear in Settings, labelled as Claude's."""
    path = tmp_path / "clip.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(speechish(3.0).tobytes())
    client = api.client()
    created = client.post(
        "/api/v1/transcriptions", json={"path": str(path)}, headers={"X-Upshot-Client": "mcp"}
    )
    assert created.status_code == 202, created.text
    assert created.json()["eta_s"] is None, "waiting: nothing to estimate"
    jobs = client.get("/api/jobs/active").json()["jobs"]
    assert [(j["id"], j["client"], j["state"]) for j in jobs] == [
        (created.json()["id"], "mcp", "pending")
    ]


def test_a_file_jobs_events_and_answers_carry_time_left(api: ApiHarness) -> None:
    svc = api.services
    store = svc.transcriptions
    job = add(store, "a.wav", "mcp")
    assert store.claim(job.id) is not None
    api.clock.advance(60)
    store.progress(job.id, "transcribe", 0.25)
    event = [e.payload for e in svc.events.history if e.type == "transcription"][-1]
    assert (event["state"], event["eta_s"]) == ("running", 180)
    # The answer the MCP bridge long-polls (``/wait``) and every other read.
    answer = api.client().get(f"/api/v1/transcriptions/{job.id}").json()
    assert answer["eta_s"] == 180
