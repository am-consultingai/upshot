"""One call, one meeting: machine B's first real meeting (2026-10-06, Zoom).

Three symptoms, all seen in the same 40 minutes:

1. The recording ended every five minutes, "both tracks were silent", while both sides
   were talking. The silence check read the recorder's pre-roll ring, which is empty
   once a recording has committed, so it heard nothing for as long as anything was
   recorded.
2. The call was offered again as a new meeting after every such end, because Zoom still
   held the microphone.
3. Each start made a new meeting: seven items in the library for one calendar event.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.audio.vad import GateOnlyVerifier, TwoStageVad
from app.audio.writer import read_manifest, track_path
from app.detect.detector import DetectorState
from app.detect.factory import RecorderVad
from app.pipeline.states import MeetingState
from tests.fixtures.api import build_harness
from tests.integration.test_phase12_detector import Harness, build, start_by_hand

# ------------------------------------------------------------------ the silence check


def hear_through_the_recorder(h: Harness) -> None:
    """The VAD the app wires: it reads the recorder's own audio. Gate only, since the
    synthetic tone is sound, not speech."""
    h.detector.sources.vad = RecorderVad(h.recorder, vad=TwoStageVad(silero=GateOnlyVerifier()))


def in_a_zoom_call_recorded_by_hand(tmp_path: Path) -> tuple[Harness, str]:
    h = build(tmp_path, detection__mode="shadow")
    hear_through_the_recorder(h)
    h.mic.hold("Zoom.exe")
    return h, start_by_hand(h)


def test_a_call_where_both_sides_talk_is_not_ended_as_silent(tmp_path: Path) -> None:
    """Symptom 1: every recording on B ended 302 s in, with speech to the last second."""
    h, meeting_id = in_a_zoom_call_recorded_by_hand(tmp_path)
    h.seconds(h.detector.dual_silence_s + 120)
    assert h.recorder.committed, "a call with people talking was ended as silent"
    assert h.detector.state is DetectorState.RECORDING
    assert h.detector.meeting_id == meeting_id
    assert h.dao.require_meeting(meeting_id).state == MeetingState.RECORDING


def test_a_call_gone_quiet_still_ends_after_the_silence_limit(tmp_path: Path) -> None:
    """The check must still work: nobody speaking on either side for five minutes ends it."""
    h, meeting_id = in_a_zoom_call_recorded_by_hand(tmp_path)
    h.seconds(30)
    for capture in h.captures.values():
        capture.pattern = "silence"
    h.seconds(h.detector.dual_silence_s + 15)
    assert not h.recorder.committed
    assert h.dao.require_meeting(meeting_id).state == MeetingState.RECORDED


def test_the_call_being_recorded_is_not_offered_again_as_a_new_meeting(tmp_path: Path) -> None:
    """Symptom 2: "Meeting started" came back every five minutes for the same Zoom call."""
    h, _ = in_a_zoom_call_recorded_by_hand(tmp_path)
    h.seconds(11 * 60)
    offers = [t.title for t in h.notifier.shown if t.title.startswith("Meeting started")]
    assert offers == [], offers
    assert len(h.dao.list_meetings(include_hidden=True)) == 1


# ------------------------------------------------------------------ one event, one meeting


@pytest.fixture
def api(tmp_path: Path, app_home: Path):  # type: ignore[no-untyped-def]
    harness = build_harness(tmp_path)
    yield harness
    recorder = harness.services.recorder
    if recorder is not None and recorder.committed:
        recorder.stop()


def an_event_on_now(
    api: Any,
    event_id: str = "ev-1",
    title: str = "Data science sync",
    *,
    also: tuple[tuple[str, str], ...] = (),
) -> str:
    """A connected calendar with an hour-long meeting that started a minute ago (and the
    ``also`` meetings booked at the same time), and the meeting service matching
    recordings against it, as the app is wired. Each call replaces what the calendar
    holds."""
    from app.gcal.events import Attendee, CalendarEvent, EventStore
    from app.gcal.source import GoogleCalendarSource
    from tests.fixtures.api import seed_calendar_account

    account = seed_calendar_account(api.services)
    store = EventStore(api.services.conn)
    start = api.clock.now() - timedelta(minutes=1)
    store.replace_window(
        account,
        "primary",
        start - timedelta(hours=1),
        start + timedelta(hours=3),
        [
            CalendarEvent(
                account_id=account,
                calendar_id="primary",
                event_id=each_id,
                title=each_title,
                start=start,
                end=start + timedelta(hours=1),
                attendees=(Attendee(name="Dana Levi"), Attendee(name="Noa Cohen")),
                attendee_count=2,
            )
            for each_id, each_title in ((event_id, title), *also)
        ],
        synced_at=datetime.now().astimezone(),
    )
    api.services.meetings.enrichment_source = GoogleCalendarSource(store, active=lambda: [account])
    return account


def record_for(api: Any, seconds: float, body: dict[str, Any] | None = None) -> str:
    """Start, let ``seconds`` of audio reach the file, stop. The meeting's id."""
    client = api.client()
    started = client.post("/api/recording/start", json=body or {})
    assert started.status_code == 200, started.text
    recorder = api.services.recorder
    api.emit(seconds)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if all(c.frames.empty() for c in api.captures.values()):
            break
        time.sleep(0.02)
    time.sleep(0.1)  # the writer thread's last pump
    stopped = client.post("/api/recording/stop")
    assert stopped.status_code == 200, stopped.text
    assert not recorder.committed
    return str(started.json()["meeting_id"])


