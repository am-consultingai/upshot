"""Several Google accounts at once (epic z8tj1hb9je, D82), end to end against a fake Google.

What the product owner asked for: connect a work and a personal account side by side,
hide one and have everything of it disappear, remove one and have its history come back
when the same address is connected again — and never lose any of it on the way.

Google is faked at the HTTP transport, as in test_calendar_features; each account is its
own list of events behind its own refresh token. Replaces the account-switching check of
2026-09-27, whose three strict xfails are the first three tests here, now passing.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from app.gcal.accounts import token_key
from app.gcal.invite import InviteReader
from app.gcal.source import CalendarNow
from tests.integration.test_calendar_features import NOW, World, g_event

A_ATTENDEES: list[dict[str, Any]] = [
    {"email": "a@example.com", "self": True, "responseStatus": "accepted"},
    {"email": "dana@example.com", "responseStatus": "accepted"},
]
B_ATTENDEES: list[dict[str, Any]] = [
    {"email": "b@example.com", "self": True, "responseStatus": "accepted"},
    {"email": "noa@example.com", "responseStatus": "accepted"},
]


class Two:
    """The World's own account is A; B is added beside it."""

    def __init__(self, world: World) -> None:
        self.w = world
        self.a = world.account
        world.api.items = [
            g_event("a-plan", NOW, 60, title="Launch plan", attendees=A_ATTENDEES),
            g_event("a-review", NOW + timedelta(days=2), title="A review", attendees=A_ATTENDEES),
        ]
        self.b_items = [
            g_event("b-sync", NOW + timedelta(hours=3), title="B sync", attendees=B_ATTENDEES),
        ]
        self.b = world.add_account("b@example.com", "r-b", self.b_items)

    def live_ids(self) -> set[str]:
        window = (NOW - timedelta(days=30), NOW + timedelta(days=30))
        return {
            e.event_id for e in self.w.store.between(*window, accounts=self.w.accounts.active_ids())
        }

    def record(self, minutes_in: int = 2, length: int = 30):  # type: ignore[no-untyped-def]
        self.w.clock.set(NOW + timedelta(minutes=minutes_in))
        meeting = self.w.meetings.create(source="manual")
        self.w.clock.set(NOW + timedelta(minutes=minutes_in + length))
        return self.w.meetings.finish(meeting.id, enqueue=False)


@pytest.fixture
def two(tmp_path: Path) -> Two:
    return Two(World(tmp_path))


def _event(meeting) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return dict(json.loads(meeting.calendar_json or "{}").get("event") or {})


# ------------------------------------------------------------------ the three former gaps


def test_every_event_reference_names_its_account(two: Two) -> None:
    """Was: the reference was ("primary", event id), so the other account was asked for
    it and the page said the event was deleted (z8tj1hb9jg)."""
    two.w.synced()
    meeting = two.record()
    assert _event(meeting) == {**_event(meeting), "account_id": two.a, "event_id": "a-plan"}
    assert meeting.calendar_account_id == two.a
    assert two.w.dao.calendar_accounts_of(meeting.id) == [two.a]
    invite = InviteReader(two.w.accounts).fetch(two.a, "primary", "a-plan")
    assert "Q4 salaries" in (invite.as_api()["agenda"] or "")


def test_rematch_never_uses_a_hidden_accounts_event(two: Two) -> None:
    """Was: rematch claimed a recording for whatever account was connected now
    (z8tj1hb9jf). Now only shown accounts are matched against."""
    two.w.accounts.set_visible(two.b, False)
    two.w.synced()
    meeting = two.record(minutes_in=180, length=30)  # B has "B sync" at exactly that time
    assert _event(meeting) == {}
    two.w.clock.advance(700)
    two.w.synced()  # a sync re-matches recent recordings
    assert _event(two.w.dao.require_meeting(meeting.id)) == {}
    two.w.accounts.set_visible(two.b, True)
    two.w.synced()
    shown = two.w.dao.require_meeting(meeting.id)
    assert _event(shown)["event_id"] == "b-sync", "shown again, it is matched like any other"
    assert shown.calendar_account_id == two.b


def test_removing_an_account_forgets_invitations_already_read(two: Two) -> None:
    """Was: the invitation cache outlived a disconnect (z8tj1hb9jh)."""
    reader = InviteReader(two.w.accounts, monotonic=two.w.clock.monotonic)
    two.w.accounts.on_hidden.append(reader.forget)
    reader.fetch(two.a, "primary", "a-plan")
    assert reader._cache
    two.w.accounts.remove(two.a)
    assert not reader._cache
    from app.gcal.oauth import CalendarAuthError

    with pytest.raises(CalendarAuthError):
        reader.fetch(two.a, "primary", "a-plan")


# ------------------------------------------------------------------ side by side


def test_two_accounts_sync_side_by_side(two: Two) -> None:
    two.w.synced()
    assert two.live_ids() == {"a-plan", "a-review", "b-sync"}
    by_account = {
        e.event_id: e.account_id
        for e in two.w.store.between(NOW, NOW + timedelta(days=5), accounts=None)
    }
    assert by_account == {"a-plan": two.a, "a-review": two.a, "b-sync": two.b}
    assert two.w.sync.status(two.a)["cached_events"] == 2
    assert two.w.sync.status(two.b)["cached_events"] == 1


