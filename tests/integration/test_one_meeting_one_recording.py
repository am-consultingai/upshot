"""One calendar meeting, one recording; and the user settles what the app cannot (D89).

- A second recording of a meeting whose first is already transcribed is merged into it:
  audio appended with the gap as silence, transcripts joined on one timeline, processed
  again. Machine B's seven pieces of 2026-10-06 are replayed and end as one.
- A recording whose calendar meeting is not settled is marked and asked about.
- Picking another calendar meeting summarizes again, or merges with that meeting's
  recording.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.asr.backend import Segment, Word
from app.audio.writer import read_manifest, track_path
from app.pipeline.states import MeetingState
from tests.fixtures.api import build_harness
from tests.integration.test_meeting_continuity import an_event_on_now, record_for

EVENT = {"calendar_id": "primary", "event_id": "ev-1"}


@pytest.fixture
def api(tmp_path: Path, app_home: Path):  # type: ignore[no-untyped-def]
    harness = build_harness(tmp_path)
    yield harness
    recorder = harness.services.recorder
    if recorder is not None and recorder.committed:
        recorder.stop()


def seconds_of_audio(folder: Path, track: str = "me") -> float:
    return (track_path(folder, track).stat().st_size - 44) / 2 / 16000


def processed(
    api: Any, meeting_id: str, *speakers: str, upto: MeetingState = MeetingState.RENDERED
) -> None:
    """As if the pipeline had run: a transcript on disk, its jobs done, the state reached."""
    from app.pipeline.stages.transcribe import segments_path

    meeting = api.services.dao.require_meeting(meeting_id)
    segments = [
        Segment(
            id=i,
            track="me" if speaker.startswith("ME") else "them",
            speaker=speaker,
            start=0.5 + i,
            end=1.0 + i,
            text=f"{meeting_id[-6:]} line {i}",
            words=(Word("w", 0.5 + i, 0.9 + i),),
        ).as_dict()
        for i, speaker in enumerate(speakers)
    ]
    segments_path(meeting.path).write_text(
        json.dumps({"version": 1, "language": "en", "segments": segments}), encoding="utf-8"
    )
    api.services.conn.execute("UPDATE jobs SET state='done' WHERE meeting_id = ?", (meeting_id,))
    path = [
        MeetingState.TRANSCRIBING,
        MeetingState.TRANSCRIBED,
        MeetingState.SUMMARIZING,
        MeetingState.SUMMARIZED,
        MeetingState.RENDERED,
    ]
    for state in path[: path.index(upto) + 1]:
        api.services.dao.set_state(meeting_id, state)
    (meeting.path / "notes.json").write_text('{"summary_html": "<p>before</p>"}', encoding="utf-8")
    (meeting.path / "summary.html").write_text("<p>before</p>", encoding="utf-8")


def visible_ids(api: Any) -> list[str]:
    return [m.id for m in api.services.dao.list_meetings(include_hidden=True)]


# ------------------------------------------------------------------ merging


def transcribed_by_the_worker(api: Any, meeting_id: str, *speakers: str) -> None:
    """The later part's transcription finishes, as the worker reports it."""
    processed(api, meeting_id, *speakers, upto=MeetingState.TRANSCRIBING)
    job = api.services.queue.enqueue(meeting_id, "transcribe")
    claimed = api.services.queue.claim(job.id)
    assert claimed is not None
    api.services.worker._on_success(claimed)


def test_a_later_recording_of_a_transcribed_meeting_is_joined_once_transcribed(api) -> None:  # type: ignore[no-untyped-def]
    an_event_on_now(api)
    first = record_for(api, 3, EVENT)
    processed(api, first, "ME", "THEM_1", "THEM_2")
    api.clock.advance(20)
    second = record_for(api, 2, EVENT)
    assert second != first, "too far along to continue: a recording of its own"
    # Only the later part is transcribed, not the whole meeting again: until it is, the
    # two stay apart and the later one knows where it goes.
    assert set(visible_ids(api)) == {first, second}
    from app import meta

    assert meta.read(api.services.dao.require_meeting(second).path)["merge_into"] == first

    transcribed_by_the_worker(api, second, "ME", "THEM_1", "THEM_2")

    assert visible_ids(api) == [first]
    meeting = api.services.dao.require_meeting(first)
    assert not api.services.dao.get_meeting(second)
    assert not (meeting.path.parent / second).exists(), "the later folder is gone"
    # One audio timeline: the first part, the 20 s between, the second part.
    assert seconds_of_audio(meeting.path) == pytest.approx(3 + 20 + 2, abs=0.6)
    records, torn = read_manifest(meeting.path)
    assert torn == 0
    me = [r.seq for r in records if r.track == "me"]
    assert me == list(range(1, len(me) + 1))
    # One transcript: the second's lines after the first's, its speakers numbered on.
    from app.pipeline.stages.transcribe import load_segments

    segments, _ = load_segments(meeting.path)
    assert [s.id for s in segments] == list(range(len(segments)))
    later = [s for s in segments if s.text.startswith(second[-6:])]
    assert later and min(s.start for s in later) >= 3 + 20 - 0.6
    assert {s.speaker for s in later} == {"ME", "THEM_3", "THEM_4"}
    assert later[0].words[0].s >= 23 - 0.6, "word times move with their segment"
    # Processed again from the joined transcript: assembled and summarized, not
    # transcribed again.
    assert meeting.state == MeetingState.TRANSCRIBING
    pending = {j.stage for j in api.services.queue.for_meeting(first) if j.state == "pending"}
    assert pending == {"assemble"}
    assert api.services.queue.take_rerun(first, "summarize"), "the summary is redone too"
    assert not api.services.queue.take_rerun(first, "transcribe")


