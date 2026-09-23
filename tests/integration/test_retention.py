"""The retention sweep: `retention.audio_days` finally deletes something (D38)."""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

from app import meta, retention
from app.clock import iso
from app.pipeline.states import JobStage, MeetingState
from tests.fixtures.api import build_harness
from tests.fixtures.meetings import write_chunks


@pytest.fixture
def api(tmp_path: Path, app_home: Path):  # type: ignore[no-untyped-def]
    return build_harness(tmp_path, retention__audio_days=30)


def age(api, meeting, days: float):  # type: ignore[no-untyped-def]
    """Move a meeting into the past. Retention reads `ended_at`, then `started_at`."""
    when = iso(api.clock.now() - timedelta(days=days))
    api.services.dao.update_meeting(meeting.id, ended_at=when)
    return api.services.dao.require_meeting(meeting.id)


def recorded(api, *, days: float, transcribed: bool = True, seconds: float = 5.0):  # type: ignore[no-untyped-def]
    meeting = api.services.meetings.create(source="manual")
    write_chunks(meeting.path, seconds=seconds)
    if transcribed:
        (meeting.path / "transcript.md").write_text("# transcript\n", encoding="utf-8")
    api.services.dao.set_state(meeting.id, MeetingState.RECORDED)
    return age(api, meeting, days)


def plan_for(api):  # type: ignore[no-untyped-def]
    svc = api.services
    return retention.plan(
        dao=svc.dao,
        queue=svc.queue,
        meetings=svc.meetings,
        config=svc.config,
        clock=svc.clock,
        recorder=svc.recorder,
    )


# ------------------------------------------------------------------ the audio sweep


def test_audio_older_than_the_policy_is_deleted(api) -> None:  # type: ignore[no-untyped-def]
    old = recorded(api, days=45)
    recent = recorded(api, days=3)
    assert (old.path / "audio" / "me.wav").exists()

    result = retention.sweep_services(api.services)

    assert result.audio_removed == 1
    assert result.bytes_freed > 0
    assert not (old.path / "audio").exists()
    assert (recent.path / "audio" / "me.wav").exists(), "a recent meeting is untouched"


def test_everything_derived_from_the_audio_survives_it(api) -> None:  # type: ignore[no-untyped-def]
    """The point of deleting audio is that the meeting is *not* deleted."""
    old = recorded(api, days=45)
    (old.path / "summary.html").write_text("<p>notes</p>", encoding="utf-8")

    retention.sweep_services(api.services)

    assert (old.path / "transcript.md").exists()
    assert (old.path / "summary.html").exists()
    assert api.services.dao.get_meeting(old.id) is not None
    mirrored = meta.read(old.path)
    assert mirrored["audio_deleted_at"], "the moment is recorded, not inferred from absence"
    assert mirrored["audio_bytes_freed"] > 0


def test_a_meeting_with_no_transcript_keeps_its_audio_forever(api) -> None:  # type: ignore[no-untyped-def]
    """Deleting it would destroy the only copy of the meeting."""
    stuck = recorded(api, days=400, transcribed=False)

    decided = plan_for(api)

    assert decided.audio == ()
    assert (stuck.id, "not transcribed yet — the audio is the only copy") in decided.spared
    retention.sweep_services(api.services)
    assert (stuck.path / "audio" / "me.wav").exists()


def test_a_meeting_still_recording_is_never_swept(api) -> None:  # type: ignore[no-untyped-def]
    """A meeting stuck in RECORDING for weeks is a crash, not an expired recording."""
    meeting = api.services.meetings.create(source="manual")
    write_chunks(meeting.path, seconds=5.0)
    (meeting.path / "transcript.md").write_text("# transcript\n", encoding="utf-8")
    assert meeting.meeting_state is MeetingState.RECORDING
    meeting = age(api, meeting, 45)

    decided = plan_for(api)

    assert decided.audio == ()
    assert (meeting.id, "still recording") in decided.spared


