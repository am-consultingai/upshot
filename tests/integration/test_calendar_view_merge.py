"""One meeting, one block: a recording and its event are never drawn twice.

A recording made before the calendar was connected carries no snapshot, and nothing ever
goes back to give it one. In the calendar view that showed as the same meeting twice, at
two different times, under two different names — one block holding the joining link and
the other the transcript. The matcher that runs at record time is asked again when the
page is read, against the cache, and only a confident verdict is used. Nothing is stored:
the snapshot still belongs to the moment of recording.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.gcal.events import Attendee, CalendarEvent, EventStore
from app.pipeline.states import MeetingState
from tests.fixtures.api import build_harness

NOW = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)


@pytest.fixture
def api(tmp_path: Path, app_home: Path):  # type: ignore[no-untyped-def]
    return build_harness(tmp_path)


def _cache(api, event_id: str, start: datetime, minutes: int = 60, **extra):  # type: ignore[no-untyped-def]
    end = start + timedelta(minutes=minutes)
    event = CalendarEvent(
        calendar_id="primary",
        event_id=event_id,
        title=extra.pop("title", "Design review"),
        start=start,
        end=end,
        ical_uid=f"{event_id}@seed",
        response="accepted",
        attendees=(Attendee(name="Dana Levi"), Attendee(name="יוסי כהן")),
        attendee_count=2,
        conference_url="https://meet.google.com/abc-defg-hij",
        **extra,
    )
    EventStore(api.services.conn).replace_window("primary", start, end, [event], synced_at=NOW)
    return event


def _record(api, meeting_id: str, started: datetime, ended: datetime | None = None):  # type: ignore[no-untyped-def]
    meeting = api.services.dao.insert_meeting(
        meeting_id=meeting_id,
        folder=api.services.config.data_root / meeting_id,
        source="manual",
        state=MeetingState.RECORDING,
        profile="cpu-deferred",
        title="Recording",
        started_at=started.isoformat(),
    )
    if ended is not None:
        api.services.dao.update_meeting(meeting_id, ended_at=ended.isoformat())
    return meeting


def _events(api, day: datetime):  # type: ignore[no-untyped-def]
    response = api.client().get(
        "/api/calendar/events",
        params={"from": day.date().isoformat(), "to": (day + timedelta(days=1)).date().isoformat()},
    )
    assert response.status_code == 200, response.text
    return response.json()["events"]


def test_an_unmatched_recording_is_folded_into_its_event(api) -> None:  # type: ignore[no-untyped-def]
    _cache(api, "evt", NOW)
    # Started twelve minutes late and ran short, as recordings do.
    _record(api, "m-late", NOW + timedelta(minutes=12), NOW + timedelta(minutes=50))

    (event,) = _events(api, NOW)
    assert event["meeting_id"] == "m-late", "the event carries the recording"


def test_the_meeting_page_learns_what_meeting_it_was(api) -> None:  # type: ignore[no-untyped-def]
    _cache(api, "evt", NOW)
    _record(api, "m-late", NOW + timedelta(minutes=12), NOW + timedelta(minutes=50))

    payload = api.client().get("/api/meetings/m-late").json()
    calendar = payload["calendar"]
    assert calendar["title"] == "Design review"
    assert calendar["participants"] == ["Dana Levi", "יוסי כהן"]
    assert calendar["conference_url"] == "https://meet.google.com/abc-defg-hij"
    assert calendar["match"]["state"] == "matched"


def test_nothing_is_written_to_the_recording(api) -> None:  # type: ignore[no-untyped-def]
    """The snapshot belongs to the moment of recording. This is a reading, not a record."""
    _cache(api, "evt", NOW)
    _record(api, "m-late", NOW + timedelta(minutes=12), NOW + timedelta(minutes=50))
    api.client().get("/api/meetings/m-late")

    assert api.services.dao.require_meeting("m-late").calendar_json in (None, "")


def test_a_recording_the_user_ruled_on_is_never_second_guessed(api) -> None:  # type: ignore[no-untyped-def]
    _cache(api, "evt", NOW)
    _record(api, "m-said-no", NOW + timedelta(minutes=12), NOW + timedelta(minutes=50))
    api.services.dao.update_meeting(
        "m-said-no",
        calendar_json=json.dumps({"match": {"state": "none", "source": "user"}}),
    )

    (event,) = _events(api, NOW)
    assert event["meeting_id"] is None
    payload = api.client().get("/api/meetings/m-said-no").json()
    assert payload["calendar"]["match"]["state"] == "none"


def test_a_guess_is_not_drawn_as_a_fact(api) -> None:  # type: ignore[no-untyped-def]
    """Two events fit almost as well as each other, so the matcher proposes rather than
    decides — and a proposal is not something to draw a recording inside."""
    _cache(api, "one", NOW, minutes=30)
    _cache(api, "two", NOW + timedelta(minutes=30), minutes=30, title="Something else")
    _record(api, "m-either", NOW + timedelta(minutes=15), NOW + timedelta(minutes=45))

    assert all(event["meeting_id"] is None for event in _events(api, NOW))
