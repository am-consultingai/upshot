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
from tests.fixtures.api import build_harness, seed_calendar_account


def data(text: str) -> Any:
    assert text.startswith(DATA_OPEN) and text.endswith(DATA_CLOSE), "marked as data"
    return json.loads(text[len(DATA_OPEN) : -len(DATA_CLOSE)])


@pytest.fixture
def tools(tmp_path: Path, app_home: Path) -> AssistantTools:
    h = build_harness(tmp_path)
    account = seed_calendar_account(h.services)
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
        # The shape the matcher writes: the event reference is under "event".
        calendar_json=json.dumps(
            {
                "participants": ["Dana Levi", "Ron Katz"],
                "event": {"account_id": account, "calendar_id": "c", "event_id": "e1"},
                "accounts": [account],
                "match": {"state": "matched", "source": "auto"},
            }
        ),
    )
    dao.set_calendar_accounts("m-q4", account, [account])
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
        account,
        "c",
        datetime(2026, 9, 20, tzinfo=UTC),
        datetime(2026, 9, 21, tzinfo=UTC),
        [
            CalendarEvent(
                account_id=account,
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


# ----------------------------------------------------------------------- D98: sizes, summaries


def test_every_list_stops_at_the_cap_and_says_how_to_see_the_rest(tools: AssistantTools) -> None:
    from app.assistant.tools import fit

    rows = [{"what": f"item {i} " + "x" * 200} for i in range(500)]
    out = fit({"count": 500, "action_items": rows}, "action_items", "filter by person")
    size = len(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    assert size <= MAX_CHARS
    shown = len(out["action_items"])
    assert 0 < shown < 500 and out["action_items"] == rows[:shown]
    assert out["truncated"] == f"{500 - shown} more not shown: filter by person"
    small = {"count": 1, "action_items": rows[:1]}
    assert fit(small, "action_items", "x") == small, "what fits is left alone"


def test_search_results_fit_the_cap(tools: AssistantTools) -> None:
    dao = tools.svc.dao
    dao.index_turns(
        "m-1on1",
        [Turn(i, "ME", i * 1000, "zebra " + "word " * 400) for i in range(60)],
    )
    found = tools.search("zebra", limit=50)
    assert len(found) <= MAX_CHARS + 200
    assert "truncated" in data(found)


def test_get_summary_returns_all_of_a_long_summary(tools: AssistantTools) -> None:
    whole = "".join(f"Point {i}: the budget moves. " for i in range(3000))
    tools.svc.dao.index_summary("m-q4", whole)
    meeting = data(tools.get_meeting("m-q4"))
    assert meeting["summary_truncated"] is True and "get_summary" in meeting["summary_rest"]
    parts = []
    part = 1
    while True:
        got = data(tools.get_summary("m-q4", part))
        parts.append(got["summary"])
        if "next" not in got:
            break
        part += 1
    assert "".join(parts) == whole.strip()
    assert got["parts"] == part


def test_get_summary_of_a_meeting_without_one(tools: AssistantTools) -> None:
    empty = data(tools.get_summary("m-1on1"))
    assert empty["summary"] is None and empty["parts"] == 1 and empty["chapters"] == []
    assert "no summary" in empty["note"]
    assert "error" in data(tools.get_summary("nope"))


def test_get_summary_with_many_chapters_still_fits(tools: AssistantTools) -> None:
    meeting = tools.svc.dao.visible_meeting("m-q4")
    assert meeting is not None
    meeting.path.mkdir(parents=True, exist_ok=True)
    chapters = [{"title": f"Topic {i} " + "word " * 12, "start_ms": i * 60_000} for i in range(100)]
    (meeting.path / "notes.json").write_text(json.dumps({"chapters": chapters}), encoding="utf-8")
    whole = "".join(f"Point {i}: the budget moves. " for i in range(3000))
    tools.svc.dao.index_summary("m-q4", whole)
    parts, part = [], 1
    while True:
        reply = tools.get_summary("m-q4", part)
        assert len(reply) <= MAX_CHARS, f"part {part} is {len(reply)} characters"
        got = data(reply)
        parts.append(got["summary"])
        if "next" not in got:
            break
        part += 1
    assert (
        "".join(parts) == whole.strip() and len(data(tools.get_summary("m-q4"))["chapters"]) == 100
    )