def test_a_queued_job_holds_the_audio(api) -> None:  # type: ignore[no-untyped-def]
    """A retry enqueued last week must still find its audio this week."""
    meeting = recorded(api, days=45)
    api.services.queue.enqueue(meeting.id, JobStage.SUMMARIZE)

    decided = plan_for(api)

    assert decided.audio == ()
    assert (meeting.id, "a job is still queued") in decided.spared


def test_the_plan_deletes_nothing(api) -> None:  # type: ignore[no-untyped-def]
    old = recorded(api, days=45)
    decided = plan_for(api)
    assert [item.meeting_id for item in decided.audio] == [old.id]
    assert decided.bytes > 0
    assert (old.path / "audio" / "me.wav").exists()


def test_a_second_sweep_finds_nothing_left_to_do(api) -> None:  # type: ignore[no-untyped-def]
    recorded(api, days=45)
    assert retention.sweep_services(api.services).audio_removed == 1
    again = retention.sweep_services(api.services)
    assert again.audio_removed == 0
    assert again.plan.spared == (), "an already-swept meeting is not reported as skipped"


# ------------------------------------------------------------------ the policy itself


@pytest.mark.parametrize("raw", [None, 0, -1, "nonsense", 1.5, [30]])
def test_only_a_positive_number_of_days_deletes_anything(raw: object) -> None:
    """`null` and `0` both mean *never*, and so does anything that is not a count."""
    expected = 1 if raw == 1.5 else None
    assert retention.days_of(raw) == expected


def test_zero_and_null_both_mean_never(tmp_path: Path, app_home: Path) -> None:
    for value in (None, 0, -1):
        harness = build_harness(tmp_path / f"root{value}", retention__audio_days=value)
        recorded(harness, days=9999)
        decided = plan_for(harness)
        assert decided.audio == (), value
        assert decided.audio_days is None, value


def test_transcripts_are_kept_indefinitely_by_default(api) -> None:  # type: ignore[no-untyped-def]
    old = recorded(api, days=4000)
    retention.sweep_services(api.services)
    assert api.services.dao.get_meeting(old.id) is not None
    assert (old.path / "transcript.md").exists()


def test_transcript_days_removes_the_meeting_outright(tmp_path: Path, app_home: Path) -> None:
    harness = build_harness(tmp_path, retention__audio_days=30, retention__transcript_days=365)
    old = recorded(harness, days=400)
    keep = recorded(harness, days=40)

    result = retention.sweep_services(harness.services)

    assert result.meetings_removed == 1
    assert not old.path.exists()
    assert harness.services.dao.get_meeting(old.id) is None
    assert keep.path.exists(), "younger than transcript_days: only its audio goes"
    assert not (keep.path / "audio").exists()


def test_a_folder_outside_the_data_root_is_refused(api) -> None:  # type: ignore[no-untyped-def]
    """The folder comes from the database; it must never aim the delete somewhere else."""
    meeting = recorded(api, days=45)
    api.services.dao.update_meeting(meeting.id, folder="/etc")

    decided = plan_for(api)

    assert (meeting.id, "outside the data folder") in decided.spared


# ------------------------------------------------------------------ how it is driven


def test_the_worker_sweeps_once_and_then_waits(api) -> None:  # type: ignore[no-untyped-def]
    old = recorded(api, days=45)
    worker = api.services.worker
    assert worker is not None

    first = worker.maybe_sweep()
    assert first is not None and first.audio_removed == 1
    assert not (old.path / "audio").exists()

    recorded(api, days=45)
    assert worker.maybe_sweep() is None, "the interval has not elapsed"
    api.clock.advance(6 * 3600 + 1)
    assert worker.maybe_sweep() is not None


def test_sweep_hours_zero_turns_the_sweep_off(tmp_path: Path, app_home: Path) -> None:
    harness = build_harness(tmp_path, retention__audio_days=30, retention__sweep_hours=0)
    old = recorded(harness, days=45)
    assert harness.services.worker is not None
    assert harness.services.worker.maybe_sweep() is None
    assert (old.path / "audio" / "me.wav").exists()


