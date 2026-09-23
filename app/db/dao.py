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
    "speaker_names",
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
    #: JSON: transcript speaker slot -> the name the user gave it. See :attr:`speakers`.
    speaker_names: str | None = None

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

    @property
    def speakers(self) -> dict[str, str]:
        """Who the user says is behind each speaker slot ("THEM_1" -> "Dana").

        A mapping laid over the transcript at read time rather than a rewrite of it: the
        slots are the recorder's and the diariser's, and a name typed against the wrong
        slot has to be correctable without re-transcribing anything.
        """
        if not self.speaker_names:
            return {}
        try:
            loaded = json.loads(self.speaker_names)
        except ValueError:
            return {}
        if not isinstance(loaded, dict):
            return {}
        return {str(k): str(v) for k, v in loaded.items() if isinstance(v, str) and v.strip()}

    def as_dict(self) -> dict[str, Any]:
        # Parsed, so meta.json and the API both carry an object rather than a JSON string
        # inside a JSON document.
        return {**asdict(self), "speaker_names": self.speakers}


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
    #: Where the match came from: ``title``, ``action`` or ``transcript``. The
    #: screen renders each differently — a title hit has no speaker and no
    #: timestamp, so it cannot be labelled "they said" or seeked to.
    kind: str = "transcript"



def _fts_query(raw: str) -> str:
    """Turn what someone typed into an FTS5 expression that cannot be a syntax error.

    The user's string used to go straight into ``MATCH``, where it is not a search
    term but a *query language*. So ``AND``, ``c++``, ``don"t`` and a lone
    apostrophe each raised ``OperationalError`` and the endpoint answered 500 —
    typing an ordinary English word broke the search box.

    Every token is quoted (which makes operators and punctuation literal) and the
    last one gets a prefix star, because the last word in a search box is usually
    still being typed.
    """
    tokens = [token for token in raw.split() if token]
    if not tokens:
        return ""
    quoted = ['"' + token.replace('"', '""') + '"' for token in tokens]
    quoted[-1] += "*"
    return " ".join(quoted)


def _mark(text: str, query: str, *, context: int = 40) -> str:
    """A snippet in the same bracket convention SQLite's ``snippet()`` produces.

    The screen splits on ``[`` and ``]`` to highlight, so a hit found by LIKE has
    to arrive looking like a hit found by FTS.
    """
    index = text.lower().find(query.lower())
    if index < 0:
        return text
    start = max(0, index - context)
    end = min(len(text), index + len(query) + context)
    return (
        ("…" if start else "")
        + text[start:index]
        + "["
        + text[index : index + len(query)]
        + "]"
        + text[index + len(query) : end]
        + ("…" if end < len(text) else "")
    )


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
    #: The lighter second line: why it matters, what it unblocks, who is waiting.
    detail: str | None = None
    #: YYYY-MM-DD. The stored value, or — for a row that has none — ``due`` resolved
    #: against the meeting's own date. See :func:`_row_to_action`.
    due_at: str | None = None
    snoozed_until: str | None = None
    #: ``model`` (re-summarizing replaces it) or ``user`` (typed in; nothing replaces it).
    source: str = "model"

    @property
    def done(self) -> bool:
        return self.done_at is not None

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "done": self.done}


#: Fields an action item accepts from the summarizer, the seed and the API alike.
ActionInput = dict[str, Any] | tuple[Any, ...]


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


def _resolved_due_at(stored: str | None, due: str | None, started_at: str | None) -> str | None:
    """The stored date, or what ``due`` meant on the day the meeting happened.

    Lazy on purpose: every row written before ``due_at`` existed gets a date the moment
    it is read, with no backfill, and a better resolver improves old rows for free.
    """
    if stored:
        return stored
    if not due or not started_at:
        return None
    from app.due import anchor_date, resolve_due

    anchor = anchor_date(started_at)
    if anchor is None:
        return None
    resolved = resolve_due(due, anchor)
    return resolved.isoformat() if resolved else None


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
        detail=row["detail"],
        due_at=_resolved_due_at(row["due_at"], row["due"], row["meeting_started_at"]),
        snoozed_until=row["snoozed_until"],
        source=row["source"] or "model",
    )


def _action_fields(item: ActionInput) -> dict[str, Any]:
    """One item as a dict, whichever shape the caller had it in.

    The tuple ``(who, what, due, at_ms)`` is the shape this took before ``detail`` and
    ``due_at`` existed; it is still accepted so a caller holding one does not have to be
    rewritten to say nothing new.
    """
    if isinstance(item, dict):
        return item
    who, what, due, at_ms = item
    return {"who": who, "what": what, "due": due, "at_ms": at_ms}


#: A tag is a label, not a note.
MAX_TAGS = 12
MAX_TAG_CHARS = 40


