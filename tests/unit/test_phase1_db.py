from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from app.clock import FakeClock, parse_iso
from app.db import migrate as migrations
from app.db.dao import Dao, GlossaryTerm, Turn, capabilities, connect
from app.errors import IllegalTransition
from app.pipeline.states import MeetingState


@pytest.fixture
def db(tmp_path: Path):  # type: ignore[no-untyped-def]
    conn = connect(tmp_path / "index.db")
    yield conn
    conn.close()


@pytest.fixture
def dao(db, tmp_path: Path) -> Dao:  # type: ignore[no-untyped-def]
    d = Dao(db, FakeClock())
    d.seed_ids(7)
    return d


def _strip_comments(sql: str) -> str:
    lines = [re.sub(r"--.*$", "", line).rstrip() for line in sql.splitlines()]
    return "\n".join(line for line in lines if line.strip())


def test_schema_sql_matches_migration_0001() -> None:
    schema = Path("app/db/schema.sql").read_text(encoding="utf-8")
    first = Path("app/db/migrations/0001_initial.sql").read_text(encoding="utf-8")
    assert _strip_comments(schema) == _strip_comments(first)


def test_migrations_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "index.db"
    conn = connect(path)
    version = migrations.current_version(conn)
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert migrations.migrate(conn) == version
    again = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert again == tables
    assert migrations.current_version(conn) == version
    conn.close()
    reopened = connect(path)
    assert migrations.current_version(reopened) == version
    reopened.close()


def test_migration_atomic(tmp_path: Path) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "0001_initial.sql").write_text(
        Path("app/db/migrations/0001_initial.sql").read_text(encoding="utf-8"), encoding="utf-8"
    )
    conn = sqlite3.connect(str(tmp_path / "index.db"), isolation_level=None)
    assert migrations.migrate(conn, migrations_dir) == 1

    # now inject a migration whose second statement fails
    (migrations_dir / "0002_broken.sql").write_text(
        "CREATE TABLE ok_before (x INTEGER);\n"
        "INSERT INTO no_such_table(x) VALUES (1);\n"
        "CREATE TABLE never_created (x INTEGER);\n",
        encoding="utf-8",
    )
    with pytest.raises(sqlite3.Error):
        migrations.migrate(conn, migrations_dir)
    assert migrations.current_version(conn) == 1
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "ok_before" not in tables and "never_created" not in tables
    # the database is still usable
    conn.execute(
        "INSERT INTO meetings(id, folder, source, state, started_at, profile, created_at, "
        "updated_at) VALUES ('m','/tmp/m','manual','RECORDING','t','auto','t','t')"
    )
    assert conn.execute("SELECT count(*) FROM meetings").fetchone()[0] == 1
    conn.close()


def test_fts5_probe_reports(tmp_path: Path) -> None:
    on = connect(tmp_path / "on.db")
    assert isinstance(capabilities(on).fts, bool)
    assert migrations.probe_fts5(on) in (True, False)

    off = connect(tmp_path / "off.db", fts=False)
    assert capabilities(off).fts is False
    dao = Dao(off, FakeClock())
    m = dao.insert_meeting(folder=tmp_path / "m", source="manual")
    dao.index_turns(
        m.id,
        [Turn(0, "ME", 0, "the quick brown fox"), Turn(1, "THEM", 1000, "a slow green turtle")],
    )
    hits = dao.search("brown")
    assert [h.text for h in hits] == ["the quick brown fox"]
    assert "brown" in hits[0].snippet
    assert dao.search("turtle")[0].at_ms == 1000
    assert dao.search("nonexistent") == []
    on.close()
    off.close()


