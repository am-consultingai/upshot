"""Which calendar meeting a recording belongs to (D89).

The scenarios of the 2026-10-06 review: one meeting on, meetings booked at the same time,
back to back, joined from Upshot or from elsewhere, and what the user is told and asked.
Machine B's own case is replayed: two meetings at 14:00, Chrome's window naming one.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.detect.assign import assign
from app.detect.detector import DetectorState
from app.gcal.events import Attendee, CalendarEvent
from app.pipeline.states import MeetingState
from app.prompts import Prompts
from tests.integration.test_phase12_detector import Harness, build

T0 = datetime(2026, 10, 6, 11, 0, tzinfo=datetime.now().astimezone().tzinfo).astimezone()

MEET = "https://meet.google.com/abc-defg-hij"
ZOOM = "https://us02web.zoom.us/j/81234567890"


def event(
    event_id: str,
    title: str,
    *,
    start: datetime = T0,
    minutes: int = 60,
    url: str | None = None,
    account: str = "acc",
) -> CalendarEvent:
    return CalendarEvent(
        calendar_id="primary",
        event_id=event_id,
        title=title,
        start=start,
        end=start + timedelta(minutes=minutes),
        attendees=(Attendee(name="Dana Levi"),),
        attendee_count=1,
        conference_url=url,
        account_id=account,
    )


TRAINING = event("training", "Training prep meeting")
DESIGN = event("design", "Design review: onboarding and settings", url=MEET)


# ------------------------------------------------------------------ the clues


def test_one_meeting_on_is_the_call() -> None:
    found = assign([TRAINING], process=r"C:\Zoom\bin\Zoom.exe")
    assert found.certain and found.event is TRAINING


def test_one_meeting_whose_link_is_for_another_app_is_only_proposed() -> None:
    """A Zoom call while the only meeting on is a Meet: probably another call."""
    found = assign([DESIGN], process=r"C:\Zoom\bin\Zoom.exe")
    assert not found.certain and found.event is None
    assert found.candidates == (DESIGN,)


def test_machine_b_the_window_title_names_the_meeting() -> None:
    """B, 14:00:30: Chrome held the microphone in "Meet - Design review…"."""
    found = assign(
        [TRAINING, DESIGN],
        process=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        windows=["Meet - Design review: onboarding and settings - Google Chrome"],
    )
    assert found.certain and found.event is DESIGN


def test_machine_b_a_zoom_call_is_not_the_meet_meeting() -> None:
    """B, 14:06: Zoom took the microphone. Design review is a Meet: the call is the other."""
    found = assign([DESIGN, TRAINING], process=r"C:\Users\x\AppData\Roaming\Zoom\bin\Zoom.exe")
    assert found.certain and found.event is TRAINING


def test_the_app_tells_a_zoom_meeting_from_a_meet_one() -> None:
    zoom = event("z", "Pricing", url=ZOOM)
    meet = event("m", "Hiring", url=MEET)
    assert assign([meet, zoom], process="Zoom.exe").event is zoom
    assert assign([zoom, meet], process="msedge.exe").certain is False, (
        "a browser can join both: Zoom has a web client"
    )


def test_two_meetings_nothing_tells_apart_are_offered_not_guessed() -> None:
    a = event("a", "Pricing", url=ZOOM)
    b = event("b", "Hiring", url="https://zoom.us/j/999")
    found = assign([a, b], process="Zoom.exe", windows=["Zoom Meeting"])
    assert not found.certain and found.event is None
    assert set(found.candidates) == {a, b}
    assert "nothing tells them apart" in found.reason


def test_back_to_back_the_call_is_the_meeting_that_began_last() -> None:
    first = event("first", "Standup", start=T0 - timedelta(minutes=30), minutes=32)
    second = event("second", "Planning", start=T0)
    found = assign([first, second], process="Teams.exe")
    assert found.certain and found.event is second


def test_a_title_too_short_to_mean_anything_is_not_a_clue() -> None:
    a = event("a", "1:1")
    b = event("b", "Sync")
    found = assign([a, b], process="chrome.exe", windows=["1:1 with the bank - Google Chrome"])
    assert not found.certain


def test_titles_are_matched_whatever_the_case_and_spacing() -> None:
    a = event("a", "Data  Science for Analysts")
    b = event("b", "Budget")
    found = assign([a, b], process="chrome.exe", windows=["Meet - data science for analysts"])
    assert found.event is a


def test_nothing_on_is_nothing() -> None:
    found = assign([], process="Zoom.exe")
    assert found.event is None and not found.certain and found.candidates == ()


# ------------------------------------------------------------------ the detector


class Calendar:
    """The calendar cache, as the detector reads it."""

    def __init__(self, *events: CalendarEvent) -> None:
        self.events = list(events)

    def live(self, now: datetime, *, lead_s: float = 120.0) -> list[CalendarEvent]:
        on = [e for e in self.events if e.start - timedelta(seconds=lead_s) <= now < e.end]
        return sorted(on, key=lambda e: e.start, reverse=True)

    def current(self, now: datetime, *, lead_s: float = 120.0) -> CalendarEvent | None:
        on = self.live(now, lead_s=lead_s)
        return on[0] if on else None

    def soon(self, now: datetime, *, ahead_s: float) -> list[CalendarEvent]:
        return [
            e for e in self.events if e.start < now + timedelta(seconds=ahead_s) and e.end > now
        ]


def at(h: Harness, when: datetime) -> None:
    h.clock.advance((when - h.clock.now()).total_seconds())


def detector_with(tmp_path: Path, *events: CalendarEvent, mode: str = "shadow") -> Harness:
    h = build(tmp_path, detection__mode=mode)
    h.detector.calendar = Calendar(*events)
    h.detector.prompts = Prompts()
    at(h, T0 + timedelta(seconds=30))
    return h


def a_call(h: Harness, process: str, *windows: str) -> None:
    h.mic.hold(process)
    h.titles.window_titles = list(windows) or ["Zoom Meeting"]
    h.vad.set(me=True, them=True)


def test_an_overbooked_call_is_offered_with_each_meeting_to_pick(tmp_path: Path) -> None:
    h = detector_with(tmp_path, event("a", "Pricing", url=ZOOM), event("b", "Hiring", url=ZOOM))
    a_call(h, "Zoom.exe")
    h.seconds(12)
    offer = h.detector.prompts.current
    assert offer is not None and offer.kind == "detected"
    assert offer.calendar_id is None, "a guess is never offered as the meeting"
    assert {c[2] for c in offer.candidates} == {"a", "b"}
    toast = next(t for t in h.notifier.shown if t.title.startswith("Meeting started"))
    labels = [b.label for b in toast.buttons]
    assert "Record: Pricing" in labels and "Record: Hiring" in labels
    assert "Which meeting" in toast.body


def test_a_call_the_clues_settle_is_offered_as_that_meeting(tmp_path: Path) -> None:
    h = detector_with(tmp_path, TRAINING, DESIGN)
    a_call(h, "chrome.exe", "Meet - Design review: onboarding and settings - Google Chrome")
    h.seconds(12)
    offer = h.detector.prompts.current
    assert offer is not None and offer.event_id == "design" and offer.candidates == ()
    # Why it is a call, for the banner to say: the score and the evidence behind it.
    assert offer.score is not None and offer.score >= h.detector.threshold
    codes = {code for code, _ in offer.evidence}
    assert {"vad.loopback", "window.title"} <= codes
    titles = [t.title for t in h.notifier.shown]
    assert "Meeting started: Design review: onboarding and settings" in titles


def test_two_meetings_starting_together_are_one_offer_and_two_notifications(
    tmp_path: Path,
) -> None:
    h = build(tmp_path, detection__mode="shadow")
    h.detector.calendar = Calendar(TRAINING, DESIGN)
    h.detector.prompts = Prompts()
    at(h, T0 - timedelta(seconds=2))
    h.seconds(4, audio=False)
    offer = h.detector.prompts.current
    assert offer is not None and offer.kind == "calendar"
    assert offer.calendar_id is None
    assert {c[2] for c in offer.candidates} == {"training", "design"}
    started = [t for t in h.notifier.shown if t.title.endswith("has started")]
    assert len(started) == 2, "each meeting's own notification starts that meeting"
    for toast in started:
        start = next(b for b in toast.buttons if b.label == "Start recording")
        assert start.event_id in ("training", "design")


def recorded_calendar(h: Harness, meeting_id: str) -> dict[str, Any]:
    import json

    raw = h.dao.require_meeting(meeting_id).calendar_json
    return json.loads(raw) if raw else {}


def test_an_automatic_recording_is_matched_by_the_detector_not_the_user(tmp_path: Path) -> None:
    h = detector_with(tmp_path, TRAINING, DESIGN, mode="on")
    a_call(h, "chrome.exe", "Meet - Design review: onboarding and settings - Google Chrome")
    h.seconds(12)
    assert h.detector.state is DetectorState.RECORDING
    snap = recorded_calendar(h, h.detector.meeting_id or "")
    assert snap["event"]["event_id"] == "design"
    assert snap["match"] == {**snap["match"], "state": "matched", "source": "detected"}
    assert h.dao.require_meeting(h.detector.meeting_id or "").title == DESIGN.title


def test_an_automatic_recording_of_an_overbooked_call_is_proposed(tmp_path: Path) -> None:
    h = detector_with(
        tmp_path, event("a", "Pricing", url=ZOOM), event("b", "Hiring", url=ZOOM), mode="on"
    )
    a_call(h, "Zoom.exe")
    h.seconds(12)
    snap = recorded_calendar(h, h.detector.meeting_id or "")
    assert snap["match"]["state"] == "proposed"
    assert {c["event_id"] for c in snap["candidates"]} == {"a", "b"}


def hang_up_and_wait(h: Harness, seconds: float) -> None:
    h.mic.release()
    h.vad.set(me=False, them=False)
    h.seconds(seconds)


def test_an_automatic_recording_rejoined_while_its_meeting_is_on_continues(
    tmp_path: Path,
) -> None:
    """The detector's own recordings follow D88 too (the gap the review found)."""
    h = detector_with(tmp_path, TRAINING, mode="on")
    a_call(h, "Zoom.exe")
    h.seconds(30)
    first = h.detector.meeting_id
    hang_up_and_wait(h, h.detector.grace_s + 5)
    assert h.detector.state is DetectorState.IDLE
    assert h.dao.require_meeting(first or "").state == MeetingState.RECORDED
    h.clock.advance(10 * 60)
    a_call(h, "Zoom.exe")
    h.seconds(12)
    assert h.detector.state is DetectorState.RECORDING
    assert h.detector.meeting_id == first
    assert len(h.dao.list_meetings(include_hidden=True)) == 1


