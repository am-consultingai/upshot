"""Search reaches summaries, and Hebrew words are found inside their prefixed forms."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.clock import FakeClock
from app.db.dao import Dao, Turn, capabilities, connect
from app.pipeline.stages.render import backfill_search


@pytest.fixture
def dao(tmp_path: Path):  # type: ignore[no-untyped-def]
    conn = connect(tmp_path / "index.db")
    d = Dao(conn, FakeClock())
    d.seed_ids(11)
    yield d
    conn.close()


def meeting(dao: Dao, tmp_path: Path, meeting_id: str) -> str:
    return dao.insert_meeting(
        meeting_id=meeting_id, folder=tmp_path / meeting_id, source="manual"
    ).id


def kinds(dao: Dao, query: str) -> list[tuple[str, str]]:
    return [(hit.meeting_id, hit.kind) for hit in dao.search(query)]


def test_a_word_only_the_summary_holds_is_found(dao: Dao, tmp_path: Path) -> None:
    m = meeting(dao, tmp_path, "m1")
    dao.index_turns(m, [Turn(0, "ME", 0, "Let us talk about the launch.")])
    dao.index_summary(m, "Decision: the Berlin launch moves to January.")
    assert kinds(dao, "Berlin") == [("m1", "summary")]
    hit = dao.search("Berlin")[0]
    assert "[Berlin]" in hit.snippet and hit.at_ms == 0 and hit.speaker == ""
    # Re-rendering replaces the copy rather than adding a second one.
    dao.index_summary(m, "Decision: the Paris launch moves to January.")
    assert kinds(dao, "Berlin") == []
    assert kinds(dao, "Paris") == [("m1", "summary")]


@pytest.mark.parametrize("fts", [True, False])
def test_hebrew_is_found_inside_the_word_its_preposition_is_glued_to(
    tmp_path: Path, fts: bool
) -> None:
    conn = connect(tmp_path / "index.db", fts=fts)
    dao = Dao(conn, FakeClock())
    m = meeting(dao, tmp_path, "he")
    dao.index_turns(m, [Turn(0, "THEM", 4000, "דיברנו בתקציב של הרבעון")])
    dao.index_summary(m, "הוחלט לקצץ בתקציב השיווק")
    assert capabilities(conn).fts is fts
    found = kinds(dao, "תקציב")
    assert ("he", "transcript") in found and ("he", "summary") in found
    transcript = next(hit for hit in dao.search("תקציב") if hit.kind == "transcript")
    assert "[תקציב]" in transcript.snippet
    assert transcript.at_ms == 4000
    conn.close()


def test_a_two_letter_word_is_still_found(dao: Dao, tmp_path: Path) -> None:
    """Trigrams cannot match two characters; the substring fallback does."""
    m = meeting(dao, tmp_path, "m2")
    dao.index_turns(m, [Turn(0, "ME", 0, "we moved it to Q3 after all")])
    assert kinds(dao, "Q3") == [("m2", "transcript")]


def test_the_last_word_is_still_a_prefix_and_operators_are_literal(
    dao: Dao, tmp_path: Path
) -> None:
    m = meeting(dao, tmp_path, "m3")
    dao.index_turns(m, [Turn(0, "ME", 0, "the standup agreed on C++ AND Rust")])
    assert kinds(dao, "stand") == [("m3", "transcript")]
    for hostile in ("AND", "c++", 'don"t', "NEAR(a b)", "*", "-x"):
        dao.search(hostile)  # must not raise


def test_deleting_a_meeting_takes_its_summary_out_of_search(dao: Dao, tmp_path: Path) -> None:
    m = meeting(dao, tmp_path, "m4")
    dao.index_summary(m, "Unique word zebracorn")
    dao.delete_meeting(m)
    assert kinds(dao, "zebracorn") == []


def test_a_database_from_before_is_reindexed_and_its_summaries_backfilled(tmp_path: Path) -> None:
    """The word index is replaced by the trigram one without losing a transcript line,
    and summaries that exist only as files are read once."""
    db = tmp_path / "index.db"
    conn = connect(db)
    dao = Dao(conn, FakeClock())
    m = meeting(dao, tmp_path, "old")
    dao.index_turns(m, [Turn(0, "ME", 1000, "the churn numbers are in")])
    # Undo what this version did, to look like a database the previous one left.
    conn.execute("DROP TABLE search_fts")
    conn.execute("CREATE VIRTUAL TABLE transcripts_fts USING fts5(meeting_id UNINDEXED, text)")
    conn.execute("DELETE FROM meeting_texts")
    conn.commit()
    conn.close()
    folder = tmp_path / "old"
    folder.mkdir()
    (folder / "notes.json").write_text(
        json.dumps({"summary_html": "<p>We agreed to <b>raise prices</b>.</p>"}), encoding="utf-8"
    )

    conn = connect(db)
    dao = Dao(conn, FakeClock())
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "transcripts_fts" not in tables and "search_fts" in tables
    assert kinds(dao, "churn") == [("old", "transcript")]
    assert backfill_search(dao) == 1
    assert kinds(dao, "raise prices") == [("old", "summary")]
    assert backfill_search(dao) == 0, "each folder is read once"
    conn.close()


def test_no_fts_database_still_searches_summaries(tmp_path: Path) -> None:
    conn = connect(tmp_path / "index.db", fts=False)
    dao = Dao(conn, FakeClock())
    m = meeting(dao, tmp_path, "nf")
    dao.index_summary(m, "The pricing page ships Monday")
    assert kinds(dao, "pricing") == [("nf", "summary")]
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("SELECT * FROM search_fts")
    conn.close()


def test_a_meeting_deleted_behind_the_index_is_not_a_hit(dao: Dao, tmp_path: Path) -> None:
    """FTS tables do not cascade; a raw delete must not leave a ghost result."""
    m = meeting(dao, tmp_path, "ghost")
    dao.index_summary(m, "kubernetes migration")
    dao.conn.execute("DELETE FROM meetings WHERE id = ?", (m,))
    assert kinds(dao, "kubernetes") == []
