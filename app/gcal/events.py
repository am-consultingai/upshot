"""Calendar events: what is kept of a Google event, and the cache that keeps it.

Kept: ids, title, times, this user's answer, attendee *display names*, and the meeting
link. Not kept: the description (dial-in PINs, passcodes, forwarded mail) and attendee
email addresses (Calendar 7, z8tj1h8jrp). An attendee Google gives no name for is shown
by a name made from the local part of the address — "jane.doe@x.com" becomes "Jane Doe"
— and the address itself is dropped here, at the boundary, before anything is stored.
"""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

#: Meeting links that arrive as plain text in the location, which is where Zoom, Teams
#: and Webex put them. Google Meet has conferenceData and needs none of this.
MEETING_URL = re.compile(
    r"https://(?:[\w-]+\.)?(?:zoom\.us|zoomgov\.com|teams\.microsoft\.com|teams\.live\.com"
    r"|meet\.google\.com|[\w-]+\.webex\.com)/[^\s<>\"')]+",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Attendee:
    name: str
    optional: bool = False
    self: bool = False
    declined: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "optional": self.optional,
            "self": self.self,
            "declined": self.declined,
        }


@dataclass(frozen=True)
class CalendarEvent:
    calendar_id: str
    event_id: str
    title: str | None
    start: datetime  # UTC
    end: datetime  # UTC
    all_day: bool = False
    ical_uid: str | None = None
    recurring_event_id: str | None = None
    original_start: str | None = None
    time_zone: str | None = None
    status: str | None = "confirmed"
    response: str | None = None
    transparent: bool = False
    event_type: str | None = "default"
    visibility: str | None = None
    attendees: tuple[Attendee, ...] = field(default_factory=tuple)
    attendee_count: int = 0
    attendees_omitted: bool = False
    conference_url: str | None = None
    updated: str | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.calendar_id, self.event_id)

    @property
    def occurrence(self) -> tuple[str, str]:
        """The identity of one meeting, the same on every calendar it appears on.

        ``iCalUID`` alone is shared by every instance of a series, so an occurrence of a
        recurring meeting is ``iCalUID`` plus its original start.
        """
        return (self.ical_uid or self.event_id, self.original_start or iso_utc(self.start))

    @property
    def declined(self) -> bool:
        return self.response == "declined"

    @property
    def others(self) -> list[Attendee]:
        """People other than this user who did not decline."""
        return [a for a in self.attendees if not a.self and not a.declined]

    @property
    def private(self) -> bool:
        return self.visibility in ("private", "confidential")

    def participants(self, limit: int = 15) -> tuple[list[str], int]:
        """Names for the summary, capped, and how many more there were."""
        names = [a.name for a in self.others]
        return names[:limit], max(0, len(names) - limit) + (1 if self.attendees_omitted else 0)

    def as_api(self) -> dict[str, Any]:
        """What the UI receives. Names only; the link; no ids beyond what it needs."""
        return {
            "calendar_id": self.calendar_id,
            "event_id": self.event_id,
            "title": self.title,
            "start": iso_utc(self.start),
            "end": iso_utc(self.end),
            "all_day": self.all_day,
            "response": self.response,
            "transparent": self.transparent,
            "attendees": [a.name for a in self.others],
            "attendees_partial": self.attendees_omitted,
            "conference_url": self.conference_url,
        }


