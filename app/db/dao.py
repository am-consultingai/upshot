"""Typed database accessors. No ORM — five tables and a handful of statements."""

from __future__ import annotations

import json
import random
import sqlite3
import threading
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from app import paths
from app.clock import Clock, SystemClock, iso
from app.db import migrate as migrations
from app.errors import IllegalTransition
from app.log import get
from app.pipeline.states import LEGAL_TRANSITIONS, MeetingState

log = get(__name__)

MEETING_COLUMNS = (
    "id",
    "folder",
    "source",
    "state",
    "title",
    "title_source",
    "language",
    "language_conf",
    "summary_language",
    "started_at",
    "ended_at",
    "duration_s",
    "profile",
    "sensitive",
    "evidence_json",
    "calendar_json",
    "error",
    "created_at",
    "updated_at",
)


@dataclass(frozen=True, slots=True)
class Meeting:
    id: str
    folder: str
    source: str
    state: str
    started_at: str
    profile: str
    created_at: str
    updated_at: str
    title: str | None = None
    title_source: str | None = None
    language: str | None = None
    language_conf: float | None = None
    summary_language: str | None = None
    ended_at: str | None = None
    duration_s: int | None = None
    sensitive: int = 0
    evidence_json: str | None = None
    calendar_json: str | None = None
    error: str | None = None

    @property
    def path(self) -> Path:
        return Path(self.folder)

    @property
    def meeting_state(self) -> MeetingState:
        return MeetingState(self.state)

    @property
    def evidence(self) -> list[dict[str, Any]]:
        if not self.evidence_json:
            return []
        loaded = json.loads(self.evidence_json)
        return list(loaded) if isinstance(loaded, list) else []

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Turn:
    seq: int
    speaker: str
    at_ms: int
    text: str


@dataclass(frozen=True, slots=True)
class SearchHit:
    meeting_id: str
    speaker: str
    at_ms: int
    text: str
    snippet: str


#: Owners the prompt is allowed to use when it cannot name a person. ``ME`` is the
#: person running this recorder, which is the only one the inbox can act on.
MINE = frozenset({"me", "myself", "i"})


@dataclass(frozen=True, slots=True)
class ActionItem:
    id: int
    meeting_id: str
    seq: int
    who: str
    what: str
    due: str | None
    at_ms: int | None
    mine: bool
    done_at: str | None
    #: Filled in by :meth:`Dao.action_items`; the row itself does not carry it.
    meeting_title: str | None = None
    meeting_started_at: str | None = None

    @property
    def done(self) -> bool:
        return self.done_at is not None

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "done": self.done}


def normalize_action(what: str) -> str:
    """The match key that carries a tick across a re-summarize.

    Casefolded and whitespace-squeezed, so the model rewording its own punctuation
    does not silently reopen something the user has already ticked off.
    """
    return " ".join(what.split()).casefold()


@dataclass(frozen=True, slots=True)
class DetectorEvent:
    id: int
    at: str
    process: str | None
    window_title: str | None
    peak_score: int
    evidence: str
    outcome: str
    meeting_id: str | None

    @property
    def evidence_list(self) -> list[dict[str, Any]]:
        loaded = json.loads(self.evidence)
        return list(loaded) if isinstance(loaded, list) else []


@dataclass(frozen=True, slots=True)
class GlossaryTerm:
    term: str
    kind: str | None = None
    aliases: str | None = None
    note: str | None = None
    hits: int = 0


@dataclass
class Capabilities:
    fts: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


class Result:
    """A materialized cursor.

    Rows are read while the connection lock is held, so a caller can iterate them at
    leisure from any thread.
    """

    __slots__ = ("_index", "lastrowid", "rowcount", "rows")

    def __init__(self, rows: list[Any], lastrowid: int | None, rowcount: int) -> None:
        self.rows = rows
        self.lastrowid = lastrowid
        self.rowcount = rowcount
        self._index = 0

    def fetchall(self) -> list[Any]:
        return self.rows

    def fetchone(self) -> Any:
        if self._index >= len(self.rows):
            return None
        row = self.rows[self._index]
        self._index += 1
        return row

    def __iter__(self) -> Any:
        return iter(self.rows)

    def __len__(self) -> int:
        return len(self.rows)


class Connection(sqlite3.Connection):
    """One process-wide connection, serialized.

    SQLite is compiled serialized, but an **FTS5 cursor is not** safe for concurrent use
    on one connection — two threads searching at once raise
    ``InterfaceError: bad parameter or other API misuse``. The API serves several
    concurrent requests per page, so every statement runs under this lock and its rows
    are materialized before the lock is released.
    """

    up_capabilities: Capabilities

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.up_lock = threading.RLock()

    def execute(self, sql: str, parameters: Any = (), /) -> Any:
        with self.up_lock:
            cursor = super().execute(sql, parameters)
            try:
                rows = cursor.fetchall()
            except sqlite3.ProgrammingError:  # a statement with no result set
                rows = []
            return Result(rows, cursor.lastrowid, cursor.rowcount)

    def executemany(self, sql: str, parameters: Any, /) -> Any:
        with self.up_lock:
            cursor = super().executemany(sql, parameters)
            return Result([], cursor.lastrowid, cursor.rowcount)


