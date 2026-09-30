"""The registry of connected Google accounts (D82): ids, addresses, hide and remove."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.clock import FakeClock
from app.db.dao import connect
from app.gcal.accounts import COLOURS, AccountRegistry


@pytest.fixture
def registry(tmp_path: Path):  # type: ignore[no-untyped-def]
    conn = connect(tmp_path / "index.db")
    yield AccountRegistry(conn, FakeClock())
    conn.close()


def test_add_gives_an_opaque_id_and_the_next_free_colour(registry: AccountRegistry) -> None:
    first, restored = registry.add_or_restore("dana@example.com")
    second, _ = registry.add_or_restore("noa@example.com")
    assert not restored
    assert re.fullmatch(r"ga_[0-9a-f]{8}", first.id)
    assert "dana" not in first.id
    assert (first.color, second.color) == (1, 2)
    assert (first.position, second.position) == (1, 2)
    assert registry.sources() == [(first.id, "primary"), (second.id, "primary")]


def test_the_same_address_keeps_its_id_case_insensitively(registry: AccountRegistry) -> None:
    first, _ = registry.add_or_restore("Dana@Example.com")
    again, restored = registry.add_or_restore("dana@example.com")
    assert again.id == first.id
    assert not restored
    assert len(registry.accounts()) == 1


def test_remove_keeps_the_row_and_the_address(registry: AccountRegistry) -> None:
    account, _ = registry.add_or_restore("dana@example.com")
    registry.remove(account.id)
    assert registry.accounts() == []
    kept = registry.get(account.id)
    assert kept is not None and kept.removed and kept.address == "dana@example.com"
    assert registry.accounts(include_removed=True)[0].id == account.id


def test_restore_clears_removed_and_shows_the_account(registry: AccountRegistry) -> None:
    account, _ = registry.add_or_restore("dana@example.com")
    registry.set_visible(account.id, False)
    registry.remove(account.id)
    back, restored = registry.add_or_restore("dana@example.com")
    assert restored
    assert back.id == account.id
    assert back.shown


def test_shown_ids_leave_out_hidden_and_removed(registry: AccountRegistry) -> None:
    a, _ = registry.add_or_restore("a@example.com")
    b, _ = registry.add_or_restore("b@example.com")
    c, _ = registry.add_or_restore("c@example.com")
    registry.set_visible(b.id, False)
    registry.remove(c.id)
    assert registry.shown_ids() == {a.id}
    assert registry.sources() == [(a.id, "primary")]
    assert len(registry.sources(shown_only=False)) == 3


def test_colours_cycle_after_six(registry: AccountRegistry) -> None:
    colours = [registry.add_or_restore(f"u{i}@example.com")[0].color for i in range(COLOURS + 1)]
    assert colours[:COLOURS] == list(range(1, COLOURS + 1))
    assert 1 <= colours[COLOURS] <= COLOURS


def test_a_removed_accounts_colour_is_free_again(registry: AccountRegistry) -> None:
    a, _ = registry.add_or_restore("a@example.com")
    registry.add_or_restore("b@example.com")
    registry.remove(a.id)
    c, _ = registry.add_or_restore("c@example.com")
    assert c.color == 1


def test_set_visible_on_an_unknown_account_is_an_error(registry: AccountRegistry) -> None:
    with pytest.raises(KeyError):
        registry.set_visible("ga_00000000", False)
