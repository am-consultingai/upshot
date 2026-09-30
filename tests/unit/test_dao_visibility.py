"""A meeting of a hidden calendar account is not there, for every read (D82)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.clock import FakeClock
from app.db.dao import Dao, Turn, connect
from app.gcal.accounts import AccountRegistry


class Library:
    def __init__(self, tmp: Path, *, fts: bool = True) -> None:
        self.conn = connect(tmp / "index.db", fts=fts)
        self.dao = Dao(self.conn, FakeClock())
        self.registry = AccountRegistry(self.conn, FakeClock())
        self.a = self.registry.add_or_restore("a@example.com")[0].id
        self.b = self.registry.add_or_restore("b@example.com")[0].id
        self.tmp = tmp

    def meeting(self, name: str, *accounts: str) -> str:
        meeting = self.dao.insert_meeting(
            folder=self.tmp / name, source="manual", title=f"Roadmap {name}"
        )
        self.dao.index_turns(meeting.id, [Turn(0, "ME", 0, f"roadmap words {name}")])
        self.dao.index_summary(meeting.id, f"roadmap summary {name}")
        self.dao.add_action_item(meeting.id, who="me", what=f"roadmap action {name}")
        self.dao.set_tags(meeting.id, [f"tag-{name}"])
        if accounts:
            self.dao.set_calendar_accounts(meeting.id, accounts[0], accounts)
        return meeting.id


@pytest.fixture(params=[True, False], ids=["fts", "like"])
def lib(request, tmp_path: Path):  # type: ignore[no-untyped-def]
    library = Library(tmp_path, fts=request.param)
    yield library
    library.conn.close()


def _everything(lib: Library) -> dict[str, set[str]]:
    dao = lib.dao
    hits = dao.search("roadmap")
    return {
        "list": {m.id for m in dao.list_meetings()},
        "title": {h.meeting_id for h in hits if h.kind == "title"},
        "action": {h.meeting_id for h in hits if h.kind == "action"},
        "summary": {h.meeting_id for h in hits if h.kind == "summary"},
        "transcript": {h.meeting_id for h in hits if h.kind == "transcript"},
        "items": {i.meeting_id for i in dao.action_items()},
        "counts": set(dao.action_item_counts()),
        "tags": {tag.removeprefix("tag-") for tag, _ in dao.tag_counts()},
    }


def test_everything_is_shown_while_every_account_is_visible(lib: Library) -> None:
    ids = {lib.meeting("x", lib.a), lib.meeting("y", lib.b), lib.meeting("z")}
    for name, found in _everything(lib).items():
        if name == "tags":
            assert found == {"x", "y", "z"}
        else:
            assert found == ids, name


def test_meeting_hidden_when_all_its_accounts_are_hidden(lib: Library) -> None:
    shown = lib.meeting("x", lib.a)
    hidden = lib.meeting("y", lib.b)
    lib.registry.set_visible(lib.b, False)
    for name, found in _everything(lib).items():
        if name == "tags":
            assert found == {"x"}
        else:
            assert found == {shown}, name
    assert lib.dao.visible_meeting(hidden) is None
    assert lib.dao.get_meeting(hidden) is not None  # still in the database
    items = lib.dao.action_items(meeting_id=hidden)
    assert items == []
    assert lib.dao.action_items(meeting_id=hidden, include_hidden=True)


def test_hidden_action_item_is_not_found_by_id(lib: Library) -> None:
    hidden = lib.meeting("y", lib.b)
    item = lib.dao.action_items(meeting_id=hidden)[0]
    lib.registry.set_visible(lib.b, False)
    assert lib.dao.action_item(item.id) is None


def test_shared_meeting_visible_while_any_account_is_visible(lib: Library) -> None:
    shared = lib.meeting("x", lib.a, lib.b)
    lib.registry.set_visible(lib.b, False)
    assert lib.dao.visible_meeting(shared) is not None
    lib.registry.set_visible(lib.a, False)
    assert lib.dao.visible_meeting(shared) is None


def test_removed_equals_hidden(lib: Library) -> None:
    gone = lib.meeting("x", lib.a)
    lib.registry.remove(lib.a)
    assert lib.dao.visible_meeting(gone) is None
    assert lib.dao.list_meetings() == []
    lib.registry.add_or_restore("a@example.com")
    assert lib.dao.visible_meeting(gone) is not None


def test_meeting_with_no_calendar_is_always_visible(lib: Library) -> None:
    plain = lib.meeting("z")
    lib.registry.set_visible(lib.a, False)
    lib.registry.set_visible(lib.b, False)
    assert [m.id for m in lib.dao.list_meetings()] == [plain]


def test_include_hidden_sees_everything(lib: Library) -> None:
    lib.meeting("x", lib.a)
    lib.registry.set_visible(lib.a, False)
    assert lib.dao.list_meetings() == []
    assert len(lib.dao.list_meetings(include_hidden=True)) == 1


def test_accounts_filter(lib: Library) -> None:
    x = lib.meeting("x", lib.a)
    y = lib.meeting("y", lib.b)
    z = lib.meeting("z")
    both = lib.meeting("w", lib.a, lib.b)

    def ids(accounts: list[str]) -> set[str]:
        return {m.id for m in lib.dao.list_meetings(accounts=accounts)}

    assert ids([lib.a]) == {x, both}
    assert ids(["none"]) == {z}
    assert ids([lib.b, "none"]) == {y, z, both}
    assert ids(["ga_unknown0"]) == set()
    assert ids([]) == set()


def test_set_calendar_accounts_replaces_membership(lib: Library) -> None:
    m = lib.meeting("x", lib.a, lib.b)
    lib.dao.set_calendar_accounts(m, lib.b)
    assert lib.dao.calendar_accounts_of(m) == [lib.b]
    assert lib.dao.require_meeting(m).calendar_account_id == lib.b
    lib.dao.set_calendar_accounts(m, None)
    assert lib.dao.calendar_accounts_of(m) == []
    assert lib.dao.require_meeting(m).calendar_account_id is None