def wav_seconds(folder: Path, track: str) -> float:
    return (track_path(folder, track).stat().st_size - 44) / 2 / 16000


def test_starting_the_same_event_again_continues_its_meeting(api) -> None:  # type: ignore[no-untyped-def]
    """Symptom 3: seven recordings of one calendar event were seven meetings."""
    an_event_on_now(api)
    event = {"calendar_id": "primary", "event_id": "ev-1"}
    first = record_for(api, 3, event)
    api.clock.advance(15)  # the time it took to press Start again
    second = record_for(api, 2, event)

    assert second == first, "the second recording of the event made a new meeting"
    meetings = api.services.dao.list_meetings(include_hidden=True)
    assert [m.id for m in meetings] == [first]
    meeting = meetings[0]
    assert meeting.state == MeetingState.RECORDED
    folder = meeting.path
    records, torn = read_manifest(folder)
    assert torn == 0
    me = [r for r in records if r.track == "me"]
    assert [r.seq for r in me] == list(range(1, len(me) + 1)), "chunks continue, not restart"
    # Both parts are in the one file, with the time between them kept as silence, so
    # the transcript's timestamps are the meeting's own.
    assert wav_seconds(folder, "me") >= 3 + 15 + 2 - 0.5
    gaps = [r for r in me if r.gap_ms]
    assert gaps and 14_000 <= gaps[0].gap_ms <= 16_000, [r.gap_ms for r in me]
    assert meeting.duration_s is not None and meeting.duration_s >= 19
    jobs = [j for j in api.services.queue.for_meeting(first) if j.state == "pending"]
    assert [j.stage for j in jobs] == ["transcribe"], "transcribed once, after the last part"


def test_a_start_from_the_page_continues_the_meeting_the_calendar_matches(api) -> None:  # type: ignore[no-untyped-def]
    """On B half the restarts came from the page's Start, which names no event: the
    calendar match made each of them the same event, and still a new meeting."""
    an_event_on_now(api)
    first = record_for(api, 3)
    api.clock.advance(20)
    second = record_for(api, 2)
    assert second == first
    assert len(api.services.dao.list_meetings(include_hidden=True)) == 1


def test_a_different_event_is_a_different_meeting(api) -> None:  # type: ignore[no-untyped-def]
    an_event_on_now(api, "ev-1", "Data science sync")
    first = record_for(api, 2, {"calendar_id": "primary", "event_id": "ev-1"})
    an_event_on_now(api, "ev-2", "Pricing review")
    api.clock.advance(10)
    second = record_for(api, 2, {"calendar_id": "primary", "event_id": "ev-2"})
    assert second != first
    assert len(api.services.dao.list_meetings(include_hidden=True)) == 2


def test_a_rejoin_while_the_event_is_on_continues_however_long_the_break(api) -> None:  # type: ignore[no-untyped-def]
    """D89: the calendar meeting is still on, so it is still that meeting (scenario 8)."""
    an_event_on_now(api)
    event = {"calendar_id": "primary", "event_id": "ev-1"}
    first = record_for(api, 2, event)
    api.clock.advance(25 * 60)
    assert record_for(api, 2, event) == first


def test_a_rejoin_soon_after_the_event_ends_continues(api) -> None:  # type: ignore[no-untyped-def]
    """An overrun: the meeting ran past its slot and the call dropped after it."""
    an_event_on_now(api)  # began a minute ago, an hour long
    event = {"calendar_id": "primary", "event_id": "ev-1"}
    first = record_for(api, 2, event)
    api.clock.advance(59 * 60 + 10 * 60)  # ten minutes after its end
    assert record_for(api, 2, event) == first


def test_the_same_event_long_after_it_ended_is_a_new_meeting(api) -> None:  # type: ignore[no-untyped-def]
    an_event_on_now(api)
    event = {"calendar_id": "primary", "event_id": "ev-1"}
    first = record_for(api, 2, event)
    within = float(api.services.config.get("detection.continue_within_s", 900))
    api.clock.advance(59 * 60 + within + 60)  # past its end, and past the margin after
    assert record_for(api, 2, event) != first


def test_a_meeting_already_being_transcribed_is_not_reopened(api) -> None:  # type: ignore[no-untyped-def]
    """Its transcript is being written from the audio as it is: a new part cannot join."""
    an_event_on_now(api)
    event = {"calendar_id": "primary", "event_id": "ev-1"}
    first = record_for(api, 2, event)
    job = next(j for j in api.services.queue.for_meeting(first) if j.stage == "transcribe")
    assert api.services.queue.claim(job.id) is not None
    api.clock.advance(10)
    second = record_for(api, 2, event)
    assert second != first
