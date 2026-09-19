"""Calendar 5: the matching table (z8tj1h8jrm).

Each case states the recording, the events around it, the expected event and the expected
verdict. The times are one morning in Jerusalem, written as UTC instants.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.gcal.events import Attendee, CalendarEvent
from app.gcal.match import MATCHED, NONE, PROPOSED, match

DANA = (Attendee("Dana Levi"), Attendee("Me", self=True))


def at(hhmm: str) -> datetime:
    hour, minute = hhmm.split(":")
    return datetime(2026, 9, 21, int(hour), int(minute), tzinfo=UTC)


def event(
    event_id: str,
    start: str,
    end: str,
    *,
    calendar: str = "primary",
    attendees: tuple[Attendee, ...] = DANA,
    link: str | None = "https://meet.google.com/abc-defg-hij",
    **extra: object,
) -> CalendarEvent:
    return CalendarEvent(
        calendar_id=calendar,
        event_id=event_id,
        title=event_id,
        start=at(start),
        end=at(end),
        ical_uid=extra.pop("ical_uid", f"{event_id}@google.com"),  # type: ignore[arg-type]
        attendees=attendees,
        conference_url=link,
        response=extra.pop("response", "accepted"),  # type: ignore[arg-type]
        **extra,  # type: ignore[arg-type]
    )


CASES = [
    # name, recording, events, expected state, expected event
    ("exact match", ("09:00", "09:30"), [event("standup", "09:00", "09:30")], MATCHED, "standup"),
    ("late start", ("09:05", "09:30"), [event("standup", "09:00", "09:30")], MATCHED, "standup"),
    ("overrun", ("09:00", "09:45"), [event("standup", "09:00", "09:30")], MATCHED, "standup"),
    (
        "late and over",
        ("09:05", "09:40"),
        [event("standup", "09:00", "09:30")],
        MATCHED,
        "standup",
    ),
    (
        "back-to-back, spans both: ambiguous, said so",
        ("09:00", "10:00"),
        [event("a", "09:00", "09:30"), event("b", "09:30", "10:00")],
        PROPOSED,
        "a",
    ),
    (
        "back-to-back, mostly the second",
        ("09:28", "10:00"),
        [event("a", "09:00", "09:30"), event("b", "09:30", "10:00")],
        MATCHED,
        "b",
    ),
    (
        "nested: the outer meeting covers the recording",
        ("09:00", "10:00"),
        [event("outer", "09:00", "10:00"), event("inner", "09:15", "09:30")],
        MATCHED,
        "outer",
    ),
    (
        "declined",
        ("09:00", "09:30"),
        [event("x", "09:00", "09:30", response="declined")],
        NONE,
        None,
    ),
    (
        "all-day",
        ("09:00", "09:30"),
        [event("holiday", "00:00", "23:59", all_day=True)],
        NONE,
        None,
    ),
    ("none", ("09:00", "09:30"), [event("later", "14:00", "15:00")], NONE, None),
    (
        "barely touching is not a candidate",
        ("09:00", "10:00"),
        [event("brush", "09:55", "10:30")],
        NONE,
        None,
    ),
    (
        "the same meeting on two calendars counts once",
        ("09:00", "09:30"),
        [
            event("sync", "09:00", "09:30", calendar="work", ical_uid="u1", response=None),
            event("sync2", "09:00", "09:30", calendar="primary", ical_uid="u1"),
        ],
        MATCHED,
        "sync2",
    ),
    (
        "recurring instance",
        ("09:02", "09:31"),
        [
            event(
                "standup_20260921",
                "09:00",
                "09:30",
                ical_uid="series@google.com",
                recurring_event_id="standup",
                original_start="2026-09-21T12:00:00+03:00",
            )
        ],
        MATCHED,
        "standup_20260921",
    ),
    (
        "a solo block is only proposed",
        ("09:00", "09:30"),
        [event("focus", "09:00", "09:30", attendees=(), link=None)],
        PROPOSED,
        "focus",
    ),
    (
        "a soft hold is only proposed",
        ("09:00", "09:30"),
        [event("maybe", "09:00", "09:30", transparent=True)],
        PROPOSED,
        "maybe",
    ),
    (
        "joined late, but wholly inside the meeting",
        ("09:20", "09:50"),
        [event("long", "08:00", "10:00")],
        MATCHED,
        "long",
    ),
    (
        "starting far away and only half covered is proposed, not assumed",
        ("09:20", "10:40"),
        [event("long", "08:00", "10:00")],
        PROPOSED,
        "long",
    ),
    (
        "out of office is not a meeting",
        ("09:00", "09:30"),
        [event("ooo", "09:00", "09:30", event_type="outOfOffice")],
        NONE,
        None,
    ),
]


@pytest.mark.parametrize(
    ("name", "recording", "events", "state", "expected"), CASES, ids=[c[0] for c in CASES]
)
def test_matching_table(
    name: str,
    recording: tuple[str, str],
    events: list[CalendarEvent],
    state: str,
    expected: str | None,
) -> None:
    verdict = match(events, at(recording[0]), at(recording[1]))
    assert verdict.state == state, verdict.reason
    assert (verdict.best.event.event_id if verdict.best else None) == expected
    if state == MATCHED:
        assert verdict.confidence >= 0.5
    if state == PROPOSED:
        assert verdict.reason, "a proposal always says why it is not a match"


def test_at_the_start_a_recording_is_scored_as_if_it_ran_fifteen_minutes() -> None:
    joined_early = match([event("standup", "09:00", "09:30")], at("08:57"))
    assert joined_early.state == MATCHED
    assert joined_early.best and joined_early.best.event.event_id == "standup"


def test_dst_cannot_move_a_match() -> None:
    """Instants are compared, never wall-clock times: the same UTC instants match however
    the local offset changed in between."""
    shifted = CalendarEvent(
        calendar_id="primary",
        event_id="after-dst",
        title="after",
        start=datetime.fromisoformat("2026-10-25T09:00:00+02:00"),
        end=datetime.fromisoformat("2026-10-25T09:30:00+02:00"),
        attendees=DANA,
        conference_url="https://meet.google.com/x",
    )
    started = datetime.fromisoformat("2026-10-25T07:00:00+00:00")
    assert match([shifted], started, started.replace(minute=30)).state == MATCHED
