"""An older install's one Google account becomes account number one (D82)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.clock import FakeClock
from app.config import FakeKeyring
from app.db.dao import Dao, connect
from app.gcal.accounts import (
    LEGACY_ACCOUNT,
    LEGACY_REFRESH,
    AccountRegistry,
    adopt_legacy,
    token_key,
)


@pytest.fixture
def world(tmp_path: Path):  # type: ignore[no-untyped-def]
    conn = connect(tmp_path / "index.db")
    dao = Dao(conn, FakeClock())
    registry = AccountRegistry(conn, FakeClock())
    secrets = FakeKeyring()
    yield conn, dao, registry, secrets, tmp_path
    conn.close()


def _legacy_event(conn, event_id: str = "e1") -> None:  # type: ignore[no-untyped-def]
    conn.execute(
        "INSERT INTO calendar_events(account_id, calendar_id, event_id, start_at, end_at, "
        "synced_at) VALUES ('', 'primary', ?, '2026-09-01T10:00:00Z', "
        "'2026-09-01T11:00:00Z', '2026-09-01T09:00:00Z')",
        (event_id,),
    )


def _matched_meeting(dao: Dao, folder: Path, event_id: str = "e1") -> str:
    payload = {
        "event": {"calendar_id": "primary", "event_id": event_id},
        "title": "Weekly",
        "match": {"state": "matched", "source": "auto"},
    }
    return dao.insert_meeting(
        folder=folder, source="detected", calendar_json=json.dumps(payload)
    ).id


def test_a_legacy_token_becomes_account_one(world) -> None:  # type: ignore[no-untyped-def]
    _conn, _dao, registry, secrets, _ = world
    secrets.set(LEGACY_REFRESH, "r-1")
    secrets.set(LEGACY_ACCOUNT, "dana@example.com")
    adopted = adopt_legacy(registry, secrets, lambda _: None)
    assert adopted is not None
    account_id, _ = adopted
    account = registry.get(account_id)
    assert account is not None and account.address == "dana@example.com" and account.shown
    assert secrets.get(token_key(account_id)) == "r-1"
    assert secrets.get(LEGACY_REFRESH) is None
    assert secrets.get(LEGACY_ACCOUNT) is None


def test_legacy_cache_rows_take_the_account(world) -> None:  # type: ignore[no-untyped-def]
    conn, _dao, registry, secrets, _ = world
    _legacy_event(conn)
    secrets.set(LEGACY_REFRESH, "r-1")
    secrets.set(LEGACY_ACCOUNT, "dana@example.com")
    account_id, _ = adopt_legacy(registry, secrets, lambda _: None)  # type: ignore[misc]
    rows = conn.execute("SELECT account_id FROM calendar_events").fetchall()
    assert [r["account_id"] for r in rows] == [account_id]


def test_legacy_matched_meetings_get_the_account_and_a_membership_row(world) -> None:  # type: ignore[no-untyped-def]
    _conn, dao, registry, secrets, tmp = world
    matched = _matched_meeting(dao, tmp / "m1")
    plain = dao.insert_meeting(folder=tmp / "m2", source="manual").id
    secrets.set(LEGACY_REFRESH, "r-1")
    secrets.set(LEGACY_ACCOUNT, "dana@example.com")
    account_id, changed = adopt_legacy(registry, secrets, lambda _: None)  # type: ignore[misc]
    assert changed == [matched]
    meeting = dao.require_meeting(matched)
    assert meeting.calendar_account_id == account_id
    assert json.loads(meeting.calendar_json or "{}")["event"]["account_id"] == account_id
    assert dao.calendar_accounts_of(matched) == [account_id]
    assert dao.calendar_accounts_of(plain) == []


def test_adoption_runs_once(world) -> None:  # type: ignore[no-untyped-def]
    _conn, _dao, registry, secrets, _ = world
    secrets.set(LEGACY_REFRESH, "r-1")
    secrets.set(LEGACY_ACCOUNT, "dana@example.com")
    assert adopt_legacy(registry, secrets, lambda _: None) is not None
    assert adopt_legacy(registry, secrets, lambda _: None) is None
    assert len(registry.accounts()) == 1


def test_no_legacy_token_leaves_everything(world) -> None:  # type: ignore[no-untyped-def]
    conn, _dao, registry, secrets, _ = world
    _legacy_event(conn)
    assert adopt_legacy(registry, secrets, lambda _: "x@example.com") is None
    assert registry.accounts() == []
    assert conn.execute("SELECT account_id FROM calendar_events").fetchone()["account_id"] == ""


def test_the_address_is_read_from_google_when_it_was_never_stored(world) -> None:  # type: ignore[no-untyped-def]
    _conn, _dao, registry, secrets, _ = world
    secrets.set(LEGACY_REFRESH, "r-1")
    seen: list[str] = []

    def probe(token: str) -> str:
        seen.append(token)
        return "dana@example.com"

    adopted = adopt_legacy(registry, secrets, probe)
    assert adopted is not None and seen == ["r-1"]


def test_address_unreadable_retries_next_start(world) -> None:  # type: ignore[no-untyped-def]
    _conn, _dao, registry, secrets, _ = world
    secrets.set(LEGACY_REFRESH, "r-1")
    assert adopt_legacy(registry, secrets, lambda _: None) is None
    assert secrets.get(LEGACY_REFRESH) == "r-1"
    assert registry.accounts() == []
