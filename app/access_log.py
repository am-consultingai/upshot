"""The activity log: every request from the user's own AI tools and programs (D98).

Written by ``/mcp/ext/`` for each tool call and by ``/api/v1`` for each request a program
makes (not the page's own, not ``info``, not a long-poll's repeats). The AI apps page
shows it. What a caller calls itself (``agent``) is its own word, never proof.

Nothing is deleted by age unless the user asks (D92): ``retention.access_log_days``, null
by default. The log is capped at :data:`MAX_ROWS` instead, oldest first, so a program
calling in a loop cannot grow it without bound; Clear empties it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any

from app.clock import Clock
from app.log import get

log = get(__name__)

MAX_ROWS = 10_000
CHANNELS = frozenset({"mcp", "api"})
OUTCOMES = frozenset({"ok", "refused", "error"})
AGENT_CHARS = 64
TARGET_CHARS = 200


@dataclass(frozen=True)
class Entry:
    id: int
    at: str
    channel: str
    agent: str
    action: str
    target: str
    outcome: str
    size: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def clean(text: Any, limit: int) -> str:
    """One line of printable text, cut to ``limit``."""
    flat = " ".join(str(text or "").split())
    return "".join(c for c in flat if c.isprintable())[:limit]


def agent_of(headers: Any) -> str:
    """What the caller calls itself: the bridge's ``X-Upshot-Agent`` (the AI app's own
    name), else ``User-Agent``, else nothing."""
    if not headers:
        return ""
    for name in ("x-upshot-agent", "user-agent"):
        value = headers.get(name)
        if value:
            return clean(value, AGENT_CHARS)
    return ""


class AccessLog:
    def __init__(self, conn: Any, clock: Clock) -> None:
        self.conn = conn
        self.clock = clock

    def record(
        self,
        channel: str,
        agent: str,
        action: str,
        target: str = "",
        outcome: str = "ok",
        size: int = 0,
    ) -> None:
        """Never raises: a request is not refused because its record could not be written."""
        try:
            self.conn.execute(
                "INSERT INTO access_log (at, channel, agent, action, target, outcome, size) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    self.clock.now().isoformat(),
                    channel if channel in CHANNELS else "api",
                    clean(agent, AGENT_CHARS),
                    clean(action, 64),
                    clean(target, TARGET_CHARS),
                    outcome if outcome in OUTCOMES else "error",
                    max(0, int(size)),
                ),
            )
            self.conn.execute(
                "DELETE FROM access_log WHERE id <= "
                "(SELECT id FROM access_log ORDER BY id DESC LIMIT 1 OFFSET ?)",
                (MAX_ROWS,),
            )
        except Exception:  # pragma: no cover - a full disk, a closed database
            log.exception("could not write the activity log")

    def recent(self, limit: int = 200, agent: str = "") -> list[Entry]:
        limit = max(1, min(int(limit), 1000))
        if agent:
            rows = self.conn.execute(
                "SELECT * FROM access_log WHERE agent = ? ORDER BY id DESC LIMIT ?",
                (agent, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM access_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [Entry(**{key: row[key] for key in Entry.__dataclass_fields__}) for row in rows]

    def agents(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT agent, MAX(id) AS last FROM access_log GROUP BY agent ORDER BY last DESC"
        ).fetchall()
        return [str(row["agent"]) for row in rows]

    def clear(self) -> int:
        return int(self.conn.execute("DELETE FROM access_log").rowcount)

    def sweep(self, days: int | None) -> int:
        """Entries older than ``days``; none when ``days`` is unset (D92)."""
        if not days or days <= 0:
            return 0
        cutoff = (self.clock.now() - timedelta(days=days)).isoformat()
        return int(self.conn.execute("DELETE FROM access_log WHERE at < ?", (cutoff,)).rowcount)
