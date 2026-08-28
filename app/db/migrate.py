"""Ordered migrations and the FTS5 capability probe.

Every migration is a numbered ``.sql`` file applied inside one explicit transaction, so a
failure leaves ``schema_version`` where it was and the database usable.
"""

from __future__ import annotations

import contextlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app import paths
from app.log import get

log = get(__name__)

NAME_RE = re.compile(r"^(\d{4})_.*\.sql$")


def migrations_dir() -> Path:
    return paths.resource("app", "db", "migrations")


@dataclass(frozen=True)
class Migration:
    version: int
    path: Path

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")


def discover(directory: Path | None = None) -> list[Migration]:
    directory = directory or migrations_dir()
    found: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        match = NAME_RE.match(path.name)
        if not match:
            continue
        found.append(Migration(int(match.group(1)), path))
    found.sort(key=lambda m: m.version)
    versions = [m.version for m in found]
    if len(set(versions)) != len(versions):
        raise RuntimeError(f"duplicate migration numbers in {directory}")
    return found


def statements(sql: str) -> list[str]:
    """Split a migration into complete statements.

    ``executescript`` cannot be used: it commits any pending transaction before running,
    which would defeat the one-transaction-per-migration rule.
    """
    out: list[str] = []
    buf = ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            text = buf.strip()
            if text:
                out.append(text)
            buf = ""
    tail = buf.strip()
    if tail:
        out.append(tail)
    return out


def current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if row is None:
        return 0
    value = conn.execute("SELECT version FROM schema_version").fetchone()
    return int(value[0]) if value else 0


def _set_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version(version) VALUES (?)", (version,))


def migrate(conn: sqlite3.Connection, directory: Path | None = None) -> int:
    """Apply every pending migration. Returns the resulting schema version."""
    version = current_version(conn)
    for migration in discover(directory):
        if migration.version <= version:
            continue
        conn.execute("BEGIN")
        try:
            for statement in statements(migration.sql):
                conn.execute(statement)
            _set_version(conn, migration.version)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            log.error(
                "migration %s failed; database left at version %s", migration.path.name, version
            )
            raise
        version = migration.version
        log.info("applied migration %s", migration.path.name)
    return version


def probe_fts5(conn: sqlite3.Connection) -> bool:
    """Is FTS5 compiled into this SQLite? (TECHNICAL-DESIGN.md §19.1)"""
    try:
        conn.execute("CREATE VIRTUAL TABLE temp.fts_probe USING fts5(x)")
    except sqlite3.Error:
        return False
    with contextlib.suppress(sqlite3.Error):
        conn.execute("DROP TABLE temp.fts_probe")
    return True


FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS transcripts_fts USING fts5(
  meeting_id UNINDEXED, speaker UNINDEXED, at_ms UNINDEXED, text,
  tokenize = 'unicode61 remove_diacritics 0'
)
"""


def ensure_fts(conn: sqlite3.Connection, *, enabled: bool = True) -> bool:
    """Create the FTS index when possible. Returns the resulting capability."""
    if not enabled or not probe_fts5(conn):
        return False
    conn.execute("BEGIN")
    try:
        conn.execute(FTS_DDL.strip())
        conn.execute("COMMIT")
    except sqlite3.Error:
        conn.execute("ROLLBACK")
        return False
    return True