def normalize_tag(tag: str) -> str:
    """Stripped and whitespace-squeezed; the casing is the user's."""
    return " ".join(str(tag).split())


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
        """Titles, action items and transcripts — in that order of confidence.

        It searched transcripts *only* until 2026-09-22, which meant a word that
        appears in a meeting's name and nowhere in the speech — "roadmap", a
        customer's name, anything the calendar supplied — returned nothing at all.
        The engine was working; it was pointed at one third of the corpus.

        Ordering is by kind rather than by date. A title match is the strongest
        signal there is ("the meeting I mean is called this"), an action item is
        next, and the sentence hits follow. Within a kind, newest first.

        Not covered: the summary. It is written to ``summary.html`` in the meeting
        folder rather than to a column, so searching it means reading and stripping
        a file per meeting on every keystroke. The right fix is a plain-text copy in
        the database at render time, which is a migration, not a query.
        """
        query = query.strip()
        if not query:
            return []
        hits: list[SearchHit] = []
        like = f"%{query}%"

        for row in self.conn.execute(
            "SELECT id, title FROM meetings "
            "WHERE title IS NOT NULL AND lower(title) LIKE lower(?) "
            "ORDER BY started_at DESC LIMIT ?",
            (like, limit),
        ).fetchall():
            hits.append(
                SearchHit(row["id"], "", 0, row["title"], _mark(row["title"], query), "title")
            )

        for row in self.conn.execute(
            "SELECT meeting_id, what, at_ms FROM action_items "
            "WHERE lower(what) LIKE lower(?) ORDER BY created_at DESC LIMIT ?",
            (like, limit),
        ).fetchall():
            hits.append(
                SearchHit(
                    row["meeting_id"],
                    "",
                    row["at_ms"] or 0,
                    row["what"],
                    _mark(row["what"], query),
                    "action",
                )
            )

        hits.extend(self._search_turns(query, limit=limit))
        return hits[:limit]

    def _search_turns(self, query: str, *, limit: int) -> list[SearchHit]:
        """FTS5 when available, LIKE over ``transcript_turns`` when it is not."""
        if capabilities(self.conn).fts:
            match = _fts_query(query)
            if not match:
                return []
            rows = self.conn.execute(
                "SELECT meeting_id, speaker, at_ms, text, "
                "snippet(transcripts_fts, 3, '[', ']', '…', 12) AS snip "
                "FROM transcripts_fts WHERE transcripts_fts MATCH ? LIMIT ?",
                (match, limit),
            ).fetchall()
            return [
                SearchHit(
                    r["meeting_id"], r["speaker"], r["at_ms"], r["text"], r["snip"], "transcript"
                )
                for r in rows
            ]
        rows = self.conn.execute(
            "SELECT meeting_id, speaker, at_ms, text FROM transcript_turns "
            "WHERE text LIKE ? ORDER BY meeting_id, seq LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()
        return [
            SearchHit(
                r["meeting_id"], r["speaker"], r["at_ms"], r["text"], _mark(r["text"], query),
                "transcript",
            )
            for r in rows
        ]

    # -- action items ------------------------------------------------------

    def replace_action_items(self, meeting_id: str, items: Sequence[ActionInput]) -> int:
        """Replace the model's action items for this meeting, keeping what is the user's.

        Each item is a dict of ``who, what, due, at_ms, detail, due_at`` (the old
        ``(who, what, due, at_ms)`` tuple is still accepted). Re-summarizing is a routine
        act — the prompt is editable and pressing Summarize redoes the work with no
        staleness check — so it must not cost the user anything they did:

        - ``done_at`` and ``snoozed_until`` are carried forward by the normalised text.
        - Rows with ``source = 'user'`` are not the model's and are never deleted. They
          are renumbered to follow the model's list, and a model item that repeats one of
          them word for word is dropped rather than shown twice.

        What is ticked, snoozed or typed in is theirs; what the list says is the model's.
        """
        kept = {
            row["norm"]: (row["done_at"], row["snoozed_until"])
            for row in self.conn.execute(
                "SELECT norm, done_at, snoozed_until FROM action_items "
                "WHERE meeting_id = ? AND source = 'model' "
                "AND (done_at IS NOT NULL OR snoozed_until IS NOT NULL)",
                (meeting_id,),
            ).fetchall()
        }
        users = self.conn.execute(
            "SELECT id, norm FROM action_items WHERE meeting_id = ? AND source = 'user' "
            "ORDER BY seq",
            (meeting_id,),
        ).fetchall()
        user_norms = {row["norm"] for row in users}
        self.conn.execute(
            "DELETE FROM action_items WHERE meeting_id = ? AND source = 'model'", (meeting_id,)
        )
        # Out of the way of UNIQUE(meeting_id, seq) while the model's rows go in at 0..n.
        self.conn.execute(
            "UPDATE action_items SET seq = seq + 1000000 WHERE meeting_id = ?", (meeting_id,)
        )
        now = iso(self.clock.now())
        count = 0
        for raw in items:
            fields = _action_fields(raw)
            what = str(fields.get("what") or "").strip()
            who = str(fields.get("who") or "").strip()
            if not what:
                continue
            norm = normalize_action(what)
            if norm in user_norms:
                continue
            done_at, snoozed = kept.get(norm, (None, None))
            at_ms = fields.get("at_ms")
            self.conn.execute(
                "INSERT INTO action_items"
                "(meeting_id, seq, who, what, norm, due, at_ms, mine, done_at, created_at, "
                "detail, due_at, snoozed_until, source) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'model')",
                (
                    meeting_id,
                    count,
                    who or "?",
                    what,
                    norm,
                    (str(fields.get("due") or "").strip() or None),
                    int(at_ms) if isinstance(at_ms, int | float) else None,
                    int(who.casefold() in MINE),
                    done_at,
                    now,
                    (str(fields.get("detail") or "").strip() or None),
                    fields.get("due_at") or None,
                    snoozed,
                ),
            )
            count += 1
        for offset, row in enumerate(users):
            self.conn.execute(
                "UPDATE action_items SET seq = ? WHERE id = ?", (count + offset, row["id"])
            )
        return count

    def add_action_item(
        self,
        meeting_id: str,
        *,
        what: str,
        who: str = "ME",
        due_at: str | None = None,
        detail: str | None = None,
    ) -> ActionItem:
        """An item the user typed in. ``source = 'user'``, so no re-summarize removes it."""
        what = what.strip()
        who = who.strip() or "ME"
        if not what:
            raise ValueError("an action item needs something to do")
        row = self.conn.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 AS next FROM action_items WHERE meeting_id = ?",
            (meeting_id,),
        ).fetchone()
        cursor = self.conn.execute(
            "INSERT INTO action_items"
            "(meeting_id, seq, who, what, norm, due, at_ms, mine, done_at, created_at, "
            "detail, due_at, snoozed_until, source) "
            "VALUES (?,?,?,?,?,NULL,NULL,?,NULL,?,?,?,NULL,'user')",
            (
                meeting_id,
                int(row["next"]),
                who,
                what,
                normalize_action(what),
                int(who.casefold() in MINE),
                iso(self.clock.now()),
                (detail or "").strip() or None,
                due_at or None,
            ),
        )
        item = self.action_item(int(cursor.lastrowid or 0))
        assert item is not None
        return item

    def update_action_item(self, item_id: int, **fields: Any) -> ActionItem | None:
        """Change what the user may change: done, dates, owner, wording, detail.

        ``done`` is a boolean, stamped with the clock; everything else is a column value,
        where None clears it. Rewording keeps ``norm`` in step, or the next re-summarize
        would carry the tick by the old wording; changing the owner keeps ``mine`` in step.
        """
        allowed = {"done", "due_at", "snoozed_until", "who", "what", "detail"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unknown action item fields: {sorted(unknown)}")
        columns: dict[str, Any] = {}
        if "done" in fields:
            columns["done_at"] = iso(self.clock.now()) if fields["done"] else None
        for key in ("due_at", "snoozed_until"):
            if key in fields:
                columns[key] = fields[key] or None
        if "detail" in fields:
            columns["detail"] = (fields["detail"] or "").strip() or None
        if fields.get("what") is not None:
            what = str(fields["what"]).strip()
            if not what:
                raise ValueError("an action item needs something to do")
            columns["what"] = what
            columns["norm"] = normalize_action(what)
        if fields.get("who") is not None:
            who = str(fields["who"]).strip() or "?"
            columns["who"] = who
            columns["mine"] = int(who.casefold() in MINE)
        if columns:
            assignments = ",".join(f"{key} = ?" for key in columns)
            self.conn.execute(
                f"UPDATE action_items SET {assignments} WHERE id = ?",
                [*columns.values(), item_id],
            )
        return self.action_item(item_id)

    def delete_action_item(self, item_id: int) -> bool:
        return bool(
            self.conn.execute("DELETE FROM action_items WHERE id = ?", (item_id,)).rowcount
        )

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

    def action_item_counts(self) -> dict[str, tuple[int, int]]:
        """Per meeting: how many commitments it recorded, and how many are still open.

        One grouped query rather than one per row: the timeline asks about every meeting
        it draws, and a library of two hundred meetings is not two hundred queries.
        """
        rows = self.conn.execute(
            "SELECT meeting_id, COUNT(*) AS total, "
            "SUM(CASE WHEN done_at IS NULL THEN 1 ELSE 0 END) AS still_open "
            "FROM action_items GROUP BY meeting_id"
        ).fetchall()
        return {row["meeting_id"]: (int(row["total"]), int(row["still_open"])) for row in rows}

    def action_item(self, item_id: int) -> ActionItem | None:
        row = self.conn.execute(
            "SELECT a.*, m.title AS meeting_title, m.started_at AS meeting_started_at "
            "FROM action_items a JOIN meetings m ON m.id = a.meeting_id WHERE a.id = ?",
            (item_id,),
        ).fetchone()
        return _row_to_action(row) if row else None

    def set_action_done(self, item_id: int, *, done: bool) -> ActionItem | None:
        return self.update_action_item(item_id, done=done)

    # -- failures ------------------------------------------------------------

    def failed_stages(self) -> dict[str, str]:
        """Per meeting, the earliest pipeline stage whose job has failed.

        One query for the whole list. "Earliest" is pipeline order, not time: a failed
        transcribe is the reason a failed summarize never had anything to work on.
        """
        from app.pipeline.states import STAGE_ORDER

        order = {str(stage): index for index, stage in enumerate(STAGE_ORDER)}
        out: dict[str, str] = {}
        for row in self.conn.execute(
            "SELECT meeting_id, stage FROM jobs WHERE state = 'failed'"
        ).fetchall():
            stage = str(row["stage"])
            current = out.get(row["meeting_id"])
            if current is None or order.get(stage, 99) < order.get(current, 99):
                out[row["meeting_id"]] = stage
        return out

    # -- tags ----------------------------------------------------------------

    def tags(self, meeting_id: str) -> list[str]:
        rows = self.conn.execute(
            # Insertion order, which set_tags makes the order the user gave them in.
            "SELECT tag FROM meeting_tags WHERE meeting_id = ? ORDER BY rowid",
            (meeting_id,),
        ).fetchall()
        return [row["tag"] for row in rows]

    def tags_by_meeting(self) -> dict[str, list[str]]:
        """Every meeting's tags from one query, for the list: two hundred rows are not
        two hundred queries."""
        out: dict[str, list[str]] = {}
        for row in self.conn.execute(
            "SELECT meeting_id, tag FROM meeting_tags ORDER BY rowid"
        ).fetchall():
            out.setdefault(row["meeting_id"], []).append(row["tag"])
        return out

    def tag_counts(self) -> list[tuple[str, int]]:
        """Each tag in use and how many meetings carry it: most used first, then by name."""
        counts: dict[str, int] = {}
        spelling: dict[str, str] = {}
        for row in self.conn.execute(
            "SELECT tag FROM meeting_tags ORDER BY created_at, rowid"
        ).fetchall():
            key = row["tag"].casefold()
            spelling.setdefault(key, row["tag"])
            counts[key] = counts.get(key, 0) + 1
        ordered = sorted(counts, key=lambda key: (-counts[key], spelling[key].casefold()))
        return [(spelling[key], counts[key]) for key in ordered]

    def set_tags(self, meeting_id: str, tags: Sequence[str]) -> list[str]:
        """Replace this meeting's tags; returns them as stored.

        Normalised (stripped, whitespace squeezed) and deduplicated case-insensitively.
        The spelling kept is the first one ever used anywhere in the library — so typing
        "roadmap" on one meeting and "Roadmap" on the next is one tag, not two, and the
        tag list does not fill with case variants.
        """
        wanted: list[str] = []
        seen: set[str] = set()
        for raw in tags:
            tag = normalize_tag(raw)
            if not tag or tag.casefold() in seen:
                continue
            if len(tag) > MAX_TAG_CHARS:
                raise ValueError(f"a tag is at most {MAX_TAG_CHARS} characters")
            seen.add(tag.casefold())
            wanted.append(tag)
        if len(wanted) > MAX_TAGS:
            raise ValueError(f"a meeting has at most {MAX_TAGS} tags")
        # Spellings on *other* meetings only: on its own meeting a tag can be re-cased,
        # which is how someone fixes "roadmap" to "Roadmap" in the first place.
        existing: dict[str, str] = {}
        for row in self.conn.execute(
            "SELECT tag FROM meeting_tags WHERE meeting_id != ? ORDER BY created_at, rowid",
            (meeting_id,),
        ).fetchall():
            existing.setdefault(row["tag"].casefold(), row["tag"])
        mine = {
            row["tag"].casefold(): row["created_at"]
            for row in self.conn.execute(
                "SELECT tag, created_at FROM meeting_tags WHERE meeting_id = ?", (meeting_id,)
            ).fetchall()
        }
        now = iso(self.clock.now())
        self.conn.execute("DELETE FROM meeting_tags WHERE meeting_id = ?", (meeting_id,))
        for tag in wanted:
            self.conn.execute(
                "INSERT INTO meeting_tags(meeting_id, tag, created_at) VALUES (?,?,?)",
                (meeting_id, existing.get(tag.casefold(), tag), mine.get(tag.casefold(), now)),
            )
        return self.tags(meeting_id)

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