def test_the_later_id_still_finds_the_meeting(api) -> None:  # type: ignore[no-untyped-def]
    an_event_on_now(api)
    first = record_for(api, 2, EVENT)
    processed(api, first, "ME")
    api.clock.advance(10)
    second = record_for(api, 2, EVENT)
    transcribed_by_the_worker(api, second, "ME")
    page = api.client().get(f"/api/meetings/{second}")
    assert page.status_code == 200
    assert page.json()["id"] == first


def test_an_earlier_part_not_yet_transcribed_is_merged_at_once_and_transcribed_whole(api) -> None:  # type: ignore[no-untyped-def]
    """The first part's transcription had begun and was paused by the next recording (the
    worker yields to the recorder): no transcript yet, so the audio joins at once and the
    whole is transcribed, once."""
    an_event_on_now(api)
    first = record_for(api, 2, EVENT)
    api.services.dao.set_state(first, MeetingState.TRANSCRIBING)  # begun, then paused
    api.clock.advance(10)
    second = record_for(api, 2, EVENT)
    assert visible_ids(api) == [first]
    assert not api.services.dao.get_meeting(second)
    meeting = api.services.dao.require_meeting(first)
    assert meeting.state == MeetingState.RECORDED
    pending = {j.stage for j in api.services.queue.for_meeting(first) if j.state == "pending"}
    assert pending == {"transcribe"}
    assert seconds_of_audio(meeting.path) == pytest.approx(2 + 10 + 2, abs=0.6)


def test_a_busy_first_recording_is_not_merged_but_offered(api) -> None:  # type: ignore[no-untyped-def]
    an_event_on_now(api)
    first = record_for(api, 2, EVENT)
    processed(api, first, "ME", upto=MeetingState.TRANSCRIBED)
    job = api.services.queue.enqueue(first, "summarize")
    assert api.services.queue.claim(job.id) is not None  # summarizing right now
    api.clock.advance(10)
    second = record_for(api, 2, EVENT)
    assert set(visible_ids(api)) == {first, second}
    page = api.client().get(f"/api/meetings/{second}").json()
    assert page["merge_with"]["id"] == first, "the page offers the merge instead"
    # Once it is free, merging by hand works; the earlier recording is the one kept.
    api.services.conn.execute("UPDATE jobs SET state='done' WHERE id = ?", (job.id,))
    merged = api.client().post(f"/api/meetings/{second}/merge", json={"other": first})
    assert merged.status_code == 200, merged.text
    assert merged.json()["id"] == first
    assert visible_ids(api) == [first]


def test_a_recording_cannot_be_merged_with_itself(api) -> None:  # type: ignore[no-untyped-def]
    first = record_for(api, 2)
    response = api.client().post(f"/api/meetings/{first}/merge", json={"other": first})
    assert response.status_code == 409


def test_recordings_of_different_meetings_are_not_merged(api) -> None:  # type: ignore[no-untyped-def]
    an_event_on_now(api, "ev-1", "Pricing", also=(("ev-2", "Hiring"),))
    first = record_for(api, 2, {"calendar_id": "primary", "event_id": "ev-1"})
    processed(api, first, "ME")
    second = record_for(api, 2, {"calendar_id": "primary", "event_id": "ev-2"})
    assert set(visible_ids(api)) == {first, second}


def test_recordings_with_no_settled_meeting_are_not_merged(api) -> None:  # type: ignore[no-untyped-def]
    first = record_for(api, 2)
    processed(api, first, "ME")
    second = record_for(api, 2)
    assert set(visible_ids(api)) == {first, second}


