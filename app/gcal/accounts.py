"""The Google accounts this installation has connected (epic z8tj1hb9je, D82).

Several at once. Each has an opaque id (``ga_`` + 8 hex) that everything else refers to:
the event cache, the snapshot a recording keeps of its event, the meeting's
``calendar_account_id`` and ``meeting_calendar_accounts``, toast links. The address is
kept here, in the database, because an account is never deleted: removing it is a
permanent hide (``removed_at``), and connecting the same address again restores the
same id, which is what brings its history back.

Tokens are not here. Each account's refresh token is in the OS credential store under
``google_refresh_token:<id>`` (``token_key``).
"""

from __future__ import annotations

import json
import secrets as random_secrets
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.clock import Clock, SystemClock, iso
from app.config import SecretStore
from app.db.dao import transaction
from app.log import get

log = get(__name__)

#: How many dot colours there are (``--account-1`` .. ``--account-6``). After that they
#: repeat: telling seven accounts apart by colour alone was never going to work anyway.
COLOURS = 6

#: Where an older install kept its one account.
LEGACY_REFRESH = "google_refresh_token"
LEGACY_ACCOUNT = "google_account"

PRIMARY = "primary"


def token_key(account_id: str) -> str:
    return f"{LEGACY_REFRESH}:{account_id}"


@dataclass(frozen=True, slots=True)
class Account:
    id: str
    address: str
    visible: bool
    removed_at: str | None
    color: int
    position: int
    added_at: str

    @property
    def removed(self) -> bool:
        return self.removed_at is not None

    @property
    def shown(self) -> bool:
        """Visible and not removed: its data appears in the application."""
        return self.visible and not self.removed


def _row(row: sqlite3.Row) -> Account:
    return Account(
        id=row["id"],
        address=row["address"],
        visible=bool(row["visible"]),
        removed_at=row["removed_at"],
        color=int(row["color"]),
        position=int(row["position"]),
        added_at=row["added_at"],
    )


class AccountRegistry:
    def __init__(self, conn: sqlite3.Connection, clock: Clock | None = None) -> None:
        self.conn = conn
        self.clock = clock or SystemClock()

    # ------------------------------------------------------------------ reads

    def accounts(self, *, include_removed: bool = False) -> list[Account]:
        clause = "" if include_removed else "WHERE removed_at IS NULL"
        rows = self.conn.execute(
            f"SELECT * FROM calendar_accounts {clause} ORDER BY position, added_at"
        ).fetchall()
        return [_row(r) for r in rows]

    def get(self, account_id: str) -> Account | None:
        row = self.conn.execute(
            "SELECT * FROM calendar_accounts WHERE id = ?", (account_id,)
        ).fetchone()
        return _row(row) if row else None

    def by_address(self, address: str) -> Account | None:
        row = self.conn.execute(
            "SELECT * FROM calendar_accounts WHERE address = ? COLLATE NOCASE", (address,)
        ).fetchone()
        return _row(row) if row else None

    def shown_ids(self) -> frozenset[str]:
        """Visible and not removed."""
        rows = self.conn.execute(
            "SELECT id FROM calendar_accounts WHERE visible = 1 AND removed_at IS NULL"
        ).fetchall()
        return frozenset(r["id"] for r in rows)

    def sources(self, *, shown_only: bool = True) -> list[tuple[str, str]]:
        """(account id, calendar id) pairs to read from Google."""
        clause = (
            "WHERE a.visible = 1 AND a.removed_at IS NULL AND s.visible = 1" if shown_only else ""
        )
        rows = self.conn.execute(
            "SELECT s.account_id, s.calendar_id FROM calendar_sources s "
            f"JOIN calendar_accounts a ON a.id = s.account_id {clause} "
            "ORDER BY a.position, s.calendar_id"
        ).fetchall()
        return [(r["account_id"], r["calendar_id"]) for r in rows]

    # ------------------------------------------------------------------ writes

    def add_or_restore(self, address: str) -> tuple[Account, bool]:
        """The account for this address: a new one, or the one it already was.

        Returns ``(account, restored)``. ``restored`` is True when the address was known
        and had been removed: it comes back shown, with its id, so its history returns.
        """
        address = address.strip()
        if not address:
            raise ValueError("an account needs its address")
        known = self.by_address(address)
        with transaction(self.conn):
            if known is not None:
                restored = known.removed
                if restored:
                    self.conn.execute(
                        "UPDATE calendar_accounts SET removed_at = NULL, visible = 1, color = ? "
                        "WHERE id = ?",
                        (self._free_colour(prefer=known.color), known.id),
                    )
                    log.info("calendar account %s restored", known.id)
                self._ensure_primary(known.id)
            else:
                restored = False
                account_id = self._new_id()
                position = self.conn.execute(
                    "SELECT COALESCE(MAX(position), 0) + 1 FROM calendar_accounts"
                ).fetchone()[0]
                self.conn.execute(
                    "INSERT INTO calendar_accounts(id, address, visible, removed_at, color, "
                    "position, added_at) VALUES (?, ?, 1, NULL, ?, ?, ?)",
                    (account_id, address, self._free_colour(), position, iso(self.clock.now())),
                )
                self._ensure_primary(account_id)
                log.info("calendar account %s added", account_id)
        account = self.by_address(address)
        assert account is not None
        return account, restored

    def set_visible(self, account_id: str, visible: bool) -> Account:
        with transaction(self.conn):
            self.conn.execute(
                "UPDATE calendar_accounts SET visible = ? WHERE id = ?", (int(visible), account_id)
            )
        account = self.get(account_id)
        if account is None:
            raise KeyError(f"no such calendar account: {account_id}")
        return account

    def remove(self, account_id: str) -> None:
        """A permanent hide. The row, its events and its meetings all stay."""
        with transaction(self.conn):
            self.conn.execute(
                "UPDATE calendar_accounts SET removed_at = ? WHERE id = ? AND removed_at IS NULL",
                (iso(self.clock.now()), account_id),
            )

    # ------------------------------------------------------------------ helpers

    def _ensure_primary(self, account_id: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO calendar_sources(account_id, calendar_id, visible) "
            "VALUES (?, ?, 1)",
            (account_id, PRIMARY),
        )

    def _new_id(self) -> str:
        while True:
            candidate = f"ga_{random_secrets.token_hex(4)}"
            if self.get(candidate) is None:
                return candidate

    def _free_colour(self, prefer: int | None = None) -> int:
        used = {
            int(r["color"])
            for r in self.conn.execute(
                "SELECT color FROM calendar_accounts WHERE removed_at IS NULL"
            ).fetchall()
        }
        if prefer is not None and prefer not in used:
            return prefer
        for colour in range(1, COLOURS + 1):
            if colour not in used:
                return colour
        count = len(used)
        return count % COLOURS + 1