def test_one_revoked_account_does_not_stop_the_other(two: Two) -> None:
    two.w.synced()
    two.w.secrets.delete(token_key(two.a))  # revoked: as a dead refresh leaves it
    two.w.accounts.auth_for(two.a)._changed()
    two.b_items.append(g_event("b-new", NOW + timedelta(hours=5), title="B new"))
    two.w.clock.advance(700)
    two.w.synced()
    assert "b-new" in two.live_ids()
    assert two.w.accounts.active_ids() == {two.b}


def test_hidden_account_is_not_synced_and_resyncs_when_shown(two: Two) -> None:
    two.w.synced()
    two.w.accounts.set_visible(two.b, False)
    assert two.live_ids() == {"a-plan", "a-review"}
    asked: list[str] = []
    before = len(two.w.api.requests)
    two.b_items.append(g_event("b-new", NOW + timedelta(hours=5), title="B new"))
    two.w.clock.advance(700)
    two.w.synced()
    for request in two.w.api.requests[before:]:
        asked.append(request.headers.get("authorization", ""))
    assert all("r-b" not in header for header in asked), "a hidden account is not asked"
    assert two.w.store.get(two.b, "primary", "b-sync") is not None, "nor is it deleted"
    two.w.accounts.set_visible(two.b, True)
    two.w.sync.kick(two.b)
    two.w.synced()
    assert {"b-sync", "b-new"} <= two.live_ids()


def test_hidden_account_gives_no_reminders_and_no_detector_signal(two: Two) -> None:
    two.w.synced()
    now = CalendarNow(two.w.store, active=two.w.accounts.active_ids)
    soon = NOW + timedelta(hours=3) - timedelta(minutes=3)
    assert [e.event_id for e in now.soon(soon, ahead_s=600)] == ["b-sync"]
    assert now.current(NOW + timedelta(hours=3, minutes=1)) is not None
    two.w.accounts.set_visible(two.b, False)
    assert now.soon(soon, ahead_s=600) == []
    assert now.current(NOW + timedelta(hours=3, minutes=1)) is None


def test_same_meeting_on_two_accounts_matches_once_and_belongs_to_both(two: Two) -> None:
    shared = g_event("shared", NOW, 60, title="Launch plan", attendees=A_ATTENDEES)
    two.w.api.items = [shared]
    two.b_items[:] = [{**shared, "attendees": B_ATTENDEES}]
    two.w.synced()
    now = CalendarNow(two.w.store, active=two.w.accounts.active_ids)
    assert len(now.soon(NOW - timedelta(minutes=3), ahead_s=600)) == 1, "reminded once"
    meeting = two.record()
    assert _event(meeting)["event_id"] == "shared"
    assert two.w.dao.calendar_accounts_of(meeting.id) == sorted([two.a, two.b])
    two.w.accounts.set_visible(two.b, False)
    assert two.w.dao.visible_meeting(meeting.id) is not None, "still on A"
    two.w.accounts.set_visible(two.a, False)
    assert two.w.dao.visible_meeting(meeting.id) is None


def test_hiding_hides_the_recordings_and_deletes_nothing(two: Two) -> None:
    two.w.synced()
    meeting = two.record()
    two.w.accounts.set_visible(two.a, False)
    assert two.w.dao.visible_meeting(meeting.id) is None
    assert [m.id for m in two.w.dao.list_meetings()] == []
    kept = two.w.dao.get_meeting(meeting.id)
    assert kept is not None and kept.calendar_json == meeting.calendar_json
    assert two.w.store.get(two.a, "primary", "a-plan") is not None
    two.w.accounts.set_visible(two.a, True)
    assert [m.id for m in two.w.dao.list_meetings()] == [meeting.id]


def test_removing_keeps_cache_rows_and_snapshots(two: Two) -> None:
    two.w.synced()
    meeting = two.record()
    two.w.accounts.remove(two.a)
    assert two.w.store.count(two.a) == 2
    kept = two.w.dao.get_meeting(meeting.id)
    assert kept is not None and _event(kept)["account_id"] == two.a
    assert two.w.dao.visible_meeting(meeting.id) is None
    assert two.w.registry.get(two.a).removed  # type: ignore[union-attr]
    assert [a.id for a in two.w.registry.accounts()] == [two.b], "not listed"


def test_reconnecting_a_removed_address_brings_its_history_back(two: Two) -> None:
    two.w.synced()
    meeting = two.record()
    two.w.accounts.remove(two.a)
    back, restored = two.w.registry.add_or_restore("ME@example.com")
    assert restored and back.id == two.a
    two.w.secrets.set(token_key(two.a), "r-1")
    two.w.accounts.auth_for(two.a)._changed()
    assert two.w.dao.visible_meeting(meeting.id) is not None
    assert two.a in two.w.accounts.active_ids()


def test_invitation_uses_the_matched_accounts_token(two: Two) -> None:
    two.w.synced()
    reader = InviteReader(two.w.accounts)
    b = reader.fetch(two.b, "primary", "b-sync")
    assert b.as_api()["title"] == "B sync"
    import httpx

    with pytest.raises(httpx.HTTPStatusError):
        reader.fetch(two.a, "primary", "b-sync")  # A's token cannot see B's event