def test_machine_b_seven_pieces_become_one(api) -> None:  # type: ignore[no-untyped-def]
    """B, 2026-10-06 14:06–14:43: seven recordings of one meeting, about 5 min each with
    10–270 s between. With the first already transcribed, every later one joins it once
    its own transcript is done."""
    an_event_on_now(api)
    gaps = [11, 13, 17, 15, 11, 267]
    first = record_for(api, 3, EVENT)
    processed(api, first, "ME", "THEM_1")
    for gap in gaps:
        api.clock.advance(gap)
        piece = record_for(api, 3, EVENT)
        assert piece != first
        transcribed_by_the_worker(api, piece, "ME", "THEM_1")
        assert visible_ids(api) == [first]
    meeting = api.services.dao.require_meeting(first)
    expected = 3 * 7 + sum(gaps)
    assert seconds_of_audio(meeting.path) == pytest.approx(expected, abs=3)
    assert meeting.duration_s == pytest.approx(expected, abs=3)
    from app.pipeline.stages.transcribe import load_segments

    segments, _ = load_segments(meeting.path)
    assert len(segments) == 2 * 7
    assert len({s.speaker for s in segments}) == 1 + 7, "each part's other side numbered on"


def test_a_later_part_with_a_pre_roll_is_placed_that_much_earlier(api) -> None:  # type: ignore[no-untyped-def]
    """A detected call's audio starts before its start, by the pre-roll: the later part's
    lines land where they were said, not a pre-roll late."""
    from app import meta

    an_event_on_now(api)
    first = record_for(api, 3, EVENT)
    processed(api, first, "ME")
    assert "preroll_ms" in meta.read(api.services.dao.require_meeting(first).path)
    api.clock.advance(20)
    second = record_for(api, 2, EVENT)
    meta.update(api.services.dao.require_meeting(second).path, preroll_ms=5000)
    transcribed_by_the_worker(api, second, "ME")
    meeting = api.services.dao.require_meeting(first)
    assert seconds_of_audio(meeting.path) == pytest.approx(3 + 20 - 5 + 2, abs=0.6)
    from app.pipeline.stages.transcribe import load_segments

    segments, _ = load_segments(meeting.path)
    later = [s for s in segments if s.text.startswith(second[-6:])]
    assert min(s.start for s in later) == pytest.approx(3 + 15 + 0.5, abs=0.6)


def test_a_merge_whose_later_folder_is_held_open_still_processes_the_meeting(  # type: ignore[no-untyped-def]
    api, monkeypatch
) -> None:
    """Windows will not delete a file something holds open. The joined meeting is
    processed all the same; the later recording's rows go at once, so nothing finds it,
    and its folder goes at the next start."""
    from app.meetings import DELETING_MARKER

    an_event_on_now(api)
    first = record_for(api, 3, EVENT)
    processed(api, first, "ME", "THEM_1")
    api.clock.advance(20)
    second = record_for(api, 2, EVENT)
    service = api.services.meetings
    real = service.purge

    def held(meeting):  # type: ignore[no-untyped-def]
        raise OSError("the file is in use")

    monkeypatch.setattr(service, "purge", held)
    transcribed_by_the_worker(api, second, "ME", "THEM_1")

    pending = {j.stage for j in api.services.queue.for_meeting(first) if j.state == "pending"}
    assert pending == {"assemble"}, "the joined meeting is processed"
    assert api.services.dao.resolve(second) == first
    listed = [m["id"] for m in api.client().get("/api/meetings").json()["meetings"]]
    assert listed == [first]
    assert api.client().get(f"/api/meetings/{second}").json()["id"] == first
    assert api.services.dao.get_meeting(second) is None
    left = service.folder_for(second)
    assert (left / DELETING_MARKER).exists()
    renamed = api.client().patch(f"/api/meetings/{second}", json={"title": "Renamed"})
    assert renamed.status_code == 404, "nothing left to act on but the meeting it joined"

    monkeypatch.setattr(service, "purge", real)
    assert service.finish_interrupted_deletes() == 1
    assert not left.exists()
    assert api.services.dao.require_meeting(first).path.exists()


