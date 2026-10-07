"""The next meeting after a hang-up is its own recording (D90).

Back-to-back meetings let go of the microphone between them, if only for a moment. The
grace after a hang-up used to read the next meeting taking it as the last one coming
back, and recorded both as one. Now the calendar decides, at the moment the microphone is
taken again: the same meeting is a rejoin; another meeting ends the last recording where
its call ended and starts the next with what was heard since. Told by the clock alone,
the next meeting is proposed with the last one, and the user asked.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from app import meta
from app.detect.detector import DetectorState
from app.pipeline.states import MeetingState
from tests.integration.test_calendar_assignment import (
    MEET,
    T0,
    ZOOM,
    Calendar,
    a_call,
    at,
    detector_with,
    event,
    recorded_calendar,
)
from tests.integration.test_phase12_detector import Harness, build, in_a_call_recorded_by_hand

PRICING = event("pricing", "Pricing review", minutes=30, url=ZOOM)
HIRING = event(
    "hiring", "Hiring sync", start=T0 + timedelta(minutes=30), minutes=30, url=ZOOM + "1"
)
DESIGN = event("design", "Design review", minutes=30, url=MEET)
ROADMAP = event("roadmap", "Roadmap", start=T0 + timedelta(minutes=30), minutes=30, url=MEET)


def recording(h: Harness, process: str, *windows: str) -> str:
    a_call(h, process, *windows)
    h.seconds(12)
    assert h.detector.state is DetectorState.RECORDING
    return h.detector.meeting_id or ""


def hang_up(h: Harness, seconds: int = 3) -> None:
    h.mic.release()
    h.seconds(seconds)
    assert h.detector.state is DetectorState.GRACE


def ids(h: Harness) -> list[str]:
    return [m.id for m in h.dao.list_meetings(include_hidden=True)]


def test_back_to_back_on_the_same_app_is_two_recordings_and_the_second_is_asked(
    tmp_path: Path,
) -> None:
    """Zoom then Zoom: nothing but the clock tells the next meeting from the last one
    running over, so the next recording is proposed with both, and asked about."""
    h = detector_with(tmp_path, PRICING, HIRING, mode="on")
    first = recording(h, "Zoom.exe")
    assert recorded_calendar(h, first)["event"]["event_id"] == "pricing"
    at(h, T0 + timedelta(minutes=30, seconds=5))
    h.seconds(2)
    hang_up(h)
    a_call(h, "Zoom.exe")
    h.seconds(3)

    second = h.detector.meeting_id
    assert second and second != first
    assert h.detector.state is DetectorState.RECORDING and h.recorder.committed
    assert h.dao.require_meeting(first).state == MeetingState.RECORDED
    snap = recorded_calendar(h, second)
    assert snap["match"]["state"] == "proposed"
    assert [c["event_id"] for c in snap["candidates"]] == ["hiring", "pricing"]
    # What was heard since the hang-up is the next recording's start, not lost.
    assert int(meta.read(h.dao.require_meeting(second).path)["preroll_ms"]) >= 2500

    h.seconds(20)
    h.mic.release()
    h.seconds(h.detector.grace_s + 2)
    asked = [t for t in h.notifier.shown if t.title == "Which meeting was this?"]
    assert asked, "the guess is the user's to settle"
    assert sorted(ids(h)) == sorted([first, second])


def test_the_window_naming_the_next_meeting_settles_it(tmp_path: Path) -> None:
    h = detector_with(tmp_path, DESIGN, ROADMAP, mode="on")
    first = recording(h, "chrome.exe", "Meet - Design review - Google Chrome")
    at(h, T0 + timedelta(minutes=29, seconds=50))
    h.seconds(2)
    hang_up(h)
    a_call(h, "chrome.exe", "Meet - Roadmap - Google Chrome")
    h.seconds(3)

    second = h.detector.meeting_id or ""
    assert second != first
    snap = recorded_calendar(h, second)
    assert snap["match"]["state"] == "matched"
    assert snap["event"]["event_id"] == "roadmap"
    assert recorded_calendar(h, first)["event"]["event_id"] == "design"


def test_a_rejoin_while_the_meeting_is_on_is_the_same_recording(tmp_path: Path) -> None:
    h = detector_with(tmp_path, PRICING, HIRING, mode="on")
    first = recording(h, "Zoom.exe")
    at(h, T0 + timedelta(minutes=10))
    h.seconds(2)
    hang_up(h, 10)
    a_call(h, "Zoom.exe")
    h.seconds(3)
    assert h.detector.meeting_id == first
    assert h.detector.state is DetectorState.RECORDING
    assert ids(h) == [first]


def test_a_rejoin_running_over_with_nothing_next_is_the_same_recording(tmp_path: Path) -> None:
    h = detector_with(tmp_path, PRICING, mode="on")
    first = recording(h, "Zoom.exe")
    at(h, T0 + timedelta(minutes=32))
    h.seconds(2)
    hang_up(h, 10)
    a_call(h, "Zoom.exe")
    h.seconds(3)
    assert h.detector.meeting_id == first
    assert ids(h) == [first]


def test_another_app_for_the_next_meeting_is_the_next_recording(tmp_path: Path) -> None:
    """Zoom, then a Meet in the browser: the app is the clue, so nothing is asked."""
    h = detector_with(tmp_path, PRICING, ROADMAP, mode="on")
    first = recording(h, "Zoom.exe")
    at(h, T0 + timedelta(minutes=30, seconds=10))
    h.seconds(2)
    hang_up(h)
    a_call(h, "chrome.exe", "Meet - Roadmap - Google Chrome")
    h.seconds(3)
    second = h.detector.meeting_id or ""
    assert second != first
    assert h.detector.recording_process == "chrome.exe"
    snap = recorded_calendar(h, second)
    assert snap["match"]["state"] == "matched" and snap["event"]["event_id"] == "roadmap"


def test_another_app_back_in_the_same_meeting_is_the_same_recording(tmp_path: Path) -> None:
    """Zoom crashed and the user rejoined from the browser: still the one meeting, and
    the browser letting go is what ends it now."""
    h = detector_with(tmp_path, PRICING, mode="on")
    first = recording(h, "Zoom.exe")
    hang_up(h, 5)
    a_call(h, "chrome.exe", "Zoom - Google Chrome")
    h.seconds(3)
    assert h.detector.meeting_id == first
    assert h.detector.state is DetectorState.RECORDING
    assert h.detector.recording_process == "chrome.exe"
    assert ids(h) == [first]


def test_detect_only_ends_the_last_recording_and_offers_the_next(tmp_path: Path) -> None:
    """In detect-only mode nothing starts by itself: a recording started by hand ends at
    its hang-up, and the next meeting is offered like any call."""
    from app.prompts import Prompts

    h = build(tmp_path, detection__mode="shadow")
    h.detector.calendar = Calendar(PRICING, HIRING)
    h.detector.prompts = Prompts()
    at(h, T0 + timedelta(seconds=30))
    first = in_a_call_recorded_by_hand(h, 20)
    from app.gcal.source import snapshot

    h.detector.meetings.choose_event(
        first, snapshot(PRICING, state="matched", source="user", confidence=1.0)
    )
    at(h, T0 + timedelta(minutes=30, seconds=5))
    hang_up(h)
    a_call(h, "Zoom.exe")
    h.seconds(14)
    assert not h.recorder.committed
    assert h.dao.require_meeting(first).state == MeetingState.RECORDED
    offer = h.detector.prompts.current
    assert offer is not None and offer.kind == "detected"
    assert ids(h) == [first]


def test_without_a_calendar_the_same_app_back_is_a_rejoin(tmp_path: Path) -> None:
    h = build(tmp_path, detection__mode="on")
    first = recording(h, "Zoom.exe")
    hang_up(h, 10)
    a_call(h, "Zoom.exe")
    h.seconds(3)
    assert h.detector.meeting_id == first
    assert ids(h) == [first]
