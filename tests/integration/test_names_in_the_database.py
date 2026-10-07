"""Names live only in the database; files hold content (D89).

A recording's folder is its id: a date, a time and a random suffix, made once and never
renamed. Its ``meta.json`` keeps processing facts, never the title, the calendar meeting
or anyone's name, and the generated files carry no title. So a rename, a reassignment or
an approval in the UI is one database update and no file can contradict it. The database
is backed up, since it is now the only record of those names.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app import meta
from app.db import backup
from tests.fixtures.api import build_harness

ID = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{4}_[0-9a-f]{6}$")


@pytest.fixture
def api(tmp_path: Path, app_home: Path):  # type: ignore[no-untyped-def]
    harness = build_harness(tmp_path)
    yield harness
    recorder = harness.services.recorder
    if recorder is not None and recorder.committed:
        recorder.stop()


def recorded(api: Any, body: dict[str, Any] | None = None) -> str:
    client = api.client()
    started = client.post("/api/recording/start", json=body or {})
    assert started.status_code == 200, started.text
    api.emit(1)
    assert client.post("/api/recording/stop").status_code == 200
    return str(started.json()["meeting_id"])


def names_in(folder: Path) -> set[str]:
    return set(meta.read(folder)) & meta.NAME_FIELDS


# ------------------------------------------------------------------ folders


def test_a_named_recording_gets_a_folder_without_its_name(api) -> None:  # type: ignore[no-untyped-def]
    meeting_id = recorded(api, {"title": "Board meeting: Q4 numbers"})
    meeting = api.services.dao.require_meeting(meeting_id)
    assert ID.match(meeting_id), meeting_id
    assert meeting.path.name == meeting_id
    assert meeting.title == "Board meeting: Q4 numbers"


def test_a_calendar_recording_gets_a_folder_without_its_name(api) -> None:  # type: ignore[no-untyped-def]
    from tests.integration.test_meeting_continuity import an_event_on_now

    an_event_on_now(api, "ev-9", "Hiring sync with Dana")
    meeting_id = recorded(api, {"calendar_id": "primary", "event_id": "ev-9"})
    assert ID.match(meeting_id), meeting_id
    assert "dana" not in meeting_id.lower()


# ------------------------------------------------------------------ meta.json


def test_meta_json_holds_no_names(api) -> None:  # type: ignore[no-untyped-def]
    from tests.integration.test_meeting_continuity import an_event_on_now

    an_event_on_now(api, "ev-2", "Pricing review")
    meeting_id = recorded(api, {"calendar_id": "primary", "event_id": "ev-2"})
    folder = api.services.dao.require_meeting(meeting_id).path
    on_disk = meta.read(folder)
    assert on_disk["id"] == meeting_id and on_disk["state"] == "RECORDED"
    assert names_in(folder) == set()
    assert "Pricing review" not in json.dumps(on_disk, ensure_ascii=False)
    assert "Dana Levi" not in json.dumps(on_disk, ensure_ascii=False)


def test_an_older_meta_json_loses_its_names_when_next_written(api) -> None:  # type: ignore[no-untyped-def]
    meeting_id = recorded(api)
    meeting = api.services.dao.require_meeting(meeting_id)
    meta.update(meeting.path, title="Old name", calendar_json='{"title": "x"}', rating=4)
    meta.mirror(meeting, echo={"delay_ms": 12})
    on_disk = meta.read(meeting.path)
    assert names_in(meeting.path) == set()
    assert on_disk["rating"] == 4 and on_disk["echo"] == {"delay_ms": 12}, "facts stay"


def test_renaming_touches_no_file(api) -> None:  # type: ignore[no-untyped-def]
    meeting_id = recorded(api, {"title": "First name"})
    meeting = api.services.dao.require_meeting(meeting_id)
    before = {p.name: p.read_bytes() for p in meeting.path.iterdir() if p.is_file()}
    response = api.client().patch(f"/api/meetings/{meeting_id}", json={"title": "Second name"})
    assert response.status_code == 200, response.text
    after = {p.name: p.read_bytes() for p in meeting.path.iterdir() if p.is_file()}
    assert after == before, "a rename is a database update only"
    assert api.services.dao.require_meeting(meeting_id).title == "Second name"
    assert meeting.path.exists(), "the folder keeps its name"


def test_reassigning_the_calendar_meeting_writes_no_name_to_disk(api) -> None:  # type: ignore[no-untyped-def]
    from tests.integration.test_meeting_continuity import an_event_on_now

    an_event_on_now(api, "ev-a", "Alpha review", also=(("ev-b", "Beta review"),))
    meeting_id = recorded(api, {"calendar_id": "primary", "event_id": "ev-a"})
    response = api.client().put(
        f"/api/meetings/{meeting_id}/calendar", json={"calendar_id": "primary", "event_id": "ev-b"}
    )
    assert response.status_code == 200, response.text
    meeting = api.services.dao.require_meeting(meeting_id)
    assert meeting.title == "Beta review"
    assert names_in(meeting.path) == set()
    text = "".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in meeting.path.rglob("*")
        if p.is_file() and p.suffix in (".json", ".md", ".html")
    )
    assert "Alpha review" not in text and "Beta review" not in text


# ------------------------------------------------------------------ generated files


def test_the_transcript_file_has_no_title() -> None:
    from app.asr.backend import Segment
    from app.pipeline.stages.assemble import coalesce, render_markdown

    turns = coalesce([Segment(id=0, start=1.0, end=2.0, text="hello", track="me", speaker="ME")])
    text = render_markdown(turns)
    assert not text.startswith("#")
    assert text.startswith("**[00:01] ME:** hello")


def test_the_summary_notes_keep_the_models_title_not_the_meetings() -> None:
    import inspect

    from app.pipeline.stages import summarize

    source = inspect.getsource(summarize)
    assert 'notes["title"] = model_title' in source
    assert "meeting.title or model_title" not in source


# ------------------------------------------------------------------ backups


def a_database(tmp_path: Path, rows: int = 3) -> Path:
    path = tmp_path / "index.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE meetings (id TEXT, title TEXT)")
    conn.executemany(
        "INSERT INTO meetings VALUES (?, ?)", [(f"m{i}", f"t{i}") for i in range(rows)]
    )
    conn.commit()
    conn.close()
    return path


def test_a_backup_is_a_whole_readable_copy(tmp_path: Path) -> None:
    database = a_database(tmp_path, rows=5)
    made = backup.backup(database, why="test")
    assert made is not None and made.parent == tmp_path / "backups"
    copy = sqlite3.connect(made)
    assert copy.execute("SELECT count(*) FROM meetings").fetchone()[0] == 5
    copy.close()
    assert not list((tmp_path / "backups").glob("*.partial"))


def test_a_backup_while_the_app_writes_is_consistent(tmp_path: Path) -> None:
    database = a_database(tmp_path, rows=1)
    writer = sqlite3.connect(database)
    writer.execute("BEGIN")
    writer.execute("INSERT INTO meetings VALUES ('m9', 'uncommitted')")
    made = backup.backup(database, why="test")
    writer.rollback()
    writer.close()
    copy = sqlite3.connect(made or "")
    assert [r[0] for r in copy.execute("SELECT id FROM meetings")] == ["m0"]
    copy.close()


def test_only_the_newest_backups_are_kept(tmp_path: Path) -> None:
    database = a_database(tmp_path)
    start = datetime(2026, 10, 1, tzinfo=UTC)
    for day in range(10):
        backup.backup(database, keep=7, now=start + timedelta(days=day))
    kept = backup.copies(tmp_path / "backups")
    assert len(kept) == 7
    assert "20261010" in kept[-1].name and "20261004" in kept[0].name


def test_a_daily_backup_is_made_once_a_day(tmp_path: Path) -> None:
    database = a_database(tmp_path)
    assert backup.maybe_backup(database) is not None
    assert backup.maybe_backup(database) is None, "one already made today"
    assert backup.maybe_backup(database, every_s=0) is not None


def test_no_database_no_backup(tmp_path: Path) -> None:
    assert backup.backup(tmp_path / "missing.db") is None


def test_the_worker_backs_the_database_up(api) -> None:  # type: ignore[no-untyped-def]
    worker = api.services.worker
    worker.maybe_backup()
    database = backup.database_of(api.services.conn)
    assert database is not None
    assert backup.copies(backup.folder_for(database)), "a backup after startup"


def test_an_update_backs_the_database_up_before_it_installs(api, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    spawned: list[list[str]] = []
    installer = api.services.installer
    installer.spawn = lambda command: spawned.append(command)
    setup = tmp_path / "Upshot-Setup.exe"
    setup.write_bytes(b"MZ")
    installer.install({"version": "0.3.0", "path": setup}, why="test", quit_after=False)
    made = backup.copies(tmp_path / "backups")
    assert made and "before-0.3.0" in made[-1].name
    assert spawned, "and then the installer ran"
