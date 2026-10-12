"""A transcription can be stopped from wherever it is shown, and the meeting stays.

Before, the only way to stop a meeting's transcription was to delete the meeting. Now a
waiting one leaves the queue at once, a running one stops at its next checkpoint, and the
meeting goes back to where it was: "recorded" for a first transcription, its transcript
and summary for a re-transcription.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.pipeline.context import StageContext
from app.pipeline.queue import STOPPED
from app.pipeline.states import MeetingState
from tests.fixtures.api import build_harness


def make_meeting(api: Any, meeting_id: str = "m1", state: str = "RECORDED") -> Any:
    folder = api.services.config.data_root / meeting_id
    api.services.dao.insert_meeting(
        meeting_id=meeting_id, folder=folder, source="manual",
        started_at="2026-10-12T10:00:00+03:00", title="Weekly sync",
    )  # fmt: skip
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "audio.wav").write_bytes(b"x" * 100)
    path = ("RECORDED", "TRANSCRIBING", "TRANSCRIBED", "SUMMARIZING", "SUMMARIZED", "RENDERED")
    for step in path[: path.index(state) + 1]:
        api.services.dao.set_state(meeting_id, step)
    return api.services.dao.require_meeting(meeting_id)


def run_until_checkpoint(api: Any, job: Any) -> list[str]:
    reached: list[str] = []

    def transcribe(ctx: StageContext) -> None:
        reached.append("started")
        ctx.checkpoint()
        reached.append("never")

    api.services.worker.stages["transcribe"] = transcribe
    api.services.worker.execute(job)
    return reached


def test_a_waiting_transcription_leaves_the_queue(tmp_path: Path, app_home: Path) -> None:
    api = build_harness(tmp_path)
    meeting = make_meeting(api)
    api.services.queue.enqueue(meeting.id, "transcribe")
    body = api.client().post(f"/api/meetings/{meeting.id}/transcription/stop").json()
    assert body["stopped"] == "waiting"
    assert api.services.queue.claim_next() is None, "nothing left to run"
    jobs = api.client().get(f"/api/meetings/{meeting.id}").json()["jobs"]
    assert jobs == [{**jobs[0], "state": "cancelled", "stopped": True}]
    assert api.services.dao.require_meeting(meeting.id).state == MeetingState.RECORDED


def test_a_running_transcription_stops_at_its_checkpoint(tmp_path: Path, app_home: Path) -> None:
    api = build_harness(tmp_path)
    meeting = make_meeting(api)
    queue = api.services.queue
    queue.enqueue(meeting.id, "transcribe")
    job = queue.claim_next()
    assert job is not None
    body = api.client().post(f"/api/meetings/{meeting.id}/transcription/stop").json()
    assert body["stopped"] == "running"

    assert run_until_checkpoint(api, job) == ["started"]
    stopped = queue.require(job.id)
    assert (stopped.state, stopped.last_error) == ("cancelled", STOPPED)
    assert not queue.stopping(meeting.id), "the press applied to that run only"
    assert api.services.dao.require_meeting(meeting.id).state == MeetingState.RECORDED
    assert queue.claim_next() is None, "assemble was not queued after it"


def test_a_stopped_retranscription_keeps_the_summary(tmp_path: Path, app_home: Path) -> None:
    api = build_harness(tmp_path)
    meeting = make_meeting(api, state="RENDERED")
    client = api.client()
    queue = api.services.queue
    client.post(f"/api/meetings/{meeting.id}/jobs/transcribe/retry?force=true")
    job = queue.claim_next()
    assert job is not None
    client.post(f"/api/meetings/{meeting.id}/transcription/stop")
    run_until_checkpoint(api, job)
    assert api.services.dao.require_meeting(meeting.id).state == MeetingState.RENDERED
    # The redo the press asked of the later stages went with it.
    assert not queue.take_rerun(meeting.id, "summarize")


def test_the_next_run_is_not_stopped(tmp_path: Path, app_home: Path) -> None:
    api = build_harness(tmp_path)
    meeting = make_meeting(api)
    client = api.client()
    queue = api.services.queue
    queue.enqueue(meeting.id, "transcribe")
    job = queue.claim_next()
    client.post(f"/api/meetings/{meeting.id}/transcription/stop")
    run_until_checkpoint(api, job)

    client.post(f"/api/meetings/{meeting.id}/jobs/transcribe/retry?force=true")
    again = queue.claim_next()
    api.services.worker.stages["transcribe"] = lambda ctx: ctx.checkpoint()
    api.services.worker.execute(again)
    assert queue.require(again.id).state == "done"
    assert not client.get(f"/api/meetings/{meeting.id}").json()["jobs"][0]["stopped"]


def test_nothing_to_stop_is_refused(tmp_path: Path, app_home: Path) -> None:
    api = build_harness(tmp_path)
    meeting = make_meeting(api, state="RENDERED")
    api.services.queue.enqueue(meeting.id, "summarize")
    answer = api.client().post(f"/api/meetings/{meeting.id}/transcription/stop")
    assert answer.status_code == 409, "a summary is not a transcription"
    assert api.services.queue.claim_next() is not None, "the summary still runs"


def test_settings_lists_a_meeting_transcription_as_stoppable(
    tmp_path: Path, app_home: Path
) -> None:
    api = build_harness(tmp_path)
    meeting = make_meeting(api)
    api.services.queue.enqueue(meeting.id, "transcribe")
    other = make_meeting(api, "m2", state="RENDERED")
    api.services.queue.enqueue(other.id, "summarize")
    jobs = {job["id"]: job for job in api.client().get("/api/jobs/active").json()["jobs"]}
    assert jobs[meeting.id]["cancellable"] is True
    assert jobs[other.id]["cancellable"] is False


def test_the_ended_notification_stops_it(tmp_path: Path, app_home: Path) -> None:
    from app.actions import parse

    api = build_harness(tmp_path)
    meeting = make_meeting(api)
    api.services.queue.enqueue(meeting.id, "transcribe")
    api.services.notifier.recording_ended(meeting.id, 12)
    toast = api.services.notifier.shown[-1]
    assert [button.action for button in toast.buttons] == ["meeting.open", "transcription.stop"]
    assert parse("upshot:transcription.stop?meeting=m1") is not None

    api.services.queue.request_stop(meeting.id)
    from app.transcription.active import stop_running

    assert stop_running(api.services) is None, "nothing runs once it is stopped"
