"""Hiding a calendar account hides everything of it, everywhere, and deletes nothing (D82).

Two accounts, a work one and a personal one, each with a recorded meeting that has a
transcript, a summary, an action item and a tag. The personal account is hidden: every
screen, every API answer and every assistant tool behaves as if its meeting did not
exist. Shown again, it is all back, and the files on disk never changed.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.assistant.citations import Citer
from app.assistant.tools import DATA_CLOSE, DATA_OPEN, AssistantTools
from app.db.dao import Turn
from app.gcal.events import CalendarEvent, EventStore
from app.pipeline.states import MeetingState
from tests.fixtures.api import build_harness, seed_calendar_account

START = datetime(2026, 9, 20, 9, 0, tzinfo=UTC)


def data(text: str) -> Any:
    return json.loads(text[len(DATA_OPEN) : -len(DATA_CLOSE)])


class Library:
    def __init__(self, tmp_path: Path) -> None:
        self.h = build_harness(tmp_path)
        svc = self.h.services
        self.work = seed_calendar_account(svc, "work@example.com")
        self.personal = seed_calendar_account(svc, "me@example.com")
        self.client = self.h.client()
        self.m_work = self._meeting("m-work", self.work, "work", START)
        self.m_personal = self._meeting(
            "m-personal", self.personal, "personal", START + timedelta(hours=3)
        )

    def _meeting(self, meeting_id: str, account: str, word: str, start: datetime) -> str:
        svc = self.h.services
        folder = svc.config.data_root / meeting_id
        svc.dao.insert_meeting(
            meeting_id=meeting_id,
            folder=folder,
            source="manual",
            state=MeetingState.RECORDING,
            profile="cpu-deferred",
            title=f"Roadmap {word}",
            started_at=start.isoformat(),
        )
        svc.dao.update_meeting(meeting_id, ended_at=(start + timedelta(minutes=30)).isoformat())
        svc.dao.set_state(meeting_id, MeetingState.RECORDED)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "transcript.json").write_text(json.dumps({"segments": []}), encoding="utf-8")
        (folder / "notes.json").write_text(json.dumps({"title": word}), encoding="utf-8")
        (folder / "summary.html").write_text(f"<p>roadmap {word}</p>", encoding="utf-8")
        svc.dao.index_turns(meeting_id, [Turn(0, "THEM", 0, f"roadmap {word} secret")])
        svc.dao.index_summary(meeting_id, f"roadmap summary {word}")
        svc.dao.add_action_item(meeting_id, what=f"roadmap action {word}")
        svc.dao.set_tags(meeting_id, [f"tag-{word}"])
        event = CalendarEvent(
            account_id=account,
            calendar_id="primary",
            event_id=f"ev-{word}",
            title=f"Roadmap {word}",
            start=start,
            end=start + timedelta(minutes=30),
            ical_uid=f"ev-{word}@x",
        )
        EventStore(svc.conn).replace_window(
            account, "primary", start - timedelta(hours=1), start + timedelta(hours=1), [event],
            synced_at=START,
        )  # fmt: skip
        from app.gcal.source import snapshot

        svc.meetings.choose_event(
            meeting_id, snapshot(event, state="matched", source="auto", confidence=1.0)
        )
        return meeting_id

    def hide(self, account: str, visible: bool = False) -> None:
        answer = self.client.patch(f"/api/calendar/accounts/{account}", json={"visible": visible})
        assert answer.status_code == 200, answer.text

    def folders_digest(self) -> str:
        digest = hashlib.sha256()
        root = self.h.services.config.data_root
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.name != "meta.json":
                digest.update(str(path.relative_to(root)).encode())
                digest.update(path.read_bytes())
        return digest.hexdigest()


@pytest.fixture
def lib(tmp_path: Path, app_home: Path) -> Library:
    return Library(tmp_path)


def _ids(rows: list[dict[str, Any]], key: str = "meeting_id") -> set[str]:
    return {str(row[key]) for row in rows}


def test_the_api_forgets_a_hidden_accounts_meeting(lib: Library) -> None:
    c = lib.client
    assert _ids(c.get("/api/meetings").json()["meetings"], "id") == {lib.m_work, lib.m_personal}
    before = lib.folders_digest()

    lib.hide(lib.personal)

    assert _ids(c.get("/api/meetings").json()["meetings"], "id") == {lib.m_work}
    assert _ids(c.get("/api/search", params={"q": "roadmap"}).json()["hits"]) == {lib.m_work}
    assert _ids(c.get("/api/action-items").json()["items"]) == {lib.m_work}
    assert {t["tag"] for t in c.get("/api/tags").json()["tags"]} == {"tag-work"}
    events = c.get("/api/calendar/events", params={"from": "2026-09-19", "to": "2026-09-22"})
    assert {e["event_id"] for e in events.json()["events"]} == {"ev-work"}

    hidden = lib.m_personal
    for path in (
        f"/api/meetings/{hidden}",
        f"/api/meetings/{hidden}/transcript",
        f"/api/meetings/{hidden}/notes",
        f"/api/meetings/{hidden}/summary.html",
        f"/api/meetings/{hidden}/related",
        f"/api/meetings/{hidden}/calendar",
        f"/api/meetings/{hidden}/invite",
    ):
        assert c.get(path).status_code == 404, path
    assert c.post(f"/api/meetings/{hidden}/ask", json={"question": "what?"}).status_code == 404
    assert c.put(f"/api/meetings/{hidden}/tags", json={"tags": ["x"]}).status_code == 404
    assert c.patch(f"/api/meetings/{hidden}", json={"title": "x"}).status_code == 404

    lib.hide(lib.personal, visible=True)
    assert _ids(c.get("/api/meetings").json()["meetings"], "id") == {lib.m_work, lib.m_personal}
    assert c.get(f"/api/meetings/{hidden}").status_code == 200
    assert lib.folders_digest() == before, "hiding and showing changed no file"


def test_attention_leaves_out_a_hidden_meeting(lib: Library) -> None:
    svc = lib.h.services
    for meeting_id in (lib.m_work, lib.m_personal):
        svc.queue.enqueue(meeting_id, "transcribe")
    svc.conn.execute("UPDATE jobs SET state = 'failed', last_error = 'x'")
    lib.hide(lib.personal)
    items = lib.client.get("/api/attention").json()["items"]
    assert _ids(items) == {lib.m_work}


def test_the_assistant_cannot_see_a_hidden_accounts_meeting(lib: Library) -> None:
    tools = AssistantTools(lib.h.services)
    lib.hide(lib.personal)
    assert _ids(data(tools.list_meetings())["meetings"]) == {lib.m_work}
    assert _ids(data(tools.search("roadmap"))["results"]) == {lib.m_work}
    assert _ids(data(tools.list_action_items())["action_items"]) == {lib.m_work}
    assert "error" in data(tools.get_meeting(lib.m_personal))
    assert "error" in data(tools.get_transcript(lib.m_personal))
    assert "error" in data(tools.related_meetings(lib.m_personal))
    day = data(tools.calendar_range("2026-09-20"))
    assert {e["title"] for e in day["events"]} == {"Roadmap work"}
    citer = Citer(lib.h.services.dao)
    shown, cited = citer.feed(f"see [[m:{lib.m_personal}@0]] and [[m:{lib.m_work}@0]] more")
    assert [c["meeting_id"] for c in cited] == [lib.m_work]
    assert lib.m_personal not in shown


def test_the_assistant_context_leaves_a_hidden_meeting_out(lib: Library) -> None:
    from app.assistant.antigravity_route import gather

    lib.hide(lib.personal)
    context = gather(lib.h.services, "roadmap secret", {"meeting_id": lib.m_personal})
    assert "personal secret" not in context
    assert "work secret" in context


def test_the_library_filter(lib: Library) -> None:
    c = lib.client
    plain = lib.h.services.dao.insert_meeting(
        meeting_id="m-plain",
        folder=lib.h.services.config.data_root / "m-plain",
        source="manual",
        title="No calendar",
        started_at=(START + timedelta(days=1)).isoformat(),
    ).id

    def listed(*accounts: str) -> set[str]:
        answer = c.get("/api/meetings", params=[("account", a) for a in accounts])
        return _ids(answer.json()["meetings"], "id")

    assert listed() == {lib.m_work, lib.m_personal, plain}
    assert listed(lib.work) == {lib.m_work}
    assert listed("none") == {plain}
    assert listed(lib.personal, "none") == {lib.m_personal, plain}
    rows = {m["id"]: m for m in c.get("/api/meetings").json()["meetings"]}
    assert rows[lib.m_work]["calendar_accounts"] == [lib.work]
    assert rows[plain]["calendar_accounts"] == []
    lib.hide(lib.personal)
    assert listed(lib.personal) == set(), "a filter never shows a hidden account"


def test_a_removed_account_is_permanently_hidden_until_it_connects_again(lib: Library) -> None:
    c = lib.client
    answer = c.delete(f"/api/calendar/accounts/{lib.personal}")
    assert answer.status_code == 200
    assert [a["id"] for a in answer.json()["accounts"]] == [lib.work]
    assert c.get(f"/api/meetings/{lib.m_personal}").status_code == 404
    kept = lib.h.services.dao.get_meeting(lib.m_personal)
    assert kept is not None, "in the database still"
    lib.h.services.calendar.registry.add_or_restore("me@example.com")
    assert c.get(f"/api/meetings/{lib.m_personal}").status_code == 200


def test_the_invitation_of_a_removed_account_says_so(lib: Library) -> None:
    """A meeting on both accounts stays shown when one is removed; its invitation, on
    the removed account, is not asked for."""
    svc = lib.h.services
    svc.dao.set_calendar_accounts(lib.m_personal, lib.personal, [lib.personal, lib.work])
    from app.gcal.invite import InviteReader

    svc.calendar_invites = InviteReader(svc.calendar)
    lib.client.delete(f"/api/calendar/accounts/{lib.personal}")
    answer = lib.client.get(f"/api/meetings/{lib.m_personal}/invite").json()
    assert answer["code"] == "account_gone"
