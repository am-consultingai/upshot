"""The scenarios of the 2026-10-06 review, end to end through the API (D88, D89).

A calendar meeting from 14:00 to 15:00 and a recording started from the page (no meeting
named), at various times and lengths: which meeting it belongs to, whether the user is
asked, and how many recordings the library ends with. The calendar matching is the app's
own (the event cache and the matcher), at the start of the recording and again at its end.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.gcal.events import Attendee, CalendarEvent, EventStore
from app.gcal.source import GoogleCalendarSource
from tests.fixtures.api import build_harness, seed_calendar_account


@pytest.fixture
def api(tmp_path: Path, app_home: Path):  # type: ignore[no-untyped-def]
    harness = build_harness(tmp_path)
    yield harness
    recorder = harness.services.recorder
    if recorder is not None and recorder.committed:
        recorder.stop()


def calendar(api: Any, *meetings: tuple[str, str, float, float]) -> datetime:
    """Meetings as (id, title, minutes from now to the start, minutes long). The time the
    first one starts: the 14:00 of the scenarios."""
    account = seed_calendar_account(api.services)
    store = EventStore(api.services.conn)
    now = api.clock.now()
    events = [
        CalendarEvent(
            account_id=account,
            calendar_id="primary",
            event_id=event_id,
            title=title,
            start=now + timedelta(minutes=start),
            end=now + timedelta(minutes=start + length),
            attendees=(Attendee(name="Dana Levi"),),
            attendee_count=1,
            conference_url="https://zoom.us/j/1",
        )
        for event_id, title, start, length in meetings
    ]
    store.replace_window(
        account,
        "primary",
        now - timedelta(hours=6),
        now + timedelta(hours=6),
        events,
        synced_at=datetime.now().astimezone(),
    )
    api.services.meetings.enrichment_source = GoogleCalendarSource(store, active=lambda: [account])
    return events[0].start if events else now


def record(api: Any, *, at: datetime, minutes: float) -> str:
    """Press Start at ``at``, record for ``minutes``, press Stop."""
    api.clock.advance((at - api.clock.now()).total_seconds())
    client = api.client()
    started = client.post("/api/recording/start", json={})
    assert started.status_code == 200, started.text
    api.emit(1)
    api.clock.advance(minutes * 60)
    stopped = client.post("/api/recording/stop")
    assert stopped.status_code == 200, stopped.text
    return str(stopped.json()["meeting_id"])


def page(api: Any, meeting_id: str) -> dict[str, Any]:
    response = api.client().get(f"/api/meetings/{meeting_id}")
    assert response.status_code == 200, response.text
    return dict(response.json())


def asked(api: Any) -> int:
    return len([t for t in api.services.notifier.shown if t.title == "Which meeting was this?"])


@pytest.mark.parametrize(
    ("name", "start_min", "minutes"),
    [
        ("1. on time, leave at 14:50", 0, 50),
        ("2. join early, 13:55", -5, 50),
        ("3. join late, 14:20", 20, 30),
        ("4. leave early, 14:10", 0, 10),
        ("5. run over to 15:20", 0, 80),
    ],
)
def test_a_recording_in_its_meetings_time_is_that_meeting(
    api: Any, name: str, start_min: float, minutes: float
) -> None:
    fourteen = calendar(api, ("ev-1", "Data science sync", 30, 60))
    meeting_id = record(api, at=fourteen + timedelta(minutes=start_min), minutes=minutes)
    shown = page(api, meeting_id)
    assert shown["title"] == "Data science sync", name
    assert shown["calendar"]["match"]["state"] == "matched", name
    assert shown["needs_meeting"] is False, name
    assert asked(api) == 0, name


def test_13_an_unscheduled_call_is_asked_about(api: Any) -> None:
    """Calendar connected, nothing on it now: the recording needs the user's word."""
    calendar(api, ("ev-1", "Later today", 300, 60))
    meeting_id = record(api, at=api.clock.now() + timedelta(minutes=5), minutes=20)
    shown = page(api, meeting_id)
    assert shown["calendar"]["match"]["state"] == "none"
    assert shown["needs_meeting"] is True
    assert asked(api) == 1
    toast = next(t for t in api.services.notifier.shown if t.title == "Which meeting was this?")
    assert [b.label for b in toast.buttons] == ["Not on my calendar"]


def test_13b_a_call_far_off_its_meetings_time_is_asked_about(api: Any) -> None:
    fourteen = calendar(api, ("ev-1", "Data science sync", 30, 60))
    meeting_id = record(api, at=fourteen + timedelta(hours=3), minutes=20)
    assert page(api, meeting_id)["needs_meeting"] is True


def test_12_two_meetings_booked_at_once_are_proposed_and_asked_about(api: Any) -> None:
    fourteen = calendar(api, ("ev-a", "Pricing review", 30, 60), ("ev-b", "Hiring sync", 30, 60))
    meeting_id = record(api, at=fourteen, minutes=40)
    shown = page(api, meeting_id)
    assert shown["calendar"]["match"]["state"] == "proposed"
    assert shown["needs_meeting"] is True
    toast = next(t for t in api.services.notifier.shown if t.title == "Which meeting was this?")
    labels = sorted(b.label for b in toast.buttons)
    assert labels == ["It was: Hiring sync", "It was: Pricing review", "Not on my calendar"]


def test_7_and_8_drops_and_rejoins_while_the_meeting_is_on_are_one_recording(api: Any) -> None:
    """Dropped at 14:20, back at 14:25; dropped at 14:35, back at 14:55."""
    fourteen = calendar(api, ("ev-1", "Data science sync", 30, 60))
    first = record(api, at=fourteen, minutes=20)
    second = record(api, at=fourteen + timedelta(minutes=25), minutes=10)
    third = record(api, at=fourteen + timedelta(minutes=55), minutes=10)
    assert first == second == third
    assert [m.id for m in api.services.dao.list_meetings(include_hidden=True)] == [first]


def test_10_back_to_back_meetings_are_two_recordings(api: Any) -> None:
    fourteen = calendar(api, ("ev-a", "Standup", 30, 30), ("ev-b", "Planning", 60, 30))
    first = record(api, at=fourteen, minutes=29)
    second = record(api, at=fourteen + timedelta(minutes=30), minutes=25)
    assert first != second
    assert page(api, first)["title"] == "Standup"
    assert page(api, second)["title"] == "Planning"


def test_a_new_day_of_a_meeting_is_a_new_recording(api: Any) -> None:
    """The same title another day is another occurrence: never continued or merged."""
    fourteen = calendar(api, ("ev-1", "Weekly sync", 30, 60))
    first = record(api, at=fourteen, minutes=50)
    api.clock.advance(24 * 3600)
    calendar(api, ("ev-2", "Weekly sync", 30, 60))
    second = record(api, at=api.clock.now() + timedelta(minutes=30), minutes=50)
    assert first != second
    assert len(api.services.dao.list_meetings(include_hidden=True)) == 2
