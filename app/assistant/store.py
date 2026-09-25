"""The assistant's conversations, kept in the app's own database (plan step 4)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from app.clock import Clock, iso

TITLE_CHARS = 60


@dataclass(frozen=True)
class Session:
    id: str
    title: str
    provider: str
    cli_session_id: str
    created_at: str
    updated_at: str

    def as_api(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "provider": self.provider,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _session(row: sqlite3.Row) -> Session:
    return Session(
        id=row["id"],
        title=row["title"],
        provider=row["provider"],
        cli_session_id=row["cli_session_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def title_from(question: str) -> str:
    """The first question, cut at a word: the title until the user gives it another."""
    text = " ".join(question.split())
    if len(text) <= TITLE_CHARS:
        return text
    cut = text[:TITLE_CHARS].rsplit(" ", 1)[0]
    return (cut or text[:TITLE_CHARS]) + "…"


class SessionStore:
    def __init__(self, conn: sqlite3.Connection, clock: Clock) -> None:
        self.conn = conn
        self.clock = clock

    def _now(self) -> str:
        return iso(self.clock.now())

    def get(self, session_id: str) -> Session | None:
        row = self.conn.execute(
            "SELECT * FROM assistant_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return _session(row) if row else None

    def ensure(self, session_id: str, *, title: str, provider: str) -> Session:
        existing = self.get(session_id)
        if existing is not None:
            return existing
        now = self._now()
        self.conn.execute(
            "INSERT INTO assistant_sessions(id, title, provider, created_at, updated_at) "
            "VALUES (?,?,?,?,?)",
            (session_id, title_from(title), provider, now, now),
        )
        return self.get(session_id)  # type: ignore[return-value]

    def recent(self, limit: int = 200) -> list[Session]:
        rows = self.conn.execute(
            "SELECT * FROM assistant_sessions ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_session(row) for row in rows]

    def rename(self, session_id: str, title: str) -> bool:
        title = " ".join(title.split())[:200]
        return bool(
            self.conn.execute(
                "UPDATE assistant_sessions SET title = ? WHERE id = ?", (title, session_id)
            ).rowcount
        )

    def delete(self, session_id: str) -> bool:
        return bool(
            self.conn.execute("DELETE FROM assistant_sessions WHERE id = ?", (session_id,)).rowcount
        )

    def set_cli_session(self, session_id: str, cli_session_id: str, provider: str) -> None:
        self.conn.execute(
            "UPDATE assistant_sessions SET cli_session_id = ?, provider = ?, updated_at = ? "
            "WHERE id = ?",
            (cli_session_id, provider, self._now(), session_id),
        )

    def append(self, session_id: str, message: dict[str, Any]) -> None:
        """Add a message in UIMessage shape; one already stored under its id is replaced."""
        seq_row = self.conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM assistant_messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        now = self._now()
        self.conn.execute(
            "INSERT INTO assistant_messages(id, session_id, seq, role, parts_json, created_at) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(session_id, id) DO UPDATE "
            "SET parts_json = excluded.parts_json",
            (
                str(message["id"]),
                session_id,
                int(seq_row[0]),
                str(message["role"]),
                json.dumps(message.get("parts") or [], ensure_ascii=False),
                now,
            ),
        )
        self.conn.execute(
            "UPDATE assistant_sessions SET updated_at = ? WHERE id = ?", (now, session_id)
        )

    def messages(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id, role, parts_json FROM assistant_messages WHERE session_id = ? ORDER BY seq",
            (session_id,),
        ).fetchall()
        return [
            {"id": row["id"], "role": row["role"], "parts": json.loads(row["parts_json"])}
            for row in rows
        ]

    def recap(self, session_id: str, *, max_chars: int = 4000) -> str:
        """The conversation so far as plain text, newest last, for a CLI that lost it."""
        lines: list[str] = []
        for message in self.messages(session_id):
            text = " ".join(
                str(part.get("text", ""))
                for part in message["parts"]
                if isinstance(part, dict) and part.get("type") == "text"
            ).strip()
            if text:
                who = "User" if message["role"] == "user" else "Assistant"
                lines.append(f"{who}: {' '.join(text.split())}")
        recap = "\n".join(lines)
        return recap[-max_chars:]
