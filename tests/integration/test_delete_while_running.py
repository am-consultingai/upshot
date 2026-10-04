"""Deleting a meeting stops whatever runs for it (machine B, 2026-09-30).

The report: a meeting ended, its transcription started, and Delete was refused. Now the
meeting leaves every list at once, its waiting jobs are dropped, a running stage stops at
its next checkpoint, and the worker removes the files once the stage has let go of them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.meetings import DELETING_MARKER
from app.pipeline.context import StageContext
from tests.fixtures.api import build_harness


def make_meeting(api: Any, meeting_id: str = "m1") -> Any:
    folder = api.services.config.data_root / meeting_id
    meeting = api.services.dao.insert_meeting(
        meeting_id=meeting_id, folder=folder, source="manual",
        started_at="2026-09-30T10:00:00+03:00", title="Weekly sync",
    )  # fmt: skip
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "audio.wav").write_bytes(b"x" * 100)
    return meeting


def test_waiting_jobs_are_dropped_and_the_meeting_goes_at_once(
    tmp_path: Path, app_home: Path
) -> None:
    api = build_harness(tmp_path)
    meeting = make_meeting(api)
    api.services.queue.enqueue(meeting.id, "transcribe")
    body = api.client().delete(f"/api/meetings/{meeting.id}").json()
    assert body["pending"] is False
    assert api.services.dao.get_meeting(meeting.id) is None
    assert not meeting.path.exists()
    assert api.services.queue.claim_next() is None, "no job left to run"


def test_a_running_stage_stops_and_the_worker_finishes_the_delete(
    tmp_path: Path, app_home: Path
) -> None:
    api = build_harness(tmp_path)
    meeting = make_meeting(api)
    client = api.client()
    queue = api.services.queue
    worker = api.services.worker
    job = queue.enqueue(meeting.id, "transcribe")
    job = queue.claim_next()  # the worker has picked it up: transcription is running
    assert job is not None

    body = client.delete(f"/api/meetings/{meeting.id}").json()
    assert body["pending"] is True, "the stage still holds the files"
    # Gone for anyone looking, straight away.
    assert all(m["id"] != meeting.id for m in client.get("/api/meetings").json()["meetings"])
    assert client.get(f"/api/meetings/{meeting.id}").status_code == 404
    assert (meeting.path / DELETING_MARKER).exists()

    reached: list[str] = []

    def transcribe(ctx: StageContext) -> None:
        reached.append("started")
        ctx.checkpoint()  # the stage's next unit of work
        reached.append("never")

    worker.stages["transcribe"] = transcribe
    worker.execute(job)
    assert reached == ["started"], "stopped at its checkpoint"
    assert api.services.dao.get_meeting(meeting.id) is None
    assert not meeting.path.exists()
    assert not queue.deleting(meeting.id)
    assert queue.claim_next() is None, "nothing queued after it"


def test_a_stage_that_ends_anyway_does_not_move_the_meeting_on(
    tmp_path: Path, app_home: Path
) -> None:
    """A long step with no checkpoint (a summary call) finishes; nothing follows it."""
    api = build_harness(tmp_path)
    meeting = make_meeting(api)
    queue = api.services.queue
    job = queue.enqueue(meeting.id, "summarize")
    job = queue.claim_next()
    assert job is not None
    api.client().delete(f"/api/meetings/{meeting.id}")
    api.services.worker.stages["summarize"] = lambda ctx: None
    api.services.worker.execute(job)
    assert api.services.dao.get_meeting(meeting.id) is None
    assert queue.claim_next() is None, "render was not queued for a deleted meeting"


def test_transcription_stops_between_segments(tmp_path: Path, app_home: Path) -> None:
    """A track can take many minutes on the CPU: the backend checks between segments."""
    import pytest

    from app.asr.fake import FakeAsr
    from app.errors import Cancelled

    asr = FakeAsr()
    produced: list[int] = []

    def stop() -> None:
        produced.append(1)
        if len(produced) == 2:
            raise Cancelled("the meeting is being deleted")

    asr.stop_check = stop
    wav = tmp_path / "them.wav"
    import wave

    with wave.open(str(wav), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(b"\\x00\\x00" * 16000 * 60)
    with pytest.raises(Cancelled):
        asr.transcribe(wav)
    assert len(produced) == 2, "stopped at the second segment, not the end of the track"


def test_a_deletion_the_app_closed_on_is_finished_at_the_next_start(
    tmp_path: Path, app_home: Path
) -> None:
    api = build_harness(tmp_path)
    meeting = make_meeting(api)
    (meeting.path / DELETING_MARKER).write_text("", encoding="utf-8")
    assert api.services.meetings.finish_interrupted_deletes() == 1
    assert api.services.dao.get_meeting(meeting.id) is None
    assert not meeting.path.exists()


def test_deleting_takes_back_the_meetings_notifications(tmp_path: Path, app_home: Path) -> None:
    """Machine B: "Meeting ended - Transcribing..." stayed on screen after the delete."""
    api = build_harness(tmp_path)
    meeting = make_meeting(api)
    api.services.notifier.recording_ended(meeting.id, 1)
    assert api.services.notifier.titles()
    api.client().delete(f"/api/meetings/{meeting.id}")
    assert api.services.notifier.withdrawn == [meeting.id]
    assert not api.services.notifier.titles()


def test_windows_notifications_carry_their_meetings_group() -> None:
    import json

    from app.notify import Toast, WindowsToastNotifier, toast_group

    commands: list[list[str]] = []
    notifier = WindowsToastNotifier(spawn=lambda c: commands.append(c), app_in_front=lambda: False)
    notifier.show(Toast(title="Meeting ended", meeting_id="2026-09-30_2024_ec7763_x" * 3))
    payload = json.loads(commands[-1][-1])
    assert payload["group"] == toast_group("2026-09-30_2024_ec7763_x" * 3)
    assert len(payload["group"]) <= 64, "Windows limits a group to 64 characters"
    notifier.withdraw("2026-09-30_2024_ec7763_x" * 3)
    removal = json.loads(commands[-1][-1])
    assert removal["remove_group"] == payload["group"]


def test_a_refused_delete_says_why_and_is_logged(
    tmp_path: Path, app_home: Path, caplog: Any
) -> None:
    """A folder outside the data root is refused, and the refusal reaches the log."""
    api = build_harness(tmp_path)
    elsewhere = tmp_path / "elsewhere" / "m1"
    meeting = api.services.dao.insert_meeting(
        meeting_id="m1", folder=elsewhere, source="manual",
        started_at="2026-09-30T10:00:00+03:00", title="Weekly sync",
    )  # fmt: skip
    response = api.client().delete(f"/api/meetings/{meeting.id}")
    assert response.status_code == 400
    assert "outside the data folder" in response.json()["detail"]
    assert api.services.dao.get_meeting(meeting.id) is not None
    assert "m1: not deleted" in caplog.text