def adopt_legacy(
    registry: AccountRegistry,
    secrets: SecretStore,
    probe_address: Callable[[str], str | None],
) -> tuple[str, list[str]] | None:
    """Turn an older install's one account into account number one. Idempotent.

    An install from before several accounts kept one refresh token under
    ``google_refresh_token``, its address under ``google_account``, its cached events
    under no account, and snapshots whose event reference names no account. All of it
    was that account's (an earlier "Use a different account" emptied the cache, but
    snapshots made before a switch are credited to the account connected now; D82 says
    so). ``probe_address(refresh_token)`` reads the address when it was never stored.

    Returns the account id and the meetings whose snapshot changed (their meta.json is
    rewritten by the caller), or None when there was nothing to adopt or the address
    could not be read (then everything is left as it was, and the next start tries again).
    """
    refresh = secrets.get(LEGACY_REFRESH)
    if not refresh:
        return None
    address = secrets.get(LEGACY_ACCOUNT) or probe_address(refresh)
    if not address:
        log.warning("an older Google connection was found but its address could not be read")
        return None
    account, _ = registry.add_or_restore(address)
    secrets.set(token_key(account.id), refresh)
    secrets.delete(LEGACY_REFRESH)
    secrets.delete(LEGACY_ACCOUNT)
    conn = registry.conn
    changed: list[str] = []
    with transaction(conn):
        conn.execute(
            "UPDATE calendar_events SET account_id = ? WHERE account_id = ''", (account.id,)
        )
        for row in conn.execute(
            "SELECT id, calendar_json FROM meetings WHERE calendar_json IS NOT NULL "
            "AND calendar_account_id IS NULL"
        ).fetchall():
            payload = _load(row["calendar_json"])
            event = payload.get("event")
            if not isinstance(event, dict) or event.get("account_id"):
                continue
            event["account_id"] = account.id
            payload["accounts"] = [account.id]
            conn.execute(
                "UPDATE meetings SET calendar_json = ?, calendar_account_id = ? WHERE id = ?",
                (json.dumps(payload, ensure_ascii=False), account.id, row["id"]),
            )
            conn.execute(
                "INSERT OR IGNORE INTO meeting_calendar_accounts(meeting_id, account_id) "
                "VALUES (?, ?)",
                (row["id"], account.id),
            )
            changed.append(row["id"])
    log.info("the older Google connection is now calendar account %s", account.id)
    return account.id, changed


def _load(raw: str) -> dict[str, Any]:
    try:
        loaded = json.loads(raw)
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}