def test_an_automatic_recording_long_after_its_meeting_is_a_new_one(tmp_path: Path) -> None:
    h = detector_with(
        tmp_path, TRAINING, event("later", "Later", start=T0 + timedelta(hours=2)), mode="on"
    )
    a_call(h, "Zoom.exe")
    h.seconds(30)
    first = h.detector.meeting_id
    hang_up_and_wait(h, h.detector.grace_s + 5)
    at(h, T0 + timedelta(hours=2, minutes=1))
    a_call(h, "Zoom.exe")
    h.seconds(12)
    assert h.detector.meeting_id not in (None, first)


# ------------------------------------------------------------------ starting from the app


@pytest.fixture
def api(tmp_path: Path, app_home: Path):  # type: ignore[no-untyped-def]
    from tests.fixtures.api import build_harness

    harness = build_harness(tmp_path)
    yield harness
    recorder = harness.services.recorder
    if recorder is not None and recorder.committed:
        recorder.stop()


def cache(api: Any, *events: CalendarEvent) -> str:
    from app.gcal.events import EventStore
    from tests.fixtures.api import seed_calendar_account

    account = seed_calendar_account(api.services)
    now = api.clock.now()
    EventStore(api.services.conn).replace_window(
        account,
        "primary",
        now - timedelta(hours=3),
        now + timedelta(hours=3),
        [
            CalendarEvent(
                **{
                    **e.__dict__,
                    "account_id": account,
                    "start": now - timedelta(minutes=1),
                    "end": now + timedelta(minutes=59),
                }
            )
            for e in events
        ],
        synced_at=datetime.now().astimezone(),
    )
    return account


