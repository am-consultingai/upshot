"""The assistant's read-only tools, on a seeded library."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.assistant.tools import DATA_CLOSE, DATA_OPEN, MAX_CHARS, AssistantTools
from app.db.dao import Turn
from app.gcal.events import Attendee, CalendarEvent, EventStore
from app.pipeline.states import MeetingState
from tests.fixtures.api import build_harness


def data(text: str) -> Any:
    assert text.startswith(DATA_OPEN) and text.endswith(DATA_CLOSE), "marked as data"
    return json.loads(text[len(DATA_OPEN) : -len(DATA_CLOSE)])


@pytest.fixture
def tools(tmp_path: Path, app_home: Path) -> AssistantTools:
    h = build_harness(tmp_path)
    dao = h.services.dao
    for meeting_id, title, started in (
        ("m-q4", "Q4 budget review", "2026-09-20T09:30:00Z"),
        ("m-1on1", "1:1 with Dana", "2026-09-22T13:00:00Z"),
    ):
        dao.insert_meeting(
            meeting_id=meeting_id,
            folder=h.services.config.data_root / meeting_id,
            source="manual",
            state=MeetingState.RECORDING,
            profile="cpu-deferred",
            title=title,
            started_at=started,
        )
    dao.update_meeting(
        "m-q4",
        duration_s=2520,
        calendar_json=json.dumps(
            {"participants": ["Dana Levi", "Ron Katz"], "calendar_id": "c", "event_id": "e1"}
        ),
    )
    dao.set_tags("m-q4", ["finance"])
    dao.index_turns(
        "m-q4",
        [
            Turn(i, "THEM" if i % 2 else "ME", i * 1000, f"line {i} about the budget")
            for i in range(300)
        ],
    )
    dao.index_summary("m-q4", "The marketing budget is cut by ten percent.")
    dao.replace_action_items(
        "m-q4",
        [
            {"who": "ME", "what": "send the revised plan", "due": None, "at_ms": 5000},
            {"who": "Dana", "what": "rerun the forecast", "due": None, "at_ms": 7000},
        ],
    )
    EventStore(h.services.conn).replace_window(
        "c",
        datetime(2026, 9, 20, tzinfo=UTC),
        datetime(2026, 9, 21, tzinfo=UTC),
        [
            CalendarEvent(
                calendar_id="c",
                event_id="e1",
                title="Q4 budget review",
                start=datetime(2026, 9, 20, 9, 30, tzinfo=UTC),
                end=datetime(2026, 9, 20, 10, 15, tzinfo=UTC),
                attendees=(Attendee("Dana Levi"), Attendee("Gone", declined=True)),
            )
        ],
        synced_at=datetime(2026, 9, 20, tzinfo=UTC),
    )
    return AssistantTools(h.services)


def test_list_meetings_by_date_and_words(tools: AssistantTools) -> None:
    everything = data(tools.list_meetings())
    assert [m["meeting_id"] for m in everything["meetings"]] == ["m-1on1", "m-q4"]
    q4 = everything["meetings"][1]
    assert q4["participants"] == ["Dana Levi", "Ron Katz"] and q4["tags"] == ["finance"]
    assert q4["duration_min"] == 42
    assert [
        m["meeting_id"] for m in data(tools.list_meetings(date_from="2026-09-21"))["meetings"]
    ] == ["m-1on1"]
    assert [m["meeting_id"] for m in data(tools.list_meetings(contains="budget"))["meetings"]] == [
        "m-q4"
    ]


def test_get_meeting_has_the_summary_and_action_items(tools: AssistantTools) -> None:
    meeting = data(tools.get_meeting("m-q4"))
    assert meeting["summary"] == "The marketing budget is cut by ten percent."
    assert [(a["who"], a["what"], a["done"]) for a in meeting["action_items"]] == [
        ("ME", "send the revised plan", False),
        ("Dana", "rerun the forecast", False),
    ]
    assert meeting["transcript_lines"] == 300
    assert "error" in data(tools.get_meeting("nope"))


def test_a_long_transcript_comes_in_parts_that_say_where_to_continue(tools: AssistantTools) -> None:
    first = data(tools.get_transcript("m-q4"))
    assert first["lines"][0] == {
        "at_ms": 0,
        "time": "0:00",
        "speaker": "ME",
        "text": "line 0 about the budget",
    }
    assert "truncated" in first
    assert len(tools.get_transcript("m-q4")) <= MAX_CHARS + 2000
    next_from = int(first["truncated"].split("from_ms=")[1].rstrip("."))
    second = data(tools.get_transcript("m-q4", from_ms=next_from))
    assert second["lines"][0]["at_ms"] == next_from
    window = data(tools.get_transcript("m-q4", from_ms=10_000, to_ms=12_000))
    assert [line["at_ms"] for line in window["lines"]] == [10_000, 11_000, 12_000]
    assert "truncated" not in window


def test_action_items_by_person_and_for_the_user(tools: AssistantTools) -> None:
    everyone = data(tools.list_action_items())
    assert everyone["count"] == 2
    mine = data(tools.list_action_items(person="me"))
    assert [a["what"] for a in mine["action_items"]] == ["send the revised plan"]
    assert mine["action_items"][0]["who"] == "the user"
    assert [a["what"] for a in data(tools.list_action_items(person="dana"))["action_items"]] == [
        "rerun the forecast"
    ]


def test_calendar_range_says_which_events_were_recorded(tools: AssistantTools) -> None:
    day = data(tools.calendar_range("2026-09-20"))
    assert day["count"] == 1
    event = day["events"][0]
    assert event["recorded_meeting_id"] == "m-q4"
    assert event["attendees"] == ["Dana Levi"], "declined attendees are left out"
    assert data(tools.calendar_range("2026-09-25"))["count"] == 0
    assert "error" in data(tools.calendar_range("next tuesday"))


def test_related_meetings_and_a_missing_meeting(tools: AssistantTools) -> None:
    assert data(tools.related_meetings("m-q4"))["meeting_id"] == "m-q4"
    assert "error" in data(tools.related_meetings("nope"))
    assert "error" in data(tools.get_transcript("nope"))


def test_search_names_the_kind_and_only_transcripts_have_a_moment(tools: AssistantTools) -> None:
    hits = data(tools.search("marketing"))["results"]
    assert [(h["kind"], h["at_ms"]) for h in hits] == [("summary", None)]
