"""Copies of ``index.db``, made while the app runs (D89).

The database is the only record of what a person sees or edits about a meeting: its title,
its calendar meeting, its speakers' names. ``meta.json`` no longer repeats them, so losing
the database would lose them. A copy is made at most once a day, from the worker's idle
time, and before an update installs; the newest few are kept.

SQLite's online backup copies a consistent snapshot while the app keeps writing, which a
file copy of a database in WAL mode does not.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.log import get

log = get(__name__)

PREFIX = "index-"
SUFFIX = ".db"
FOLDER = "backups"
#: How many copies stay: a week of daily ones, or a few updates.
KEEP = 7


def folder_for(database: Path) -> Path:
    return Path(database).parent / FOLDER


def copies(folder: Path) -> list[Path]:
    """The backups in ``folder``, oldest first."""
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.glob(f"{PREFIX}*{SUFFIX}") if p.is_file())


def backup(
    database: Path, *, why: str = "daily", keep: int = KEEP, now: datetime | None = None
) -> Path | None:
    """Copy ``database`` beside it, into ``backups/``, and drop all but the newest ``keep``.

    None when there is no database file to copy (an in-memory or a missing one)."""
    database = Path(database)
    if not database.is_file():
        return None
    folder = folder_for(database)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = folder / f"{PREFIX}{stamp}-{why}{SUFFIX}"
    partial = target.with_suffix(".partial")
    source = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        copy = sqlite3.connect(partial)
        try:
            source.backup(copy)
        finally:
            copy.close()
    finally:
        source.close()
    partial.replace(target)  # a crash mid-copy leaves a .partial, never a broken backup
    for old in copies(folder)[:-keep] if keep > 0 else []:
        old.unlink(missing_ok=True)
    log.info("database backed up to %s (%s)", target.name, why)
    return target


def newest_age_s(database: Path, *, now: datetime | None = None) -> float | None:
    """Seconds since the newest backup was made; None when there is none."""
    found = copies(folder_for(database))
    if not found:
        return None
    made = datetime.fromtimestamp(found[-1].stat().st_mtime, UTC)
    return ((now or datetime.now(UTC)) - made).total_seconds()


def maybe_backup(database: Path, *, every_s: float = 24 * 3600) -> Path | None:
    """A backup when the newest is older than ``every_s``, or there is none."""
    age = newest_age_s(database)
    if age is not None and age < every_s:
        return None
    return backup(database)


def database_of(conn: sqlite3.Connection) -> Path | None:
    """The file behind ``conn``; None for an in-memory database."""
    for row in conn.execute("PRAGMA database_list").fetchall():
        name, file = row[1], row[2]
        if name == "main" and file:
            return Path(file)
    return None
