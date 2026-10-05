"""File transcription jobs: the ``transcriptions`` table and their folders (D86).

A file job is one unit of work, so one row: it is claimed, runs its phases and ends —
``done``, ``failed`` or ``cancelled`` — and the row stays until retention or a delete.
Its folder is ``<data root>/transcriptions/<id>/``: ``input/`` for an uploaded copy,
``audio.wav`` while it runs, ``result.json`` once done. A ``path`` source is read where it
is and never copied, moved or deleted.

Claims, backoff and crash recovery work as ``JobQueue``'s do. Cancel and delete of a
running job are requests the worker picks up between segments (``stop_check``): the
worker, not the API thread, removes a running job's folder, once the engine has let go of
``audio.wav`` (Windows file locks; ``Worker.finish_delete`` does the same for meetings).
"""

from __future__ import annotations

import base64
import json
import random
import secrets
import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.clock import Clock, SystemClock, iso, parse_iso
from app.errors import Cancelled
from app.log import get
from app.pipeline.queue import MAX_ATTEMPTS, backoff_seconds
from app.transcription.types import Options

log = get(__name__)

PENDING, RUNNING, DONE, FAILED, CANCELLED = "pending", "running", "done", "failed", "cancelled"
STATES = (PENDING, RUNNING, DONE, FAILED, CANCELLED)
FINISHED = frozenset({DONE, FAILED, CANCELLED})

FOLDER_NAME = "transcriptions"
#: Uploads in progress; nothing in here has a row yet (§4.4).
INCOMING = ".incoming"
INPUT_DIR = "input"
RESULT_NAME = "result.json"

#: At most one progress event a second per job: the page updates live without the event
#: stream turning into a flood during ASR.
EVENT_INTERVAL_S = 1.0

