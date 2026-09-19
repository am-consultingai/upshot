"""Calendar 2, 4, 6 and 7, end to end against a fake Google.

Calendar 2 — read and cache events (z8tj1h8jrh)
Calendar 4 — name the recording and know who was there (z8tj1h8jrk)
Calendar 6 — the detector uses the calendar (z8tj1h8jrn)
Calendar 7 — privacy, and saying what is kept (z8tj1h8jrp)

Google is faked at the HTTP transport: the real token handling, the real sync, the real
cache and the real matching all run.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from app.clock import FakeClock, iso
from app.config import FakeKeyring
from app.db.dao import Dao, connect
from app.detect.evidence import CALENDAR
from app.gcal.events import EventStore, name_from_email, parse
from app.gcal.oauth import REFRESH_SECRET, CalendarAuth
from app.gcal.source import CalendarNow, GoogleCalendarSource
from app.gcal.sync import CalendarSync
from app.meetings import MeetingService
from app.pipeline.queue import JobQueue
from tests.unit.test_gcal_oauth import CLIENT

NOW = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)  # 12:00 in Jerusalem


def g_event(
    event_id: str,
    start: datetime,
    minutes: int = 30,
    *,
    title: str | None = None,
    attendees: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """An events.list item the way Google sends it."""
    return {
        "id": event_id,
        "status": "confirmed",
        "summary": title or event_id,
        "iCalUID": f"{event_id}@google.com",
        "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Jerusalem"},
        "end": {"dateTime": (start + timedelta(minutes=minutes)).isoformat()},
        "attendees": attendees
        if attendees is not None
        else [
            {"email": "me@example.com", "self": True, "responseStatus": "accepted"},
            {"email": "dana.levi@example.com", "responseStatus": "accepted"},
            {"email": "yossi@example.com", "displayName": "יוסי כהן", "responseStatus": "accepted"},
        ],
        "hangoutLink": "https://meet.google.com/abc-defg-hij",
        "description": "Dial-in PIN: 445 221 9981 — agenda: salaries for Q4",
        "updated": "2026-09-20T10:00:00.000Z",
        **extra,
    }


class FakeCalendarApi:
    """Token refresh plus events.list over a list of items the test controls."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []
        self.offline = False
        self.revoked = False
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.offline:
            raise httpx.ConnectError("offline", request=request)
        url = str(request.url)
        if url.startswith("https://oauth2.googleapis.com/token"):
            if self.revoked:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": "a", "expires_in": 3600})
        if url.startswith("https://oauth2.googleapis.com/revoke"):
            return httpx.Response(200)
        if "/events" in url:
            query = parse_qs(request.url.query.decode())
            lo, hi = query["timeMin"][0], query["timeMax"][0]
            assert query["singleEvents"] == ["true"]
            inside = [
                item
                for item in self.items
                if _utc(item["start"]) < _parse(hi) and _utc(item["end"]) > _parse(lo)
            ]
            return httpx.Response(200, json={"summary": "me@example.com", "items": inside})
        return httpx.Response(404)