def test_a_merge_that_fails_before_joining_leaves_both_as_they_were(  # type: ignore[no-untyped-def]
    api, monkeypatch
) -> None:
    import app.merge

    an_event_on_now(api)
    first = record_for(api, 2, EVENT)
    processed(api, first, "ME")
    api.clock.advance(10)
    second = record_for(api, 2, EVENT)
    processed(api, second, "ME", upto=MeetingState.TRANSCRIBED)
    api.services.queue.enqueue(second, "summarize")

    def full(*_args):  # type: ignore[no-untyped-def]
        raise OSError("no space left on the disk")

    monkeypatch.setattr(app.merge, "append_audio", full)
    response = api.client().post(f"/api/meetings/{second}/merge", json={"other": first})
    assert response.status_code >= 400
    assert set(visible_ids(api)) == {first, second}
    assert api.services.dao.resolve(second) == second
    pending = {j.stage for j in api.services.queue.for_meeting(second) if j.state == "pending"}
    assert pending == {"summarize"}, "its waiting job is back"


def test_a_merge_that_fails_partway_is_undone_and_can_be_done_again(  # type: ignore[no-untyped-def]
    api, monkeypatch
) -> None:
    """The writer saves each chunk as it closes: a merge that fails after the audio was
    appended cuts it back off, so the retry does not append it twice."""
    import app.merge

    an_event_on_now(api)
    first = record_for(api, 2, EVENT)
    processed(api, first, "ME")
    api.clock.advance(10)
    second = record_for(api, 2, EVENT)
    processed(api, second, "ME", upto=MeetingState.TRANSCRIBED)
    folder = api.services.dao.require_meeting(first).path
    from app.pipeline.stages.transcribe import segments_path

    audio = {t: track_path(folder, t).read_bytes() for t in ("me", "them")}
    manifest = (folder / "audio" / "manifest.jsonl").read_bytes()
    transcript = segments_path(folder).read_bytes()
    real = app.merge.join_segments

    def broken(*_args):  # type: ignore[no-untyped-def]
        raise OSError("no space left on the disk")

    monkeypatch.setattr(app.merge, "join_segments", broken)
    response = api.client().post(f"/api/meetings/{second}/merge", json={"other": first})
    assert response.status_code == 409
    assert {t: track_path(folder, t).read_bytes() for t in ("me", "them")} == audio
    assert (folder / "audio" / "manifest.jsonl").read_bytes() == manifest
    assert segments_path(folder).read_bytes() == transcript

    monkeypatch.setattr(app.merge, "join_segments", real)
    again = api.client().post(f"/api/meetings/{second}/merge", json={"other": first})
    assert again.status_code == 200, again.text
    assert seconds_of_audio(folder) == pytest.approx(2 + 10 + 2, abs=0.6)
    records, torn = read_manifest(folder)
    assert torn == 0
    me = [r.seq for r in records if r.track == "me"]
    assert me == list(range(1, len(me) + 1))


def test_deleting_by_a_merged_id_waits_for_the_running_stage(api) -> None:  # type: ignore[no-untyped-def]
    an_event_on_now(api)
    first = record_for(api, 3, EVENT)
    processed(api, first, "ME")
    api.clock.advance(20)
    second = record_for(api, 2, EVENT)
    transcribed_by_the_worker(api, second, "ME")
    queue = api.services.queue
    job = queue.peek()
    assert job is not None and job.meeting_id == first
    assert queue.claim(job.id) is not None  # the joined meeting is being assembled

    response = api.client().delete(f"/api/meetings/{second}")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "deleted": first,
        "folder": str(api.services.dao.require_meeting(first).path),
        "pending": True,
    }
    assert queue.deleting(first), "the running stage is asked to stop first"
    assert api.services.dao.require_meeting(first).path.exists()


# ------------------------------------------------------------------ needs a meeting


def proposed_recording(api: Any) -> str:
    """Two meetings booked at once and a Start that picked neither (D89)."""
    from app.prompts import Prompt

    account = an_event_on_now(api, "ev-a", "Pricing", also=(("ev-b", "Hiring"),))
    api.services.prompts.offer(
        Prompt(
            kind="detected",
            title="Pricing / Hiring",
            at=api.clock.now(),
            process="Zoom.exe",
            candidates=(
                (account, "primary", "ev-a", "Pricing"),
                (account, "primary", "ev-b", "Hiring"),
            ),
        ),
        recording=False,
    )
    return record_for(api, 2)


def test_an_unsettled_recording_is_marked_and_asked_about(api) -> None:  # type: ignore[no-untyped-def]
    meeting_id = proposed_recording(api)
    listed = api.client().get("/api/meetings").json()["meetings"]
    assert next(m for m in listed if m["id"] == meeting_id)["needs_meeting"] is True
    assert api.client().get(f"/api/meetings/{meeting_id}").json()["needs_meeting"] is True
    asked = [t for t in api.services.notifier.shown if t.title == "Which meeting was this?"]
    assert len(asked) == 1
    labels = [b.label for b in asked[0].buttons]
    assert labels == ["It was: Pricing", "It was: Hiring", "Not on my calendar"]


