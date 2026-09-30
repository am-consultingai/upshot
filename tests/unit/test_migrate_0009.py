"""Migration 0009 keeps the event cache and every meeting (D82: calendar data is never
deleted)."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from app.db import migrate as migrations
from app.db.dao import Connection


def _at_version_8(tmp: Path) -> Connection:
    older = tmp / "migrations"
    older.mkdir()
    for path in migrations.migrations_dir().glob("*.sql"):
        if int(path.name[:4]) <= 8:
            shutil.copy(path, older / path.name)
    conn = sqlite3.connect(
        str(tmp / "index.db"), isolation_level=None, check_same_thread=False, factory=Connection
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    migrations.migrate(conn, older)
    assert migrations.current_version(conn) == 8
    return conn


def test_the_cache_and_the_meetings_come_through(tmp_path: Path) -> None:
    conn = _at_version_8(tmp_path)
    conn.execute(
        "INSERT INTO calendar_events(calendar_id, event_id, title, start_at, end_at, synced_at) "
        "VALUES ('primary', 'e1', 'Weekly', '2026-09-01T10:00:00Z', '2026-09-01T11:00:00Z', "
        "'2026-09-01T09:00:00Z')"
    )
    conn.execute(
        "INSERT INTO meetings(id, folder, source, state, started_at, profile, created_at, "
        "updated_at, calendar_json) VALUES ('m1', '/x', 'manual', 'ready', "
        "'2026-09-01T10:00:00Z', 'auto', 'now', 'now', '{\"title\": \"Weekly\"}')"
    )
    migrations.migrate(conn)
    event = conn.execute("SELECT * FROM calendar_events").fetchone()
    assert (event["account_id"], event["event_id"], event["title"]) == ("", "e1", "Weekly")
    assert event["removed_at"] is None
    meeting = conn.execute("SELECT * FROM meetings WHERE id = 'm1'").fetchone()
    assert meeting["calendar_json"] == '{"title": "Weekly"}'
    assert meeting["calendar_account_id"] is None
    indexes = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {
        "ix_calendar_events_start",
        "ix_calendar_events_end",
        "ix_calendar_events_account",
        "ix_meetings_calendar_account",
        "ix_meeting_calendar_accounts_account",
    } <= indexes
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"calendar_accounts", "calendar_sources", "meeting_calendar_accounts"} <= tables
    conn.close()


def test_the_same_event_id_can_live_on_two_accounts(tmp_path: Path) -> None:
    conn = _at_version_8(tmp_path)
    migrations.migrate(conn)
    for account in ("ga_1", "ga_2"):
        conn.execute(
            "INSERT INTO calendar_events(account_id, calendar_id, event_id, start_at, end_at, "
            "synced_at) VALUES (?, 'primary', 'e1', 'a', 'b', 'c')",
            (account,),
        )
    assert conn.execute("SELECT COUNT(*) FROM calendar_events").fetchone()[0] == 2
    conn.close()


def test_membership_goes_with_its_meeting(tmp_path: Path) -> None:
    conn = _at_version_8(tmp_path)
    migrations.migrate(conn)
    conn.execute(
        "INSERT INTO meetings(id, folder, source, state, started_at, profile, created_at, "
        "updated_at) VALUES ('m1', '/x', 'manual', 'ready', 't', 'auto', 'now', 'now')"
    )
    conn.execute("INSERT INTO meeting_calendar_accounts VALUES ('m1', 'ga_1')")
    conn.execute("DELETE FROM meetings WHERE id = 'm1'")
    assert conn.execute("SELECT COUNT(*) FROM meeting_calendar_accounts").fetchone()[0] == 0
    conn.close()