def connect(path: Path | None = None, *, fts: bool = True) -> Connection:
    """Open (creating if needed) the index database, migrated and ready."""
    target = path or paths.db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(target), isolation_level=None, check_same_thread=False, factory=Connection
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    migrations.migrate(conn)
    conn.up_capabilities = Capabilities(fts=migrations.ensure_fts(conn, enabled=fts))
    return conn


def capabilities(conn: sqlite3.Connection) -> Capabilities:
    caps = getattr(conn, "up_capabilities", None)
    if isinstance(caps, Capabilities):
        return caps
    return Capabilities(fts=False)


def _row_to_action(row: sqlite3.Row) -> ActionItem:
    return ActionItem(
        id=row["id"],
        meeting_id=row["meeting_id"],
        seq=row["seq"],
        who=row["who"],
        what=row["what"],
        due=row["due"],
        at_ms=row["at_ms"],
        mine=bool(row["mine"]),
        done_at=row["done_at"],
        meeting_title=row["meeting_title"],
        meeting_started_at=row["meeting_started_at"],
    )


def _row_to_meeting(row: sqlite3.Row) -> Meeting:
    return Meeting(**{key: row[key] for key in MEETING_COLUMNS})


class Dao:
    """Everything that touches the database goes through here."""

    def __init__(self, conn: sqlite3.Connection, clock: Clock | None = None) -> None:
        self.conn = conn
        self.clock = clock or SystemClock()
        self._rand = random.Random()

    # -- ids ---------------------------------------------------------------

    def seed_ids(self, seed: int) -> None:
        """Tests seed the id suffix instead of relying on entropy."""
        self._rand = random.Random(seed)

    def new_meeting_id(self, started_at: datetime | None = None) -> str:
        when = started_at or self.clock.now()
        suffix = f"{self._rand.randrange(16**6):06x}"
        return f"{when:%Y-%m-%d_%H%M}_{suffix}"

    # -- meetings ----------------------------------------------------------

    def insert_meeting(
        self,
        *,
        meeting_id: str | None = None,
        folder: Path | str,
        source: str,
        state: MeetingState | str = MeetingState.RECORDING,
        started_at: datetime | str | None = None,
        profile: str = "auto",
        title: str | None = None,
        title_source: str | None = None,
        sensitive: bool = False,
        evidence: Sequence[dict[str, Any]] | None = None,
        calendar_json: str | None = None,
    ) -> Meeting:
        folder_path = Path(folder)
        if not folder_path.is_absolute():
            raise ValueError(f"meetings.folder must be absolute, got {folder!r}")
        now = iso(self.clock.now())
        started = started_at if isinstance(started_at, str) else iso(started_at or self.clock.now())
        mid = meeting_id or self.new_meeting_id()
        meeting = Meeting(
            id=mid,
            folder=str(folder_path),
            source=source,
            state=str(state),
            started_at=started,
            profile=profile,
            created_at=now,
            updated_at=now,
            title=title,
            title_source=title_source,
            sensitive=int(sensitive),
            evidence_json=json.dumps(list(evidence), ensure_ascii=False) if evidence else None,
            calendar_json=calendar_json,
        )
        columns = ",".join(MEETING_COLUMNS)
        marks = ",".join("?" for _ in MEETING_COLUMNS)
        values = [getattr(meeting, col) for col in MEETING_COLUMNS]
        self.conn.execute(f"INSERT INTO meetings({columns}) VALUES ({marks})", values)
        return meeting

    def get_meeting(self, meeting_id: str) -> Meeting | None:
        row = self.conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        return _row_to_meeting(row) if row else None

    def require_meeting(self, meeting_id: str) -> Meeting:
        meeting = self.get_meeting(meeting_id)
        if meeting is None:
            raise KeyError(f"no such meeting: {meeting_id}")
        return meeting

    def list_meetings(
        self,
        *,
        frm: str | None = None,
        to: str | None = None,
        state: str | None = None,
        q: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[Meeting]:
        where: list[str] = []
        args: list[Any] = []
        if frm:
            where.append("started_at >= ?")
            args.append(frm)
        if to:
            where.append("started_at <= ?")
            args.append(to)
        if state:
            where.append("state = ?")
            args.append(state)
        if q:
            ids = {hit.meeting_id for hit in self.search(q, limit=500)}
            title_rows = self.conn.execute(
                "SELECT id FROM meetings WHERE title LIKE ?", (f"%{q}%",)
            ).fetchall()
            ids.update(row["id"] for row in title_rows)
            if not ids:
                return []
            where.append(f"id IN ({','.join('?' for _ in ids)})")
            args.extend(sorted(ids))
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = self.conn.execute(
            f"SELECT * FROM meetings {clause} ORDER BY started_at DESC LIMIT ? OFFSET ?",
            [*args, limit, offset],
        ).fetchall()
        return [_row_to_meeting(row) for row in rows]

    def update_meeting(self, meeting_id: str, **fields: Any) -> Meeting:
        if "state" in fields:
            raise ValueError("use set_state() — state changes are guarded")
        unknown = set(fields) - set(MEETING_COLUMNS)
        if unknown:
            raise ValueError(f"unknown meeting columns: {sorted(unknown)}")
        fields["updated_at"] = iso(self.clock.now())
        assignments = ",".join(f"{key} = ?" for key in fields)
        self.conn.execute(
            f"UPDATE meetings SET {assignments} WHERE id = ?",
            [*fields.values(), meeting_id],
        )
        return self.require_meeting(meeting_id)

    def set_state(self, meeting_id: str, new: MeetingState | str) -> Meeting:
        meeting = self.require_meeting(meeting_id)
        old = MeetingState(meeting.state)
        target = MeetingState(new)
        if (old, target) not in LEGAL_TRANSITIONS:
            raise IllegalTransition(f"{meeting_id}: {old} → {target} is not a legal transition")
        self.conn.execute(
            "UPDATE meetings SET state = ?, updated_at = ? WHERE id = ?",
            (str(target), iso(self.clock.now()), meeting_id),
        )
        return replace(meeting, state=str(target))

    def delete_meeting(self, meeting_id: str) -> None:
        self.clear_turns(meeting_id)
        self.conn.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))

    # -- transcripts and search -------------------------------------------

    def index_turns(self, meeting_id: str, turns: Iterable[Turn]) -> int:
        """Replace this meeting's indexed turns. Idempotent by construction."""
        self.clear_turns(meeting_id)
        count = 0
        for turn in turns:
            self.conn.execute(
                "INSERT INTO transcript_turns(meeting_id, seq, speaker, at_ms, text) "
                "VALUES (?,?,?,?,?)",
                (meeting_id, turn.seq, turn.speaker, turn.at_ms, turn.text),
            )
            if capabilities(self.conn).fts:
                self.conn.execute(
                    "INSERT INTO transcripts_fts(meeting_id, speaker, at_ms, text) "
                    "VALUES (?,?,?,?)",
                    (meeting_id, turn.speaker, turn.at_ms, turn.text),
                )
            count += 1
        return count

    def clear_turns(self, meeting_id: str) -> None:
        self.conn.execute("DELETE FROM transcript_turns WHERE meeting_id = ?", (meeting_id,))
        if capabilities(self.conn).fts:
            self.conn.execute("DELETE FROM transcripts_fts WHERE meeting_id = ?", (meeting_id,))

    def turns(self, meeting_id: str) -> list[Turn]:
        rows = self.conn.execute(
            "SELECT seq, speaker, at_ms, text FROM transcript_turns "
            "WHERE meeting_id = ? ORDER BY seq",
            (meeting_id,),
        ).fetchall()
        return [Turn(r["seq"], r["speaker"], r["at_ms"], r["text"]) for r in rows]

    def search(self, query: str, *, limit: int = 50) -> list[SearchHit]:
        """FTS5 when available, LIKE over ``transcript_turns`` when it is not."""
        query = query.strip()
        if not query:
            return []
        if capabilities(self.conn).fts:
            rows = self.conn.execute(
                "SELECT meeting_id, speaker, at_ms, text, "
                "snippet(transcripts_fts, 3, '[', ']', '…', 12) AS snip "
                "FROM transcripts_fts WHERE transcripts_fts MATCH ? LIMIT ?",
                (query, limit),
            ).fetchall()
            return [
                SearchHit(r["meeting_id"], r["speaker"], r["at_ms"], r["text"], r["snip"])
                for r in rows
            ]
        rows = self.conn.execute(
            "SELECT meeting_id, speaker, at_ms, text FROM transcript_turns "
            "WHERE text LIKE ? ORDER BY meeting_id, seq LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()
        hits = []
        for row in rows:
            text = row["text"]
            idx = text.lower().find(query.lower())
            start = max(0, idx - 30)
            snippet = ("…" if start else "") + text[start : idx + len(query) + 30]
            hits.append(SearchHit(row["meeting_id"], row["speaker"], row["at_ms"], text, snippet))
        return hits

    # -- action items ------------------------------------------------------

    def replace_action_items(
        self, meeting_id: str, items: Sequence[tuple[str, str, str | None, int | None]]
    ) -> int:
        """Replace this meeting's action items, carrying the done state forward.

        Each item is ``(who, what, due, at_ms)``. Re-summarizing is a routine act —
        the prompt is editable and pressing Summarize redoes the work with no
        staleness check — so it must not cost the user the ticks they have made.
        What is ticked is theirs; what the list says is the model's.
        """
        done = {
            row["norm"]: row["done_at"]
            for row in self.conn.execute(
                "SELECT norm, done_at FROM action_items "
                "WHERE meeting_id = ? AND done_at IS NOT NULL",
                (meeting_id,),
            ).fetchall()
        }
        self.conn.execute("DELETE FROM action_items WHERE meeting_id = ?", (meeting_id,))
        now = iso(self.clock.now())
        count = 0
        for who, what, due, at_ms in items:
            what = what.strip()
            who = who.strip()
            if not what:
                continue
            norm = normalize_action(what)
            self.conn.execute(
                "INSERT INTO action_items"
                "(meeting_id, seq, who, what, norm, due, at_ms, mine, done_at, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    meeting_id,
                    count,
                    who or "?",
                    what,
                    norm,
                    (due or None),
                    at_ms,
                    int(who.casefold() in MINE),
                    done.get(norm),
                    now,
                ),
            )
            count += 1
        return count

    def action_items(
        self,
        *,
        meeting_id: str | None = None,
        open_only: bool = False,
        limit: int = 500,
    ) -> list[ActionItem]:
        """Across every meeting unless one is named. Mine first, newest meeting first."""
        where: list[str] = []
        args: list[Any] = []
        if meeting_id:
            where.append("a.meeting_id = ?")
            args.append(meeting_id)
        if open_only:
            where.append("a.done_at IS NULL")
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = self.conn.execute(
            "SELECT a.*, m.title AS meeting_title, m.started_at AS meeting_started_at "
            f"FROM action_items a JOIN meetings m ON m.id = a.meeting_id {clause} "
            "ORDER BY a.mine DESC, m.started_at DESC, a.seq ASC LIMIT ?",
            [*args, limit],
        ).fetchall()
        return [_row_to_action(row) for row in rows]

    def action_item(self, item_id: int) -> ActionItem | None:
        row = self.conn.execute(
            "SELECT a.*, m.title AS meeting_title, m.started_at AS meeting_started_at "
            "FROM action_items a JOIN meetings m ON m.id = a.meeting_id WHERE a.id = ?",
            (item_id,),
        ).fetchone()
        return _row_to_action(row) if row else None

    def set_action_done(self, item_id: int, *, done: bool) -> ActionItem | None:
        self.conn.execute(
            "UPDATE action_items SET done_at = ? WHERE id = ?",
            (iso(self.clock.now()) if done else None, item_id),
        )
        return self.action_item(item_id)

    # -- glossary ----------------------------------------------------------

    def glossary(self) -> list[GlossaryTerm]:
        rows = self.conn.execute("SELECT * FROM glossary ORDER BY term").fetchall()
        return [
            GlossaryTerm(r["term"], r["kind"], r["aliases"], r["note"], r["hits"] or 0)
            for r in rows
        ]

    def upsert_term(self, term: GlossaryTerm) -> None:
        self.conn.execute(
            "INSERT INTO glossary(term, kind, aliases, note, hits) VALUES (?,?,?,?,?) "
            "ON CONFLICT(term) DO UPDATE SET kind=excluded.kind, aliases=excluded.aliases, "
            "note=excluded.note",
            (term.term, term.kind, term.aliases, term.note, term.hits),
        )

    def delete_term(self, term: str) -> None:
        self.conn.execute("DELETE FROM glossary WHERE term = ?", (term,))

    # -- settings ----------------------------------------------------------

    def setting(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO settings(key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # -- detector events ---------------------------------------------------

    def add_detector_event(
        self,
        *,
        peak_score: int,
        evidence: Sequence[dict[str, Any]],
        outcome: str,
        process: str | None = None,
        window_title: str | None = None,
        meeting_id: str | None = None,
        at: datetime | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO detector_events(at, process, window_title, peak_score, evidence, "
            "outcome, meeting_id) VALUES (?,?,?,?,?,?,?)",
            (
                iso(at or self.clock.now()),
                process,
                window_title,
                peak_score,
                json.dumps(list(evidence), ensure_ascii=False),
                outcome,
                meeting_id,
            ),
        )
        return int(cur.lastrowid or 0)

    def detector_events(self, limit: int = 50) -> list[DetectorEvent]:
        rows = self.conn.execute(
            "SELECT * FROM detector_events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            DetectorEvent(
                r["id"],
                r["at"],
                r["process"],
                r["window_title"],
                r["peak_score"],
                r["evidence"],
                r["outcome"],
                r["meeting_id"],
            )
            for r in rows
        ]
