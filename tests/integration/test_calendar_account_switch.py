"""Switching Google accounts: what stays, what changes, and what goes wrong (epic
z8tj1hb9je, "Multiple calendars").

NOT SUPPORTED YET. Upshot does not support switching between Google accounts: the three
``xfail`` tests below are expected to fail until the epic is built, and the suite passes
because they do. Do not remove a marker to "fix" the suite; remove it only when the
behaviour it describes has been built and the test passes on its own.

The scenario the product owner asked for: connect account A, record a meeting, switch to
account B, then come back to A. Google is faked at the HTTP transport, as in
test_calendar_features; each account is its own list of events.

The passing tests pin what already holds. The ``xfail(strict=True)`` tests are the gaps
this check found: each states the behaviour we want, and fails until it is built, when
strict xfail turns the pass into a failure so the marker is removed.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.gcal.invite import InviteReader
from app.gcal.oauth import REFRESH_SECRET
from tests.integration.test_calendar_features import NOW, World, g_event

A_ATTENDEES: list[dict[str, Any]] = [
    {"email": "a@example.com", "self": True, "responseStatus": "accepted"},
    {"email": "dana@example.com", "responseStatus": "accepted"},
]
B_ATTENDEES: list[dict[str, Any]] = [
    {"email": "b@example.com", "self": True, "responseStatus": "accepted"},
    {"email": "noa@example.com", "responseStatus": "accepted"},
]


class Accounts:
    """Two Google accounts behind one fake; ``use`` is signing in as one of them."""

    def __init__(self, world: World) -> None:
        self.world = world
        self.events: dict[str, list[dict[str, Any]]] = {
            "A": [
                g_event("a-plan", NOW, 60, title="Launch plan", attendees=A_ATTENDEES),
                g_event(
                    "a-review", NOW + timedelta(days=2), title="A review", attendees=A_ATTENDEES
                ),
            ],
            "B": [
                g_event("b-sync", NOW + timedelta(hours=3), title="B sync", attendees=B_ATTENDEES),
            ],
        }

    def use(self, account: str) -> None:
        """Disconnect whoever is signed in (as Settings does), then sign in as ``account``."""
        w = self.world
        if w.auth.connected():
            w.auth.disconnect()
            w.sync.forget()
        w.secrets.set(REFRESH_SECRET, f"r-{account}")
        w.api.items = self.events[account]
        w.synced()

    def cached_ids(self) -> set[str]:
        w = self.world
        window = (NOW - timedelta(days=30), NOW + timedelta(days=30))
        return {e.event_id for e in w.store.between(*window)}


@pytest.fixture
def accounts(tmp_path: Path) -> Accounts:
    return Accounts(World(tmp_path))


def _record(world: World, minutes_in: int = 2, length: int = 30):  # type: ignore[no-untyped-def]
    world.clock.set(NOW + timedelta(minutes=minutes_in))
    meeting = world.meetings.create(source="manual")
    world.clock.set(NOW + timedelta(minutes=minutes_in + length))
    return world.meetings.finish(meeting.id, enqueue=False)


def _event_id(meeting) -> str | None:  # type: ignore[no-untyped-def]
    return (json.loads(meeting.calendar_json or "{}").get("event") or {}).get("event_id")


# ------------------------------------------------------------------ what already holds


def test_the_calendar_shows_only_the_signed_in_accounts_events(accounts: Accounts) -> None:
    accounts.use("A")
    assert accounts.cached_ids() == {"a-plan", "a-review"}
    accounts.use("B")
    assert accounts.cached_ids() == {"b-sync"}
    accounts.use("A")
    assert accounts.cached_ids() == {"a-plan", "a-review"}


def test_a_recording_and_what_it_took_from_the_event_survive_the_switch(accounts: Accounts) -> None:
    w = accounts.world
    accounts.use("A")
    meeting = _record(w)
    assert meeting.title == "Launch plan" and _event_id(meeting) == "a-plan"

    accounts.use("B")
    kept = w.dao.require_meeting(meeting.id)
    assert kept.title == "Launch plan"
    assert _event_id(kept) == "a-plan"
    assert w.meetings.participants(kept) == ("Dana",)

    accounts.use("A")
    back = w.dao.require_meeting(meeting.id)
    assert back.calendar_json == meeting.calendar_json


def test_back_on_account_a_the_invitation_is_readable_again(accounts: Accounts) -> None:
    w = accounts.world
    accounts.use("A")
    meeting = _record(w)
    accounts.use("B")
    accounts.use("A")
    invite = InviteReader(w.auth).fetch("primary", _event_id(meeting) or "")
    assert "Q4 salaries" in (invite.as_api()["agenda"] or "")


# ------------------------------------------------------------------ the gaps


@pytest.mark.xfail(
    strict=True,
    reason="an event reference is ('primary', event id) with no account, so account B is "
    "asked for account A's event, Google answers 404, and the meeting page says the "
    "event was deleted",
)
def test_while_on_b_the_meeting_page_knows_the_event_belongs_to_a(accounts: Accounts) -> None:
    w = accounts.world
    accounts.use("A")
    meeting = _record(w)
    accounts.use("B")
    snapshot = json.loads(w.dao.require_meeting(meeting.id).calendar_json or "{}")
    assert snapshot["event"].get("account") == "a@example.com"


@pytest.mark.xfail(
    strict=True,
    reason="rematch_recent after a sync re-matches every recent recording without a "
    "confident match, whichever account it was recorded under, so account B's event "
    "claims a recording made on account A",
)
def test_a_recording_made_on_a_is_not_claimed_by_bs_event(accounts: Accounts) -> None:
    w = accounts.world
    accounts.use("A")
    # Recorded on A at a time A has nothing: no event, so no match.
    meeting = _record(w, minutes_in=180, length=30)
    assert _event_id(meeting) is None

    accounts.use("B")  # B has "B sync" at exactly that time
    assert _event_id(w.dao.require_meeting(meeting.id)) is None


@pytest.mark.xfail(
    strict=True,
    reason="the invitation reader keeps its short cache across a disconnect, so account "
    "A's invitation is still served after switching to B",
)
def test_disconnecting_forgets_invitations_already_read(accounts: Accounts) -> None:
    w = accounts.world
    reader = InviteReader(w.auth, monotonic=w.clock.monotonic)
    accounts.use("A")
    reader.fetch("primary", "a-plan")
    accounts.use("B")
    with pytest.raises(httpx.HTTPStatusError):
        reader.fetch("primary", "a-plan")