def offer(api: Any, prompt: Any) -> None:
    api.services.prompts.offer(prompt, recording=False)


def started_snapshot(api: Any, response: Any) -> dict[str, Any]:
    import json

    assert response.status_code == 200, response.text
    meeting = api.services.dao.require_meeting(response.json()["meeting_id"])
    api.client().post("/api/recording/stop")
    return json.loads(meeting.calendar_json or "{}")


def test_starting_the_detectors_offer_as_offered_is_the_detectors_match(api) -> None:  # type: ignore[no-untyped-def]
    """Start on a banner that named a meeting: matched, but not the user's own choice."""
    from app.prompts import Prompt

    account = cache(api, DESIGN)
    offer(
        api,
        Prompt(
            kind="detected",
            title=DESIGN.title or "",
            at=api.clock.now(),
            calendar_id="primary",
            event_id="design",
            account_id=account,
            process="chrome.exe",
        ),
    )
    response = api.client().post(
        "/api/recording/start", json={"calendar_id": "primary", "event_id": "design"}
    )
    snap = started_snapshot(api, response)
    assert snap["match"]["state"] == "matched" and snap["match"]["source"] == "detected"


def test_picking_a_meeting_is_the_users_choice(api) -> None:  # type: ignore[no-untyped-def]
    cache(api, TRAINING, DESIGN)
    response = api.client().post(
        "/api/recording/start", json={"calendar_id": "primary", "event_id": "training"}
    )
    snap = started_snapshot(api, response)
    assert snap["event"]["event_id"] == "training"
    assert snap["match"]["source"] == "user"