def test_the_api_shows_the_policy_before_it_is_believed(api) -> None:  # type: ignore[no-untyped-def]
    old = recorded(api, days=45)
    stuck = recorded(api, days=45, transcribed=False)
    client = api.client()

    body = client.get("/api/retention").json()
    assert body["audio_days"] == 30 and body["transcript_days"] is None
    assert [item["meeting_id"] for item in body["audio"]] == [old.id]
    assert {item["meeting_id"] for item in body["spared"]} == {stuck.id}
    assert body["bytes"] > 0
    assert (old.path / "audio" / "me.wav").exists(), "GET must not delete"

    swept = client.post("/api/retention/sweep").json()
    assert swept["audio_removed"] == 1 and swept["bytes_freed"] > 0
    assert not (old.path / "audio").exists()


def test_the_meeting_page_is_told_the_audio_was_deleted(api) -> None:  # type: ignore[no-untyped-def]
    """Otherwise a swept meeting is indistinguishable from a recording that failed."""
    old = recorded(api, days=45)
    client = api.client()
    assert client.get(f"/api/meetings/{old.id}").json()["audio_deleted_at"] is None

    retention.sweep_services(api.services)

    body = client.get(f"/api/meetings/{old.id}").json()
    assert body["audio_deleted_at"]
    assert body["audio_tracks"] == {}


# ------------------------------------------------------------------ deletion that fails


@pytest.fixture
def unremovable(api):  # type: ignore[no-untyped-def]
    """A meeting whose audio cannot be unlinked, the way Windows behaves on an open file.

    Found by running the sweep on Windows while a reader held `them.wav`: `rmtree` with
    `ignore_errors=True` removed what it could, said nothing, and the meeting was recorded
    as swept with its audio still on disk.

    POSIX only: this stands in for the open file with a read-only folder, and Windows
    ignores the mode bits, so there the audio is simply deleted.
    """
    if sys.platform == "win32":
        pytest.skip("chmod does not stop a deletion on Windows")
    meeting = recorded(api, days=45)
    audio = meeting.path / "audio"
    audio.chmod(0o500)  # readable and traversable, not writable
    yield meeting
    if audio.exists():
        audio.chmod(0o700)


def test_a_deletion_that_failed_is_not_recorded_as_done(api, unremovable) -> None:  # type: ignore[no-untyped-def]
    result = retention.sweep_services(api.services)

    assert result.audio_removed == 0
    assert result.bytes_freed == 0
    assert len(result.errors) == 1 and unremovable.id in result.errors[0]
    assert (unremovable.path / "audio" / "me.wav").exists()
    assert "audio_deleted_at" not in meta.read(unremovable.path), (
        "a meeting marked swept is never looked at again"
    )


def test_the_next_sweep_tries_again(api, unremovable) -> None:
    """The whole point of not writing the mark: this must not be a permanent skip."""
    assert retention.sweep_services(api.services).errors
    (unremovable.path / "audio").chmod(0o700)

    result = retention.sweep_services(api.services)

    assert result.audio_removed == 1 and result.errors == []
    assert not (unremovable.path / "audio").exists()
    assert meta.read(unremovable.path)["audio_deleted_at"]


def test_deleting_a_meeting_that_cannot_be_removed_keeps_its_row(api, unremovable) -> None:
    """A row deleted against a folder that survived orphans the folder for good."""
    with pytest.raises(OSError, match="still held"):
        api.services.meetings.purge(unremovable)
    assert api.services.dao.get_meeting(unremovable.id) is not None


def test_the_delete_endpoint_says_so_rather_than_claiming_success(api, unremovable) -> None:
    response = api.client().delete(f"/api/meetings/{unremovable.id}")
    assert response.status_code == 409, response.text
    assert "still held" in response.json()["detail"]
    assert api.services.dao.get_meeting(unremovable.id) is not None
