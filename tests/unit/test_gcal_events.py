"""The event cache across accounts: nothing deleted, every read told which accounts (D82)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.db.dao import connect
from app.gcal.events import CalendarEvent, EventStore

START = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
WINDOW = (START - timedelta(hours=1), START + timedelta(hours=4))


def ev(account: str, event_id: str, hours: int = 0, uid: str | None = None) -> CalendarEvent:
    start = START + timedelta(hours=hours)
    return CalendarEvent(
        account_id=account,
        calendar_id="primary",
        event_id=event_id,
        title=event_id,
        start=start,
        end=start + timedelta(minutes=30),
        ical_uid=uid or f"{event_id}@x",
    )


@pytest.fixture
def store(tmp_path: Path):  # type: ignore[no-untyped-def]
    conn = connect(tmp_path / "index.db")
    yield EventStore(conn)
    conn.close()


def test_key_has_three_parts() -> None:
    assert ev("ga_1", "e").key == ("ga_1", "primary", "e")
    assert ev("ga_1", "e").as_api()["account_id"] == "ga_1"


def test_replace_window_tombstones_instead_of_deleting(store: EventStore) -> None:
    store.replace_window("ga_1", "primary", *WINDOW, [ev("ga_1", "a"), ev("ga_1", "b", 1)],
                         synced_at=START)  # fmt: skip
    store.replace_window("ga_1", "primary", *WINDOW, [ev("ga_1", "a")], synced_at=START)
    assert [e.event_id for e in store.between(*WINDOW, accounts=None)] == ["a"]
    assert store.get("ga_1", "primary", "b") is not None
    assert store.count("ga_1") == 1, "a tombstone is not counted as live"


def test_an_event_that_returns_clears_its_tombstone(store: EventStore) -> None:
    store.replace_window("ga_1", "primary", *WINDOW, [ev("ga_1", "a")], synced_at=START)
    store.replace_window("ga_1", "primary", *WINDOW, [], synced_at=START)
    store.replace_window("ga_1", "primary", *WINDOW, [ev("ga_1", "a")], synced_at=START)
    assert [e.event_id for e in store.between(*WINDOW, accounts=None)] == ["a"]


def test_a_sync_of_one_account_never_touches_another(store: EventStore) -> None:
    store.replace_window("ga_1", "primary", *WINDOW, [ev("ga_1", "a")], synced_at=START)
    store.replace_window("ga_2", "primary", *WINDOW, [], synced_at=START)
    assert [e.event_id for e in store.between(*WINDOW, accounts=["ga_1"])] == ["a"]


def test_between_asks_for_accounts(store: EventStore) -> None:
    store.replace_window("ga_1", "primary", *WINDOW, [ev("ga_1", "a")], synced_at=START)
    store.replace_window("ga_2", "primary", *WINDOW, [ev("ga_2", "b", 1)], synced_at=START)
    assert [e.event_id for e in store.between(*WINDOW, accounts=["ga_2"])] == ["b"]
    assert store.between(*WINDOW, accounts=[]) == []
    assert len(store.between(*WINDOW, accounts=None)) == 2


def test_the_same_event_id_on_two_accounts_is_two_rows(store: EventStore) -> None:
    store.replace_window("ga_1", "primary", *WINDOW, [ev("ga_1", "same")], synced_at=START)
    store.replace_window("ga_2", "primary", *WINDOW, [ev("ga_2", "same")], synced_at=START)
    assert store.count() == 2


def test_copies_finds_the_same_occurrence_on_other_accounts(store: EventStore) -> None:
    store.replace_window("ga_1", "primary", *WINDOW, [ev("ga_1", "x", uid="m@x")],
                         synced_at=START)  # fmt: skip
    store.replace_window("ga_2", "primary", *WINDOW, [ev("ga_2", "y", uid="m@x")],
                         synced_at=START)  # fmt: skip
    store.replace_window("ga_3", "primary", *WINDOW, [ev("ga_3", "z", uid="m@x")],
                         synced_at=START)  # fmt: skip
    one = store.get("ga_1", "primary", "x")
    assert one is not None
    assert store.copies(one, accounts=None) == ["ga_1", "ga_2", "ga_3"]
    assert store.copies(one, accounts=["ga_1", "ga_2"]) == ["ga_1", "ga_2"]