def _parse(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _utc(node: dict[str, Any]) -> datetime:
    if "dateTime" in node:
        return _parse(node["dateTime"])
    return datetime.fromisoformat(node["date"]).replace(tzinfo=UTC)


class World:
    def __init__(self, tmp_path: Path) -> None:
        from app.config import default_config

        self.config = default_config(
            asr__backend="fake",
            llm__provider="fake",
            audio__vad="energy",
            secrets__backend="memory",
            audio__min_meeting_s=60,
        )
        self.config.set("data_root", str(tmp_path / "meetings"))
        self.conn = connect(tmp_path / "index.db")
        self.clock = FakeClock(start=NOW)
        self.dao = Dao(self.conn, self.clock)
        self.api = FakeCalendarApi()
        secrets = FakeKeyring()
        secrets.set(REFRESH_SECRET, "r-1")
        self.secrets = secrets
        self.auth = CalendarAuth(
            secrets,
            client_loader=lambda: CLIENT,
            http=httpx.Client(transport=httpx.MockTransport(self.api.handler)),
            monotonic=self.clock.monotonic,
        )
        self.store = EventStore(self.conn)
        self.sync = CalendarSync(self.auth, self.store, clock=self.clock)
        self.source = GoogleCalendarSource(self.store, available=self.auth.connected)
        self.meetings = MeetingService(
            self.config,
            self.dao,
            JobQueue(self.conn, self.clock),
            clock=self.clock,
            source=self.source,
        )
        self.sync.on_synced = self.meetings.rematch_recent

    def synced(self) -> None:
        self.sync.kick()
        assert self.sync.tick(), self.sync.last_error


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


# =========================================================================== Calendar 2


def test_first_sync_fills_the_cache(world: World) -> None:
    world.api.items = [g_event("standup", NOW), g_event("later", NOW + timedelta(days=3))]
    world.synced()
    assert {e.event_id for e in world.store.between(NOW, NOW + timedelta(days=5))} == {
        "standup",
        "later",
    }
    assert world.sync.status()["last_synced_at"].startswith("2026-09-21T09:00")


def test_a_deleted_event_leaves_the_cache_on_the_next_sync(world: World) -> None:
    world.api.items = [g_event("standup", NOW), g_event("review", NOW + timedelta(hours=1))]
    world.synced()
    world.api.items = [g_event("standup", NOW)]
    world.synced()
    assert [e.event_id for e in world.store.between(NOW, NOW + timedelta(hours=3))] == ["standup"]


def test_offline_keeps_the_cache_and_says_when_it_last_synced(world: World) -> None:
    world.api.items = [g_event("standup", NOW)]
    world.synced()
    world.api.offline = True
    world.clock.advance(120)
    world.sync.kick()
    assert world.sync.tick() is False
    status = world.sync.status()
    assert status["cached_events"] == 1, "the cache survives a failed sync intact"
    assert "could not be reached" in status["sync_error"]
    assert status["last_synced_at"] is not None


def test_a_revoked_token_stops_syncing(world: World) -> None:
    world.api.revoked = True
    world.sync.kick()
    assert world.sync.tick() is False
    before = len(world.api.requests)
    world.clock.advance(600)
    world.sync.kick()
    assert world.sync.tick() is False
    assert len(world.api.requests) == before, "nothing is sent until the user reconnects"


def test_timezones_all_day_and_recurrence(world: World) -> None:
    world.api.items = [
        # 09:00 in New York is 13:00 UTC, whatever the machine's zone.
        g_event("ny", datetime.fromisoformat("2026-09-21T09:00:00-04:00")),
        {
            **g_event("holiday", NOW),
            "start": {"date": "2026-09-22"},
            "end": {"date": "2026-09-23"},
        },
        g_event(
            "standup_20260921T090000Z",
            NOW,
            recurringEventId="standup",
            originalStartTime={"dateTime": NOW.isoformat()},
            iCalUID="standup@google.com",
        ),
    ]
    world.synced()
    by_id = {
        e.event_id: e for e in world.store.between(NOW - timedelta(days=1), NOW + timedelta(days=5))
    }
    assert by_id["ny"].start == datetime(2026, 9, 21, 13, 0, tzinfo=UTC)
    assert by_id["holiday"].all_day and by_id["holiday"].end - by_id["holiday"].start == timedelta(
        days=1
    )
    assert by_id["standup_20260921T090000Z"].recurring_event_id == "standup"


# =========================================================================== Calendar 7


def test_no_description_and_no_email_is_ever_stored(world: World) -> None:
    world.api.items = [g_event("standup", NOW)]
    world.synced()
    dump = "\n".join(str(tuple(row)) for row in world.conn.execute("SELECT * FROM calendar_events"))
    assert "@" not in dump.replace("@google.com", ""), "no attendee address in the cache"
    assert "PIN" not in dump and "salaries" not in dump, "the description is never kept"
    event = world.store.get("primary", "standup")
    assert event is not None
    assert [a.name for a in event.others] == ["Dana Levi", "יוסי כהן"]


def test_names_are_made_from_addresses_without_keeping_them() -> None:
    assert name_from_email("jane.doe@x.com") == "Jane Doe"
    assert name_from_email("j_smith-2@x.com") == "J Smith"
    parsed = parse(
        g_event(
            "x",
            NOW,
            attendees=[
                {"email": "room-4@resource.calendar.google.com", "resource": True},
                {"email": "gone@example.com", "responseStatus": "declined"},
                {"email": "me@example.com", "self": True, "responseStatus": "accepted"},
            ],
        ),
        "primary",
    )
    assert parsed is not None
    assert parsed.attendee_count == 2, "a meeting room is not an attendee"
    assert parsed.others == [], "declined people were not there"


def test_zoom_links_are_found_in_the_location(world: World) -> None:
    item = g_event("zoom", NOW, location="Zoom: https://us02web.zoom.us/j/123456?pwd=abc")
    item.pop("hangoutLink")
    parsed = parse(item, "primary")
    assert parsed is not None and parsed.conference_url is not None
    assert parsed.conference_url.startswith("https://us02web.zoom.us/j/123456")


def test_disconnect_forgets_the_cache(world: World) -> None:
    world.api.items = [g_event("standup", NOW)]
    world.synced()
    world.auth.disconnect()
    world.sync.forget()
    assert world.store.count() == 0
    assert world.secrets.get(REFRESH_SECRET) is None


# =========================================================================== Calendar 4


def test_a_recording_takes_the_meetings_name_and_attendees(world: World) -> None:
    world.api.items = [g_event("design", NOW, title="Design review")]
    world.synced()
    world.clock.set(NOW + timedelta(minutes=2))
    meeting = world.meetings.create(source="manual")
    assert meeting.title == "Design review"
    assert meeting.title_source == "calendar"
    assert world.meetings.participants(meeting) == ("Dana Levi", "יוסי כהן")
    payload = json.loads(meeting.calendar_json or "{}")
    assert payload["event"]["event_id"] == "design"
    assert payload["conference_url"] == "https://meet.google.com/abc-defg-hij"
    assert payload["match"] == {
        "state": "matched",
        "source": "auto",
        "confidence": 1.0,
        "reason": "",
    }


@pytest.mark.parametrize(
    ("existing_title", "existing_source", "expected_title", "expected_source"),
    [
        (None, None, "Design review", "calendar"),  # nothing yet
        ("Zoom Meeting", "window", "Design review", "calendar"),  # calendar beats window
        ("A model's guess", "llm", "Design review", "calendar"),  # calendar beats model
        ("Budget with Dana", "user", "Budget with Dana", "user"),  # the user beats all
        ("Typed at start", None, "Typed at start", None),  # a title given without a source
    ],
)
def test_title_precedence(
    world: World,
    existing_title: str | None,
    existing_source: str | None,
    expected_title: str,
    expected_source: str | None,
) -> None:
    world.api.items = [g_event("design", NOW, title="Design review")]
    world.synced()
    meeting = world.dao.insert_meeting(
        meeting_id="m1",
        folder=world.config.data_root / "m1",
        source="detected",
        state="RECORDED",
        started_at=NOW,
        title=existing_title,
        title_source=existing_source,
        profile="cpu-deferred",
    )
    world.dao.update_meeting(meeting.id, ended_at=iso(NOW + timedelta(minutes=30)))
    after = world.meetings.rematch(meeting.id)
    assert (after.title, after.title_source) == (expected_title, expected_source)


def test_the_real_end_time_settles_a_back_to_back_start(world: World) -> None:
    """Started a minute before the second of two meetings, but ran through it: at the
    start that looked ambiguous; once finished, it is plainly the second meeting."""
    world.api.items = [
        g_event("first", NOW - timedelta(minutes=30), title="First"),
        g_event("second", NOW, 60, title="Second"),
    ]
    world.synced()
    world.clock.set(NOW - timedelta(minutes=1))
    meeting = world.meetings.create(source="manual")
    world.clock.set(NOW + timedelta(minutes=55))
    done = world.meetings.finish(meeting.id, duration_s=56 * 60)
    assert world.dao.require_meeting(done.id).title == "Second"


def test_no_match_is_said_plainly_and_invents_nothing(world: World) -> None:
    world.api.items = [g_event("tomorrow", NOW + timedelta(days=1))]
    world.synced()
    meeting = world.meetings.create(source="manual")
    assert meeting.title is None
    assert json.loads(meeting.calendar_json or "{}")["match"]["state"] == "none"


def test_an_event_that_arrives_later_is_matched_on_the_next_sync(world: World) -> None:
    world.synced()  # nothing on the calendar yet
    meeting = world.meetings.create(source="manual")
    world.clock.set(NOW + timedelta(minutes=30))
    world.meetings.finish(meeting.id, duration_s=30 * 60)
    world.api.items = [g_event("added", NOW, title="Added afterwards")]
    world.clock.advance(61)
    world.synced()
    assert world.dao.require_meeting(meeting.id).title == "Added afterwards"


def test_a_user_choice_is_never_undone(world: World) -> None:
    from app.gcal.source import snapshot

    world.api.items = [g_event("a", NOW, title="A"), g_event("b", NOW, title="B")]
    world.synced()
    meeting = world.meetings.create(source="manual")
    event_b = world.store.get("primary", "b")
    assert event_b is not None
    world.meetings.choose_event(meeting.id, snapshot(event_b, state="matched", source="user"))
    world.clock.set(NOW + timedelta(minutes=30))
    world.meetings.finish(meeting.id, duration_s=30 * 60)
    world.meetings.rematch_recent()
    assert world.dao.require_meeting(meeting.id).title == "B"


def test_a_slow_calendar_never_delays_a_meeting(world: World) -> None:
    import time

    class Slow:
        name = "slow"

        def for_meeting(self, started_at: datetime, ended_at: datetime | None) -> None:
            time.sleep(5)

    world.meetings.enrichment_source = Slow()  # type: ignore[assignment]
    started = time.monotonic()
    meeting = world.meetings.create(source="manual")
    assert time.monotonic() - started < 2.5
    assert meeting.calendar_json is None


# =========================================================================== the prompt


def _summarize_prompt(world: World, tmp_path: Path, **config: Any) -> str:
    from app.llm.client import FakeLlm
    from app.pipeline.stages import assemble, summarize, transcribe
    from app.pipeline.states import JobStage
    from tests.fixtures.meetings import write_chunks

    for key, value in config.items():
        world.config.set(key, value)
    world.api.items = [g_event("design", NOW, title="Design review")]
    world.synced()
    meeting = world.meetings.create(source="manual")
    write_chunks(meeting.path, seconds=90)

    class Svc:
        def __init__(self) -> None:
            from app.asr.fake import FakeAsr

            self.asr = FakeAsr()
            self.llm = FakeLlm()

    from app.pipeline.context import StageContext

    svc = Svc()
    queue = JobQueue(world.conn, world.clock)

    def ctx(stage: str) -> Any:
        return StageContext(
            meeting=world.dao.require_meeting(meeting.id),
            dao=world.dao,
            queue=queue,
            config=world.config,
            clock=world.clock,
            job=queue.enqueue(meeting.id, stage),
            services=svc,
        )

    transcribe.run(ctx(JobStage.TRANSCRIBE))
    assemble.run(ctx(JobStage.ASSEMBLE))
    summarize.run(ctx(JobStage.SUMMARIZE))
    return "\n".join(call["user"] for call in svc.llm.calls)


def test_the_title_reaches_the_prompt_and_names_only_when_allowed(
    world: World, tmp_path: Path
) -> None:
    prompt = _summarize_prompt(world, tmp_path)
    assert "Title: Design review" in prompt
    assert "Dana Levi" not in prompt, "attendee names are asked for before they are sent"


def test_attendee_names_reach_the_prompt_once_allowed_and_never_an_address(
    world: World, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    prompt = _summarize_prompt(world, tmp_path, **{"calendar.prompt_attendees": True})
    assert "Dana Levi" in prompt and "יוסי כהן" in prompt
    for text in (prompt, caplog.text):
        assert "@example.com" not in text, "no attendee address in a prompt or a log line"
        assert "PIN" not in text and "salaries" not in text, "never the description"
        assert "meet.google.com" not in text, "never the meeting link"


def test_a_private_event_sends_nothing_to_the_model(world: World, tmp_path: Path) -> None:
    world.api.items = []
    prompt = _summarize_prompt(world, tmp_path, **{"calendar.prompt_attendees": True})
    assert "Design review" in prompt  # sanity: the ordinary event does reach it
    world2 = World(tmp_path / "private")
    world2.api.items = [g_event("design", NOW, title="Design review", visibility="private")]
    world2.synced()
    meeting = world2.meetings.create(source="manual")
    from app.pipeline.stages.summarize import meeting_context

    class Ctx:
        config = world2.config

    Ctx.meeting = meeting  # type: ignore[attr-defined]
    assert meeting_context(Ctx) is None  # type: ignore[arg-type]


# =========================================================================== Calendar 6


def _detector(tmp_path: Path, world: World) -> Any:
    from tests.integration.test_phase12_detector import build

    h = build(tmp_path / "det")
    h.clock.set(NOW)
    h.detector.calendar = CalendarNow(world.store, available=lambda: True)
    h.detector.meetings.enrichment_source = world.source
    return h


def test_event_plus_known_app_commits_and_scores_exactly(world: World, tmp_path: Path) -> None:
    world.api.items = [g_event("design", NOW, title="Design review")]
    world.synced()
    h = _detector(tmp_path, world)
    h.mic.hold("Zoom.exe")  # 3, plus the calendar's 3: past the threshold of 5 at once
    h.seconds(int(h.detector.sustain_s) + 1)
    meetings = h.dao.list_meetings()
    assert len(meetings) == 1
    evidence = {item["code"]: item for item in meetings[0].evidence}
    assert evidence[CALENDAR]["detail"] == "Design review is on your calendar"
    assert h.detector.score(h.detector.collect("Zoom.exe")) == 6


def test_an_event_alone_never_starts_a_recording_but_does_nudge(
    world: World, tmp_path: Path
) -> None:
    world.api.items = [g_event("design", NOW + timedelta(minutes=1), title="Design review")]
    world.synced()
    h = _detector(tmp_path, world)
    published: list[tuple[str, dict[str, Any]]] = []
    h.detector._publish = lambda state, **payload: published.append((state, payload))  # type: ignore[method-assign]
    h.seconds(180, audio=False)
    assert h.dao.list_meetings() == [], "a blocked-out hour is not a call"
    assert h.recorder.armed is False
    upcoming = [p for s, p in published if s == "upcoming"]
    assert len(upcoming) == 1, "said once, not once a second"
    assert upcoming[0]["event"] == "Design review"
    assert any("Design review is starting" in t.title for t in h.notifier.shown)


def test_a_declined_event_contributes_nothing(world: World, tmp_path: Path) -> None:
    world.api.items = [
        g_event(
            "design",
            NOW,
            attendees=[
                {"email": "me@example.com", "self": True, "responseStatus": "declined"},
                {"email": "dana@example.com", "responseStatus": "accepted"},
            ],
        )
    ]
    world.synced()
    h = _detector(tmp_path, world)
    assert not any(e.code == CALENDAR for e in h.detector.collect("Zoom.exe"))