def iso_utc(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


def name_from_email(email: str) -> str:
    """ "jane.doe@x.com" -> "Jane Doe". The address itself never leaves this function."""
    local = email.split("@", 1)[0]
    words = [w for w in re.split(r"[._\-+]+", local) if w and not w.isdigit()]
    return " ".join(w.capitalize() for w in words) or "Guest"


def _when(node: dict[str, Any]) -> tuple[datetime, bool]:
    if "dateTime" in node:
        return parse_utc(str(node["dateTime"])), False
    day = datetime.fromisoformat(str(node["date"])).replace(tzinfo=UTC)
    return day, True


def _conference_url(item: dict[str, Any]) -> str | None:
    for entry in (item.get("conferenceData") or {}).get("entryPoints") or []:
        if entry.get("entryPointType") == "video" and entry.get("uri"):
            return str(entry["uri"])
    if item.get("hangoutLink"):
        return str(item["hangoutLink"])
    # Read here and dropped: the description is never stored, but a Zoom or Teams link
    # often lives only there.
    for text in (item.get("location"), item.get("description")):
        if text:
            found = MEETING_URL.search(str(text))
            if found:
                return found.group(0).rstrip(".,;")
    return None


def parse(item: dict[str, Any], calendar_id: str) -> CalendarEvent | None:
    """One ``events.list`` item, reduced to what is kept. None for what cannot be placed."""
    if item.get("status") == "cancelled" or "start" not in item or "end" not in item:
        return None
    start, all_day = _when(item["start"])
    end, _ = _when(item["end"])
    attendees: list[Attendee] = []
    response: str | None = None
    people = 0
    for raw in item.get("attendees") or []:
        if raw.get("resource"):
            continue  # a meeting room is not an attendee
        people += 1
        is_self = bool(raw.get("self"))
        if is_self:
            response = raw.get("responseStatus")
        name = raw.get("displayName") or name_from_email(str(raw.get("email", "")))
        attendees.append(
            Attendee(
                name=str(name),
                optional=bool(raw.get("optional")),
                self=is_self,
                declined=raw.get("responseStatus") == "declined",
            )
        )
    original = item.get("originalStartTime") or {}
    return CalendarEvent(
        calendar_id=calendar_id,
        event_id=str(item["id"]),
        title=item.get("summary"),
        start=start,
        end=end,
        all_day=all_day,
        ical_uid=item.get("iCalUID"),
        recurring_event_id=item.get("recurringEventId"),
        original_start=original.get("dateTime") or original.get("date"),
        time_zone=item["start"].get("timeZone"),
        status=item.get("status"),
        response=response,
        transparent=item.get("transparency") == "transparent",
        event_type=item.get("eventType", "default"),
        visibility=item.get("visibility"),
        attendees=tuple(attendees),
        attendee_count=people,
        attendees_omitted=bool(item.get("attendeesOmitted")),
        conference_url=_conference_url(item),
        updated=item.get("updated"),
    )


# --------------------------------------------------------------------------- the cache

_COLUMNS = (
    "calendar_id, event_id, ical_uid, recurring_event_id, original_start, title, start_at, "
    "end_at, all_day, time_zone, status, response, transparent, event_type, visibility, "
    "attendees_json, attendee_count, attendees_omitted, conference_url, updated, synced_at"
)


def _row(row: sqlite3.Row) -> CalendarEvent:
    attendees = tuple(
        Attendee(
            name=str(a.get("name", "")),
            optional=bool(a.get("optional")),
            self=bool(a.get("self")),
            declined=bool(a.get("declined")),
        )
        for a in json.loads(row["attendees_json"] or "[]")
    )
    return CalendarEvent(
        calendar_id=row["calendar_id"],
        event_id=row["event_id"],
        title=row["title"],
        start=parse_utc(row["start_at"]),
        end=parse_utc(row["end_at"]),
        all_day=bool(row["all_day"]),
        ical_uid=row["ical_uid"],
        recurring_event_id=row["recurring_event_id"],
        original_start=row["original_start"],
        time_zone=row["time_zone"],
        status=row["status"],
        response=row["response"],
        transparent=bool(row["transparent"]),
        event_type=row["event_type"],
        visibility=row["visibility"],
        attendees=attendees,
        attendee_count=int(row["attendee_count"]),
        attendees_omitted=bool(row["attendees_omitted"]),
        conference_url=row["conference_url"],
        updated=row["updated"],
    )


class EventStore:
    """The ``calendar_events`` table. A cache: every row can be rebuilt by a sync."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    @contextlib.contextmanager
    def _transaction(self) -> Iterator[None]:
        # The connection is in autocommit mode (isolation_level=None), so ``with conn``
        # would not make a transaction. Its own lock keeps other threads' statements out
        # of the middle of this one.
        lock = getattr(self.conn, "up_lock", None) or threading.RLock()
        with lock:
            self.conn.execute("BEGIN")
            try:
                yield
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            self.conn.execute("COMMIT")

    def replace_window(
        self,
        calendar_id: str,
        start: datetime,
        end: datetime,
        events: Iterable[CalendarEvent],
        *,
        synced_at: datetime,
    ) -> int:
        """Make the window exactly what Google just returned for it.

        Google's ``timeMin``/``timeMax`` select events *overlapping* the window, so the
        same overlap test picks the rows to drop: an event deleted or declined-and-hidden
        since the last sync disappears here rather than lingering for ever.
        """
        rows = [
            (
                e.calendar_id,
                e.event_id,
                e.ical_uid,
                e.recurring_event_id,
                e.original_start,
                e.title,
                iso_utc(e.start),
                iso_utc(e.end),
                int(e.all_day),
                e.time_zone,
                e.status,
                e.response,
                int(e.transparent),
                e.event_type,
                e.visibility,
                json.dumps([a.as_dict() for a in e.attendees], ensure_ascii=False),
                e.attendee_count,
                int(e.attendees_omitted),
                e.conference_url,
                e.updated,
                iso_utc(synced_at),
            )
            for e in events
        ]
        with self._transaction():
            self.conn.execute(
                "DELETE FROM calendar_events WHERE calendar_id = ? AND start_at < ? AND end_at > ?",
                (calendar_id, iso_utc(end), iso_utc(start)),
            )
            self.conn.executemany(
                f"INSERT OR REPLACE INTO calendar_events ({_COLUMNS}) "
                f"VALUES ({', '.join('?' for _ in range(21))})",
                rows,
            )
        return len(rows)

    def between(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        """Every cached event overlapping [start, end), earliest first."""
        found = self.conn.execute(
            f"SELECT {_COLUMNS} FROM calendar_events WHERE start_at < ? AND end_at > ? "
            "ORDER BY start_at, event_id",
            (iso_utc(end), iso_utc(start)),
        ).fetchall()
        return [_row(row) for row in found]

    def get(self, calendar_id: str, event_id: str) -> CalendarEvent | None:
        row = self.conn.execute(
            f"SELECT {_COLUMNS} FROM calendar_events WHERE calendar_id = ? AND event_id = ?",
            (calendar_id, event_id),
        ).fetchone()
        return _row(row) if row else None

    def around(self, when: datetime, *, before_s: float, after_s: float) -> list[CalendarEvent]:
        return self.between(when - timedelta(seconds=before_s), when + timedelta(seconds=after_s))

    def count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM calendar_events").fetchone()[0])

    def clear(self) -> int:
        gone = self.conn.execute("DELETE FROM calendar_events").rowcount
        return int(gone or 0)