def test_a_start_without_a_pick_on_an_overbooked_offer_is_proposed_and_unnamed(api) -> None:  # type: ignore[no-untyped-def]
    from app.prompts import Prompt

    account = cache(api, TRAINING, DESIGN)
    offer(
        api,
        Prompt(
            kind="detected",
            title="Training prep… / Design review…",
            at=api.clock.now(),
            process="Zoom.exe",
            candidates=((account, "primary", "training", "T"), (account, "primary", "design", "D")),
        ),
    )
    response = api.client().post("/api/recording/start", json={})
    meeting = api.services.dao.require_meeting(response.json()["meeting_id"])
    snap = started_snapshot(api, response)
    assert snap["match"]["state"] == "proposed"
    assert {c["event_id"] for c in snap["candidates"]} == {"training", "design"}
    assert "Design review" not in (meeting.title or ""), "the offer's two names are not a title"


def test_a_candidates_button_on_the_notification_records_that_meeting(api) -> None:  # type: ignore[no-untyped-def]
    from app.prompts import Prompt
    from tests.integration.test_toast_actions import ACTION_PATH, _launcher

    account = cache(api, TRAINING, DESIGN)
    offer(
        api,
        Prompt(
            kind="detected",
            title="T / D",
            at=api.clock.now(),
            process="Zoom.exe",
            candidates=((account, "primary", "training", "T"), (account, "primary", "design", "D")),
        ),
    )
    response = _launcher(api).post(
        ACTION_PATH,
        json={
            "action": "recording.start",
            "calendar_id": "primary",
            "event_id": "design",
            "account_id": account,
        },
    )
    snap = started_snapshot(api, response)
    assert snap["event"]["event_id"] == "design" and snap["match"]["source"] == "user"
