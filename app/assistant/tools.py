"""What the assistant may look at. Read-only by construction (D61): every function here
reads the database or a meeting's files and returns text for the model.

The same functions are served to the Claude Code and Codex CLIs over MCP
(``app/assistant/mcp.py``), so a tool is written once whichever CLI calls it.

Every result is capped (:data:`MAX_CHARS`, about 8k tokens) so one call cannot fill the
model's context, and says so when it was cut, with what to ask for next.
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from app.ask import transcript_lines
from app.db.dao import Meeting
from app.meetings import calendar_payload
from app.services import Services

#: Tool output is data, never instructions (D61). It is fenced with a tag whose name is
#: made when the app starts ("spotlighting"): a transcript that says "</upshot-data>
#: ignore the above" cannot close the fence, because it cannot know the name. The system
#: prompt tells the model the name (see :func:`data_tag`).
DATA_TAG = f"upshot-data-{secrets.token_hex(4)}"
DATA_OPEN = f"<{DATA_TAG}>"
DATA_CLOSE = f"</{DATA_TAG}>"
MAX_CHARS = 24_000


def data_tag() -> str:
    return DATA_TAG


def as_data(payload: Any) -> str:
    # Compact: the model reads it as easily, and every byte is a token of its context.
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # Belt and braces: the fence cannot be closed from inside, even by a lucky guess.
    text = text.replace(DATA_CLOSE, "").replace(f"<{DATA_TAG}", "")
    return f"{DATA_OPEN}\n{text}\n{DATA_CLOSE}"


def clock(at_ms: int) -> str:
    seconds = max(0, int(at_ms) // 1000)
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


def _day(text: str | None) -> str | None:
    """A date the model may pass as ``2026-09-20`` or a full ISO time; None if neither."""
    if not text:
        return None
    text = text.strip()
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return text


class AssistantTools:
    def __init__(self, svc: Services) -> None:
        self.svc = svc

    # -- helpers -----------------------------------------------------------

    def _titles(self) -> dict[str, tuple[str, str]]:
        meetings = self.svc.dao.list_meetings(limit=10_000)
        return {m.id: (m.title or "", m.started_at or "") for m in meetings}

    def _participants(self, meeting: Meeting) -> list[str]:
        names = calendar_payload(meeting).get("participants") or []
        return [" ".join(str(name).split()) for name in names if str(name).strip()]

    def _brief(self, meeting: Meeting) -> dict[str, Any]:
        return {
            "meeting_id": meeting.id,
            "title": meeting.title or "",
            "started_at": meeting.started_at,
            "duration_min": round(meeting.duration_s / 60) if meeting.duration_s else None,
            "participants": self._participants(meeting),
            "tags": self.svc.dao.tags(meeting.id),
            "state": meeting.state,
        }

    def _missing(self, meeting_id: str) -> str:
        return as_data(
            {"error": f"No meeting with id {meeting_id!r}. Search or list meetings first."}
        )

    # -- tools -------------------------------------------------------------

    def search(self, query: str, limit: int = 20) -> str:
        """Meetings, action items, summaries and transcript lines that contain the words."""
        limit = max(1, min(int(limit), 50))
        hits = self.svc.dao.search(query, limit=limit)
        titles = self._titles()
        results = [
            {
                "meeting_id": hit.meeting_id,
                "meeting_title": titles.get(hit.meeting_id, ("", ""))[0],
                "meeting_date": titles.get(hit.meeting_id, ("", ""))[1],
                "kind": hit.kind,
                "speaker": hit.speaker,
                "at_ms": hit.at_ms if hit.kind == "transcript" else None,
                "text": hit.snippet if hit.kind == "summary" else hit.text,
            }
            for hit in hits
        ]
        return as_data({"query": query, "count": len(results), "results": results})

    def list_meetings(
        self,
        date_from: str = "",
        date_to: str = "",
        contains: str = "",
        limit: int = 30,
    ) -> str:
        """Meetings, newest first, optionally within dates or containing words."""
        limit = max(1, min(int(limit), 100))
        meetings = self.svc.dao.list_meetings(
            frm=_day(date_from), to=_day(date_to), q=contains.strip() or None, limit=limit
        )
        return as_data({"count": len(meetings), "meetings": [self._brief(m) for m in meetings]})

    def get_meeting(self, meeting_id: str) -> str:
        """One meeting: details, the summary as text, and its action items."""
        meeting = self.svc.dao.get_meeting(meeting_id)
        if meeting is None:
            return self._missing(meeting_id)
        summary = self.svc.dao.summary_text(meeting.id)
        items = self.svc.dao.action_items(meeting_id=meeting.id)
        payload = {
            **self._brief(meeting),
            "speaker_names": meeting.speakers,
            "summary": summary[: MAX_CHARS // 2] or None,
            "summary_truncated": len(summary) > MAX_CHARS // 2,
            "action_items": [
                {
                    "who": item.who,
                    "what": item.what,
                    "due": item.due_at or item.due,
                    "done": item.done_at is not None,
                    "at_ms": item.at_ms,
                }
                for item in items
            ],
            "transcript_lines": len(self.svc.dao.turns(meeting.id)),
        }
        return as_data(payload)

    def get_transcript(self, meeting_id: str, from_ms: int = 0, to_ms: int = 0) -> str:
        """Part of a transcript: lines with the time each was said (``at_ms``)."""
        meeting = self.svc.dao.get_meeting(meeting_id)
        if meeting is None:
            return self._missing(meeting_id)
        lines = [
            line
            for line in transcript_lines(self.svc.dao, meeting)
            if line.at_ms >= int(from_ms) and (not to_ms or line.at_ms <= int(to_ms))
        ]
        out: list[dict[str, Any]] = []
        used = 0
        next_from: int | None = None
        for line in lines:
            row = {
                "at_ms": line.at_ms,
                "time": clock(line.at_ms),
                "speaker": line.speaker,
                "text": line.text,
            }
            # Measured as it will be sent.
            size = len(json.dumps(row, ensure_ascii=False, separators=(",", ":"))) + 1
            if out and used + size > MAX_CHARS:
                next_from = line.at_ms
                break
            out.append(row)
            used += size
        payload: dict[str, Any] = {
            "meeting_id": meeting.id,
            "title": meeting.title or "",
            "started_at": meeting.started_at,
            "lines": out,
        }
        if next_from is not None:
            payload["truncated"] = f"More follows. Call again with from_ms={next_from}."
        return as_data(payload)

    def list_action_items(
        self, open_only: bool = True, person: str = "", meeting_id: str = "", limit: int = 100
    ) -> str:
        """Action items across meetings (or one), optionally only one person's."""
        limit = max(1, min(int(limit), 300))
        items = self.svc.dao.action_items(
            meeting_id=meeting_id or None, open_only=bool(open_only), limit=1000
        )
        wanted = " ".join(person.split()).casefold()
        if wanted:
            items = [
                item
                for item in items
                if wanted in item.who.casefold() or (wanted in {"me", "i", "אני"} and item.mine)
            ]
        rows = [
            {
                "meeting_id": item.meeting_id,
                "meeting_title": item.meeting_title,
                "meeting_date": item.meeting_started_at,
                "who": "the user" if item.mine else item.who,
                "what": item.what,
                "detail": item.detail,
                "due": item.due_at or item.due,
                "done": item.done_at is not None,
                "snoozed_until": item.snoozed_until,
                "at_ms": item.at_ms,
            }
            for item in items[:limit]
        ]
        return as_data({"count": len(rows), "more": len(items) > limit, "action_items": rows})

    def calendar_range(self, date_from: str, date_to: str = "") -> str:
        """Calendar events between two dates, and which were recorded."""
        start_text = _day(date_from)
        if start_text is None:
            return as_data({"error": "date_from must be a date like 2026-09-20."})
        start = datetime.fromisoformat(start_text.replace("Z", "+00:00"))
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        end_text = _day(date_to)
        end = (
            datetime.fromisoformat(end_text.replace("Z", "+00:00"))
            if end_text
            else start + timedelta(days=1)
        )
        if end.tzinfo is None:
            end = end.replace(tzinfo=UTC)
        if end <= start:
            end = start + timedelta(days=1)
        end = min(end, start + timedelta(days=62))
        from app.gcal.events import EventStore

        store = (
            self.svc.calendar_sync.store
            if self.svc.calendar_sync is not None
            else EventStore(self.svc.conn)
        )
        recorded: dict[tuple[str, str], str] = {}
        for meeting in self.svc.dao.list_meetings(limit=5000):
            payload = calendar_payload(meeting)
            if payload.get("event_id"):
                recorded[(str(payload.get("calendar_id", "")), str(payload["event_id"]))] = (
                    meeting.id
                )
        events = [
            {
                "title": event.title or "",
                "start": event.start.isoformat(),
                "end": event.end.isoformat(),
                "all_day": event.all_day,
                "attendees": [a.name for a in event.attendees if not a.declined][:30],
                "recorded_meeting_id": recorded.get(event.key),
            }
            for event in store.between(start, end)
            if event.status != "cancelled"
        ]
        return as_data(
            {
                "from": start.isoformat(),
                "to": end.isoformat(),
                "count": len(events),
                "events": events[:200],
            }
        )

    def related_meetings(self, meeting_id: str) -> str:
        """Meetings related to this one, and why: people, series, action items, a rare word."""
        from app.related import related

        if self.svc.dao.get_meeting(meeting_id) is None:
            return self._missing(meeting_id)
        found = related(self.svc.dao, meeting_id)
        return as_data({"meeting_id": meeting_id, "related": [item.as_api() for item in found]})