def test_answering_from_the_notification_settles_it(api) -> None:  # type: ignore[no-untyped-def]
    from tests.integration.test_toast_actions import ACTION_PATH, _launcher

    meeting_id = proposed_recording(api)
    button = next(
        b
        for t in api.services.notifier.shown
        if t.title == "Which meeting was this?"
        for b in t.buttons
        if b.label == "It was: Hiring"
    )
    response = _launcher(api).post(
        ACTION_PATH,
        json={
            "action": button.action,
            "meeting_id": button.meeting_id,
            "calendar_id": button.calendar_id,
            "event_id": button.event_id,
            "account_id": button.account_id,
        },
    )
    assert response.status_code == 200, response.text
    page = api.client().get(f"/api/meetings/{meeting_id}").json()
    assert page["needs_meeting"] is False
    assert page["title"] == "Hiring"
    assert page["calendar"]["match"]["source"] == "user"


def test_not_on_my_calendar_is_an_answer(api) -> None:  # type: ignore[no-untyped-def]
    from tests.integration.test_toast_actions import ACTION_PATH, _launcher

    meeting_id = proposed_recording(api)
    response = _launcher(api).post(
        ACTION_PATH, json={"action": "meeting.none", "meeting_id": meeting_id}
    )
    assert response.status_code == 200, response.text
    assert api.client().get(f"/api/meetings/{meeting_id}").json()["needs_meeting"] is False


def test_a_matched_recording_is_not_asked_about(api) -> None:  # type: ignore[no-untyped-def]
    an_event_on_now(api)
    meeting_id = record_for(api, 2, EVENT)
    assert api.client().get(f"/api/meetings/{meeting_id}").json()["needs_meeting"] is False
    assert not [t for t in api.services.notifier.shown if t.title == "Which meeting was this?"]


def test_without_a_calendar_nothing_is_asked(api) -> None:  # type: ignore[no-untyped-def]
    meeting_id = record_for(api, 2)
    assert api.client().get(f"/api/meetings/{meeting_id}").json()["needs_meeting"] is False
    assert not [t for t in api.services.notifier.shown if t.title == "Which meeting was this?"]


# ------------------------------------------------------------------ reassigning


def test_another_meeting_picked_after_the_summary_summarizes_again(api) -> None:  # type: ignore[no-untyped-def]
    api.services.config.set("llm.provider", "fake")
    an_event_on_now(api, "ev-a", "Pricing", also=(("ev-b", "Hiring"),))
    meeting_id = record_for(api, 2, {"calendar_id": "primary", "event_id": "ev-a"})
    processed(api, meeting_id, "ME")
    response = api.client().put(
        f"/api/meetings/{meeting_id}/calendar", json={"calendar_id": "primary", "event_id": "ev-b"}
    )
    assert response.status_code == 200, response.text
    folder = api.services.dao.require_meeting(meeting_id).path
    assert (folder / "notes.prev.json").read_text(encoding="utf-8").find("before") > 0
    assert (folder / "summary.prev.html").exists()
    pending = {j.stage for j in api.services.queue.for_meeting(meeting_id) if j.state == "pending"}
    assert pending == {"summarize"}


def test_without_an_ai_the_summary_is_not_redone(api) -> None:  # type: ignore[no-untyped-def]
    api.services.config.set("llm.provider", "none")
    an_event_on_now(api, "ev-a", "Pricing", also=(("ev-b", "Hiring"),))
    meeting_id = record_for(api, 2, {"calendar_id": "primary", "event_id": "ev-a"})
    processed(api, meeting_id, "ME")
    api.client().put(
        f"/api/meetings/{meeting_id}/calendar", json={"calendar_id": "primary", "event_id": "ev-b"}
    )
    pending = [j for j in api.services.queue.for_meeting(meeting_id) if j.state == "pending"]
    assert pending == []


def test_picking_the_meeting_of_another_recording_merges_them(api) -> None:  # type: ignore[no-untyped-def]
    an_event_on_now(api, "ev-a", "Pricing")
    first = record_for(api, 3, {"calendar_id": "primary", "event_id": "ev-a"})
    processed(api, first, "ME")
    api.clock.advance(30)
    api.services.meetings.enrichment_source = __import__(
        "app.enrich.null", fromlist=["NullSource"]
    ).NullSource()
    loose = record_for(api, 2)  # no calendar meeting settled
    processed(api, loose, "ME")
    assert set(visible_ids(api)) == {first, loose}
    response = api.client().put(
        f"/api/meetings/{loose}/calendar", json={"calendar_id": "primary", "event_id": "ev-a"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["id"] == first
    assert visible_ids(api) == [first]