COLUMNS = (
    "id", "state", "source_name", "source_kind", "source_path", "size_bytes", "duration_s",
    "options", "client", "language", "language_conf", "model", "phase", "progress",
    "attempts", "not_before", "last_error", "queued_at", "created_at", "started_at",
    "finished_at", "updated_at",
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class Transcription:
    id: str
    state: str
    source_name: str
    source_kind: str
    source_path: str
    size_bytes: int | None
    duration_s: float | None
    options: str
    client: str
    language: str | None
    language_conf: float | None
    model: str | None
    phase: str | None
    progress: float
    attempts: int
    not_before: str | None
    last_error: str | None
    queued_at: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    updated_at: str

    @property
    def parsed_options(self) -> Options:
        return Options.from_dict(json.loads(self.options or "{}"))

    @property
    def fifo_key(self) -> str:
        return self.queued_at

    @property
    def finished(self) -> bool:
        return self.state in FINISHED

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state,
            "source_name": self.source_name,
            "source_kind": self.source_kind,
            "size_bytes": self.size_bytes,
            "duration_s": self.duration_s,
            "options": json.loads(self.options or "{}"),
            "client": self.client,
            "language": self.language,
            "language_conf": self.language_conf,
            "model": json.loads(self.model) if self.model else None,
            "phase": self.phase,
            "progress": self.progress,
            "attempts": self.attempts,
            "error": self.last_error,
            "queued_at": self.queued_at,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


def _row(row: sqlite3.Row) -> Transcription:
    return Transcription(**{key: row[key] for key in COLUMNS})


def new_id() -> str:
    """``tr_`` and 12 base32 characters: opaque, and never a meeting's id."""
    return "tr_" + base64.b32encode(secrets.token_bytes(8)).decode("ascii")[:12].lower()


class TranscriptionStore:
    def __init__(
        self,
        conn: sqlite3.Connection,
        data_root: Path | Callable[[], Path],
        clock: Clock | None = None,
        rng: random.Random | None = None,
        *,
        events: Any = None,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> None:
        self.conn = conn
        # A callable, so a data root moved in Settings is followed without a restart.
        self._data_root = data_root
        self.clock = clock or SystemClock()
        self.rng = rng or random.Random()
        self.events = events
        self.max_attempts = max_attempts
        #: Running jobs asked to stop, and how: "cancel" or "delete". In memory, like
        #: ``JobQueue._deleting``: a request outlives nothing but the run it was made in.
        self._stopping: dict[str, str] = {}
        self._last_event: dict[str, float] = {}

    # -- folders -----------------------------------------------------------

    @property
    def root(self) -> Path:
        base = self._data_root() if callable(self._data_root) else self._data_root
        return Path(base) / FOLDER_NAME

    def folder(self, transcription_id: str) -> Path:
        return self.root / transcription_id

    def incoming(self) -> Path:
        return self.root / INCOMING

    def inside_root(self, folder: Path) -> bool:
        """The id comes from a caller, so a removal must never aim anywhere else."""
        root = self.root.resolve()
        target = Path(folder).resolve()
        return root in target.parents

    def remove_folder(self, transcription_id: str) -> None:
        folder = self.folder(transcription_id)
        if not folder.exists():
            return
        if not self.inside_root(folder):
            raise ValueError(f"{folder} is outside the transcriptions folder")
        shutil.rmtree(folder)

    def clean_incoming(self) -> int:
        """Partial uploads from a run that ended mid-upload: none of them has a row."""
        directory = self.incoming()
        if not directory.exists():
            return 0
        removed = 0
        for path in directory.iterdir():
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                removed += 1
            except OSError as exc:  # pragma: no cover - a file still held (Windows)
                log.warning("could not remove the partial upload %s: %s", path.name, exc)
        return removed

    # -- writing -----------------------------------------------------------

    def create(
        self,
        *,
        source_name: str,
        source_kind: str,
        source_path: str,
        options: Options,
        client: str,
        size_bytes: int | None = None,
        duration_s: float | None = None,
        transcription_id: str | None = None,
    ) -> Transcription:
        now = iso(self.clock.now())
        tid = transcription_id or new_id()
        self.conn.execute(
            "INSERT INTO transcriptions(id, state, source_name, source_kind, source_path, "
            "size_bytes, duration_s, options, client, progress, attempts, queued_at, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,0,0,?,?,?)",
            (tid, PENDING, source_name, source_kind, source_path, size_bytes, duration_s,
             json.dumps(options.as_dict(), ensure_ascii=False), client, now, now, now),
        )  # fmt: skip
        created = self.require(tid)
        self._publish(created)
        return created

    def claim(self, transcription_id: str) -> Transcription | None:
        """Take this job if it is still pending. ``None`` when an API thread cancelled or
        deleted it between the scheduler's peek and here."""
        now = iso(self.clock.now())
        row = self.conn.execute(
            "UPDATE transcriptions SET state='running', started_at=?, updated_at=?, "
            "phase=NULL, progress=0 WHERE id=? AND state='pending' RETURNING *",
            (now, now, transcription_id),
        ).fetchone()
        if row is None:
            return None
        claimed = _row(row)
        self._publish(claimed)
        return claimed

    def progress(self, transcription_id: str, phase: str, progress: float) -> None:
        self.conn.execute(
            "UPDATE transcriptions SET phase=?, progress=?, updated_at=? WHERE id=?",
            (phase, round(progress, 4), iso(self.clock.now()), transcription_id),
        )
        now = self.clock.monotonic()
        last = self._last_event.get(transcription_id)
        if last is None or now - last >= EVENT_INTERVAL_S:
            self._last_event[transcription_id] = now
            self._emit(transcription_id, RUNNING, phase, progress)

    def complete(
        self,
        transcription_id: str,
        *,
        duration_s: float,
        language: str | None,
        language_conf: float | None,
        model: dict[str, Any],
    ) -> Transcription:
        now = iso(self.clock.now())
        self.conn.execute(
            "UPDATE transcriptions SET state='done', phase=NULL, progress=1, last_error=NULL, "
            "duration_s=?, language=?, language_conf=?, model=?, finished_at=?, updated_at=? "
            "WHERE id=?",
            (duration_s, language, language_conf, json.dumps(model, ensure_ascii=False),
             now, now, transcription_id),
        )  # fmt: skip
        return self._finished(transcription_id)

    def fail(
        self, job: Transcription, error: BaseException | str, *, permanent: bool = False
    ) -> Transcription:
        """Back off and try again, as ``JobQueue.fail`` does, or fail for good."""
        now = self.clock.now()
        attempts = job.attempts + 1
        message = f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else error
        if permanent or attempts >= self.max_attempts:
            self.conn.execute(
                "UPDATE transcriptions SET state='failed', attempts=?, last_error=?, "
                "phase=NULL, not_before=NULL, finished_at=?, updated_at=? WHERE id=?",
                (attempts, message[:2000], iso(now), iso(now), job.id),
            )
            return self._finished(job.id)
        delay = backoff_seconds(attempts, self.rng)
        self.conn.execute(
            "UPDATE transcriptions SET state='pending', attempts=?, last_error=?, phase=NULL, "
            "progress=0, not_before=?, started_at=NULL, updated_at=? WHERE id=?",
            (attempts, message[:2000], iso(now + timedelta(seconds=delay)), iso(now), job.id),
        )
        updated = self.require(job.id)
        self._publish(updated)
        return updated

    def release(self, job: Transcription) -> Transcription:
        """Preempted by a recording: back to pending, **no** attempt counted, same place
        in the queue. It starts again from the beginning."""
        self.conn.execute(
            "UPDATE transcriptions SET state='pending', phase=NULL, progress=0, "
            "started_at=NULL, updated_at=? WHERE id=?",
            (iso(self.clock.now()), job.id),
        )
        updated = self.require(job.id)
        self._publish(updated)
        return updated

    def cancel(self, transcription_id: str) -> Transcription:
        """A waiting job is cancelled now; a running one when the worker next checks.
        The row stays, ``cancelled``. A finished job is left as it is."""
        job = self.require(transcription_id)
        if job.state == PENDING:
            return self.mark_cancelled(transcription_id)
        if job.state == RUNNING:
            self._stopping.setdefault(transcription_id, "cancel")
        return job

    def mark_cancelled(self, transcription_id: str) -> Transcription:
        now = iso(self.clock.now())
        self.conn.execute(
            "UPDATE transcriptions SET state='cancelled', phase=NULL, not_before=NULL, "
            "finished_at=?, updated_at=? WHERE id=?",
            (now, now, transcription_id),
        )
        self._stopping.pop(transcription_id, None)
        return self._finished(transcription_id)

    def retry(self, transcription_id: str) -> Transcription:
        """A failed or cancelled job runs again, at the back of the queue."""
        job = self.require(transcription_id)
        if job.state not in (FAILED, CANCELLED):
            raise ValueError(
                f"{transcription_id} is {job.state}; only a failed or cancelled job is retried"
            )
        now = iso(self.clock.now())
        self.conn.execute(
            "UPDATE transcriptions SET state='pending', attempts=0, last_error=NULL, "
            "not_before=NULL, phase=NULL, progress=0, started_at=NULL, finished_at=NULL, "
            "queued_at=?, updated_at=? WHERE id=?",
            (now, now, transcription_id),
        )
        updated = self.require(transcription_id)
        self._publish(updated)
        return updated

    def delete(self, transcription_id: str) -> bool:
        """Remove the row and the folder. A running job is a hand-off: it stops at its next
        check and the worker removes it (``finish_delete``). True when it is gone now."""
        job = self.require(transcription_id)
        if job.state == RUNNING:
            self._stopping[transcription_id] = "delete"
            return False
        self.purge(transcription_id)
        return True

    def purge(self, transcription_id: str) -> None:
        """Folder first, then the row: a row deleted against a folder that survived would
        orphan the folder with nothing pointing at it."""
        self.remove_folder(transcription_id)
        self.conn.execute("DELETE FROM transcriptions WHERE id=?", (transcription_id,))
        self._stopping.pop(transcription_id, None)
        self._last_event.pop(transcription_id, None)
        if self.events is not None:
            self.events.publish("transcription", id=transcription_id, state="deleted")

    def finish_delete(self, transcription_id: str) -> bool:
        """The delete asked for while it ran, now that the engine has let go of its files."""
        if self._stopping.get(transcription_id) != "delete":
            return False
        try:
            self.purge(transcription_id)
        except OSError as exc:  # a file still held (Windows): the sweep retries later
            log.warning("transcription %s: could not finish deleting it: %s", transcription_id, exc)
            return False
        return True

    def stopping(self, transcription_id: str) -> str | None:
        return self._stopping.get(transcription_id)

    def stop_check(self, transcription_id: str) -> None:
        """The engine's ``stop_check``: raises :class:`Cancelled` once a cancel or a delete
        has been asked for."""
        how = self._stopping.get(transcription_id)
        if how is not None:
            raise Cancelled(f"transcription {transcription_id}: {how} requested")

    def reset_running(self) -> int:
        """Crash recovery: a job left running by a dead process is runnable again."""
        cur = self.conn.execute(
            "UPDATE transcriptions SET state='pending', phase=NULL, progress=0, "
            "started_at=NULL, updated_at=? WHERE state='running'",
            (iso(self.clock.now()),),
        )
        count = int(cur.rowcount or 0)
        if count:
            log.info("crash recovery: reset %d running transcription(s) to pending", count)
        self.clean_incoming()
        return count

    # -- reading -----------------------------------------------------------

    def get(self, transcription_id: str) -> Transcription | None:
        row = self.conn.execute(
            "SELECT * FROM transcriptions WHERE id=?", (transcription_id,)
        ).fetchone()
        return _row(row) if row else None

    def require(self, transcription_id: str) -> Transcription:
        job = self.get(transcription_id)
        if job is None:
            raise KeyError(f"no such transcription: {transcription_id}")
        return job

    def recent(
        self, *, state: str | None = None, limit: int = 50, before: str | None = None
    ) -> list[Transcription]:
        """Newest first. ``before`` is a ``created_at`` to page from."""
        clauses, params = [], []
        if state is not None:
            clauses.append("state = ?")
            params.append(state)
        if before is not None:
            clauses.append("created_at < ?")
            params.append(before)
        where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM transcriptions {where}ORDER BY created_at DESC, id DESC LIMIT ?",
            (*params, max(1, min(int(limit), 500))),
        ).fetchall()
        return [_row(row) for row in rows]

    def runnable(self) -> list[Transcription]:
        """Pending and due, oldest key first. Compared as parsed datetimes, never strings:
        the UTC offset in every timestamp changes at DST."""
        now = self.clock.now()
        rows = self.conn.execute("SELECT * FROM transcriptions WHERE state='pending'").fetchall()
        due = [
            job
            for job in map(_row, rows)
            if job.not_before is None or parse_iso(job.not_before) <= now
        ]
        return sorted(due, key=lambda job: (parse_iso(job.queued_at), job.id))

    def peek(self) -> Transcription | None:
        due = self.runnable()
        return due[0] if due else None

    def pending(self) -> list[Transcription]:
        """Every waiting job, due or backing off, oldest key first."""
        rows = self.conn.execute("SELECT * FROM transcriptions WHERE state='pending'").fetchall()
        return sorted(map(_row, rows), key=lambda job: (parse_iso(job.queued_at), job.id))

    def depth(self) -> int:
        row = self.conn.execute(
            "SELECT count(*) AS n FROM transcriptions WHERE state IN ('pending','running')"
        ).fetchone()
        return int(row["n"])

    def running(self) -> Transcription | None:
        row = self.conn.execute(
            "SELECT * FROM transcriptions WHERE state='running' LIMIT 1"
        ).fetchone()
        return _row(row) if row else None

    def expired(self, keep_days: int, now: datetime) -> list[Transcription]:
        """Finished before ``keep_days`` ago: what retention removes."""
        cutoff = now - timedelta(days=keep_days)
        rows = self.conn.execute(
            "SELECT * FROM transcriptions WHERE state IN ('done','failed','cancelled')"
        ).fetchall()
        return [
            job for job in map(_row, rows) if parse_iso(job.finished_at or job.updated_at) < cutoff
        ]

    # -- events ------------------------------------------------------------

    def _finished(self, transcription_id: str) -> Transcription:
        job = self.require(transcription_id)
        self._last_event.pop(transcription_id, None)
        self._publish(job)
        return job

    def _publish(self, job: Transcription) -> None:
        self._emit(job.id, job.state, job.phase, job.progress)

    def _emit(self, transcription_id: str, state: str, phase: str | None, progress: float) -> None:
        if self.events is not None:
            self.events.publish(
                "transcription",
                id=transcription_id,
                state=state,
                phase=phase,
                progress=round(progress, 4),
            )