def test_fts_hebrew_diacritics(dao: Dao, db, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    if not capabilities(db).fts:
        pytest.skip("FTS5 unavailable in this SQLite build")
    m1 = dao.insert_meeting(meeting_id="a", folder=tmp_path / "a", source="manual")
    m2 = dao.insert_meeting(meeting_id="b", folder=tmp_path / "b", source="manual")
    dao.index_turns(m1.id, [Turn(0, "ME", 0, "שָׁלוֹם")])
    dao.index_turns(m2.id, [Turn(0, "ME", 0, "שלום")])
    hits = dao.search("שָׁלוֹם")
    assert [h.meeting_id for h in hits] == ["a"], "remove_diacritics 0 must keep niqqud distinct"
    assert [h.meeting_id for h in dao.search("שלום")] == ["b"]


def test_dao_meeting_roundtrip(dao: Dao, tmp_path: Path) -> None:
    folder = tmp_path / "2026-08-28_1400_x"
    meeting = dao.insert_meeting(
        folder=folder,
        source="manual",
        profile="gpu-live",
        title="Weekly Sync",
        title_source="window",
    )
    fetched = dao.get_meeting(meeting.id)
    assert fetched == meeting
    assert fetched is not None
    assert fetched.folder == str(folder)
    assert fetched.title == "Weekly Sync"
    assert parse_iso(fetched.started_at).tzinfo is not None
    assert parse_iso(fetched.created_at) == parse_iso(fetched.updated_at)
    updated = dao.update_meeting(meeting.id, title="Renamed", duration_s=2820)
    assert updated.title == "Renamed" and updated.duration_s == 2820
    assert dao.list_meetings()[0].id == meeting.id
    assert dao.list_meetings(state="RECORDING")[0].id == meeting.id
    assert dao.list_meetings(state="DELIVERED") == []


def test_meeting_folder_is_absolute(dao: Dao) -> None:
    with pytest.raises(ValueError, match="absolute"):
        dao.insert_meeting(folder="relative/path", source="manual")


def test_illegal_transition_raises(dao: Dao, tmp_path: Path) -> None:
    meeting = dao.insert_meeting(folder=tmp_path / "m", source="manual")
    with pytest.raises(IllegalTransition):
        dao.set_state(meeting.id, MeetingState.DELIVERED)
    assert dao.require_meeting(meeting.id).state == "RECORDING"
    for state in (
        MeetingState.RECORDED,
        MeetingState.TRANSCRIBING,
        MeetingState.TRANSCRIBED,
        MeetingState.SUMMARIZING,
        MeetingState.SUMMARIZED,
        MeetingState.RENDERED,
        MeetingState.DELIVERED,
    ):
        dao.set_state(meeting.id, state)
    assert dao.require_meeting(meeting.id).state == "DELIVERED"


def test_data_root_change_preserves_old(dao: Dao, tmp_path: Path) -> None:
    first_root = tmp_path / "root-a"
    second_root = tmp_path / "root-b"
    first = dao.insert_meeting(folder=first_root / "m1", source="manual")
    second = dao.insert_meeting(folder=second_root / "m2", source="manual")
    assert dao.require_meeting(first.id).folder == str(first_root / "m1")
    assert dao.require_meeting(second.id).folder == str(second_root / "m2")
    assert Path(dao.require_meeting(first.id).folder).is_absolute()


def test_glossary_and_settings_roundtrip(dao: Dao) -> None:
    dao.upsert_term(GlossaryTerm("Kubernetes", kind="tech", aliases="k8s"))
    dao.upsert_term(GlossaryTerm("Kubernetes", kind="tech", aliases="k8s,קוברנטיס"))
    terms = dao.glossary()
    assert len(terms) == 1 and terms[0].aliases == "k8s,קוברנטיס"
    dao.set_setting("ui.language", "he")
    dao.set_setting("ui.language", "en")
    assert dao.setting("ui.language") == "en"
    assert dao.setting("missing", "fallback") == "fallback"
    dao.delete_term("Kubernetes")
    assert dao.glossary() == []


def test_detector_events_roundtrip(dao: Dao) -> None:
    dao.add_detector_event(
        peak_score=9,
        evidence=[{"weight": 3, "code": "mic.known_app", "detail": "Zoom.exe"}],
        outcome="shadow",
        process="Zoom.exe",
        window_title="Zoom Meeting",
    )
    events = dao.detector_events()
    assert len(events) == 1
    assert events[0].outcome == "shadow"
    assert events[0].evidence_list[0]["code"] == "mic.known_app"


def test_search_covers_titles_and_action_items(dao: Dao, tmp_path: Path) -> None:
    """The bug this exists for: a word in a meeting's *name* returned nothing.

    Search read `transcript_turns` and nothing else, so "roadmap" — which the
    calendar supplied and nobody said out loud — answered "Nothing matched." The
    engine was fine; it was pointed at one third of the corpus.
    """
    m = dao.insert_meeting(meeting_id="r", folder=tmp_path / "r", source="manual")
    dao.update_meeting(m.id, title="Q4 roadmap working session")
    dao.index_turns(m.id, [Turn(0, "ME", 0, "activation is down six percent")])
    dao.replace_action_items(m.id, [("me", "Circulate the one-pager", None, None)])

    titles = dao.search("roadmap")
    assert [h.kind for h in titles] == ["title"]
    assert "[roadmap]" in titles[0].snippet

    actions = dao.search("one-pager")
    assert [h.kind for h in actions] == ["action"]

    # The transcript path still works, and still carries the moment.
    spoken = dao.search("activation")
    assert [h.kind for h in spoken] == ["transcript"]
    assert spoken[0].at_ms == 0

    # A title match outranks a sentence match for the same word.
    dao.index_turns(m.id, [Turn(0, "ME", 0, "the roadmap is agreed")])
    both = dao.search("roadmap")
    assert [h.kind for h in both] == ["title", "transcript"]


def test_search_survives_fts_operators_and_punctuation(dao: Dao, db, tmp_path: Path) -> None:
    """`MATCH` takes a query *language*, not a search term.

    The raw string went straight into it, so "AND", "c++" and a lone apostrophe
    each raised OperationalError and /api/search answered 500 — typing an
    ordinary English word broke the search box.
    """
    if not capabilities(db).fts:
        pytest.skip("FTS5 unavailable in this SQLite build")
    m = dao.insert_meeting(meeting_id="p", folder=tmp_path / "p", source="manual")
    dao.index_turns(m.id, [Turn(0, "ME", 0, "we shipped C++ and the standup agreed")])

    for hostile in ("AND", "OR", "NOT", "c++", 'don"t', "'", "*", "NEAR(a b)", "-x", "^"):
        dao.search(hostile)  # must not raise

    assert dao.search("standup"), "a plain word still matches"
    assert dao.search("stand"), "the last token is a prefix match, as a search box implies"
