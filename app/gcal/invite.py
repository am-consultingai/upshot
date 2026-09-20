"""The full invitation, read live and never stored.

A matched recording keeps a small snapshot — title, attendee names, the meeting link — so
that it survives the event being edited or deleted. Everything else in the invitation
(the agenda, the links inside it, the files attached to it) is fetched from Google when
it is actually wanted: by the meeting page while someone is looking at it, and by the
summarize stage while it builds its prompt.

Fetching rather than storing is the point. The invitation lives in the calendar, which is
where the user maintains it, and the recordings database stays a database of recordings.
An event edited after the meeting reads correctly here, and deleting the event deletes
what Upshot can show.

Email addresses are dropped at this boundary, as they are everywhere else: an attendee is
a display name, or a name made from the local part of their address.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

from app.gcal.events import name_from_email
from app.log import get

log = get(__name__)

#: A fetched invitation is reused for this long. The meeting page and the summarize stage
#: usually ask within moments of each other, and an invitation does not change by the
#: second.
CACHE_S = 60.0

#: Only the fields that are shown or summarized. `attendees` carries addresses, which are
#: reduced to names below and never leave this module.
FIELDS = (
    "summary,description,location,start,end,htmlLink,hangoutLink,conferenceData,"
    "attendees(displayName,email,resource,optional,responseStatus,self),"
    "organizer(displayName,email),attachments(title,fileUrl,mimeType)"
)

_URL = re.compile(r"https?://[^\s<>\"']+")


class _Text(HTMLParser):
    """Google's description is HTML. This turns it back into readable lines, keeping the
    address of every link so nothing that was written as a link is lost."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.links: list[str] = []
        self._href: str | None = None
        self._label: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("br", "p", "div", "tr", "li", "ul", "ol"):
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")
        if tag == "a":
            href = dict(attrs).get("href")
            self._href = href
            self._label = []
            if href:
                self.links.append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            label = "".join(self._label).strip()
            # "text (https://…)" unless the text is the address itself.
            if self._href and label and label not in self._href:
                self.parts.append(f" ({self._href})")
            self._href = None
            self._label = []
        if tag in ("p", "div", "li"):
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)
        if self._href is not None:
            self._label.append(data)


def plain_text(html: str) -> tuple[str, list[str]]:
    """The description as text, and every link found in it."""
    parser = _Text()
    parser.feed(html)
    parser.close()
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()
    # A link harvested from the text carries whatever punctuation followed it, including
    # the bracket this parser itself added around a labelled link.
    found = [url.rstrip(").,;'\"") for url in _URL.findall(text)]
    links = list(dict.fromkeys(parser.links + found))
    return text, links


@dataclass(frozen=True)
class Invite:
    """One invitation, as it is right now in Google Calendar. Never persisted."""

    title: str | None = None
    start: str | None = None
    end: str | None = None
    location: str | None = None
    organizer: str | None = None
    attendees: tuple[str, ...] = ()
    declined: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    agenda: str = ""
    links: tuple[str, ...] = ()
    attachments: tuple[dict[str, str], ...] = ()
    conference_url: str | None = None
    html_link: str | None = None

    def as_api(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "start": self.start,
            "end": self.end,
            "location": self.location,
            "organizer": self.organizer,
            "attendees": list(self.attendees),
            "declined": list(self.declined),
            "optional": list(self.optional),
            "agenda": self.agenda,
            "links": list(self.links),
            "attachments": [dict(a) for a in self.attachments],
            "conference_url": self.conference_url,
            "html_link": self.html_link,
        }

    def as_prompt(self) -> str:
        """The invitation as context for the summary model.

        Everything the invitation holds except addresses: what the meeting was called,
        when it ran, who was invited, what the organizer wrote, and what they attached.
        A model that knows the agenda writes about the meeting that was intended, not
        only the words that happened to be transcribed.
        """
        lines: list[str] = ["Meeting details, from the calendar invitation:"]
        if self.title:
            lines.append(f"Title: {self.title}")
        when = " to ".join(part for part in (self.start, self.end) if part)
        if when:
            lines.append(f"Scheduled: {when}")
        if self.organizer:
            lines.append(f"Organised by: {self.organizer}")
        if self.attendees:
            invited = ", ".join(self.attendees)
            if self.optional:
                invited += f" (optional: {', '.join(self.optional)})"
            lines.append(
                f"Invited: {invited}. Action items should belong to one of these people "
                "or to the speaker labelled ME."
            )
        if self.declined:
            lines.append(f"Declined, so probably absent: {', '.join(self.declined)}")
        if self.location:
            lines.append(f"Location: {self.location}")
        if self.attachments:
            named = ", ".join(
                f"{a.get('title') or 'file'} ({a.get('fileUrl')})" for a in self.attachments
            )
            lines.append(f"Files attached to the invitation: {named}")
        if self.agenda:
            lines.append("What the organizer wrote in the invitation:")
            lines.append(self.agenda)
        return "\n".join(lines)


def parse(item: dict[str, Any]) -> Invite:
    attendees: list[str] = []
    declined: list[str] = []
    optional: list[str] = []
    for raw in item.get("attendees") or []:
        if raw.get("resource") or raw.get("self"):
            continue  # a meeting room is not a person; the user is ME in the transcript
        name = str(raw.get("displayName") or name_from_email(str(raw.get("email", ""))))
        if raw.get("responseStatus") == "declined":
            declined.append(name)
            continue
        attendees.append(name)
        if raw.get("optional"):
            optional.append(name)
    organizer = item.get("organizer") or {}
    organizer_name = (
        str(organizer.get("displayName") or name_from_email(str(organizer.get("email", ""))))
        if organizer
        else None
    )
    agenda, links = plain_text(str(item.get("description") or ""))
    conference = None
    for entry in (item.get("conferenceData") or {}).get("entryPoints") or []:
        if entry.get("entryPointType") == "video" and entry.get("uri"):
            conference = str(entry["uri"])
            break
    return Invite(
        title=item.get("summary"),
        start=(item.get("start") or {}).get("dateTime") or (item.get("start") or {}).get("date"),
        end=(item.get("end") or {}).get("dateTime") or (item.get("end") or {}).get("date"),
        location=item.get("location"),
        organizer=organizer_name,
        attendees=tuple(attendees),
        declined=tuple(declined),
        optional=tuple(optional),
        agenda=agenda,
        links=tuple(links),
        attachments=tuple(
            {"title": str(a.get("title") or ""), "url": str(a.get("fileUrl") or "")}
            for a in item.get("attachments") or []
            if a.get("fileUrl")
        ),
        conference_url=conference or item.get("hangoutLink"),
        html_link=item.get("htmlLink"),
    )


@dataclass
class InviteReader:
    """Fetches invitations, briefly remembering the last few. Raises what the caller
    should react to: ``CalendarAuthError`` to reconnect, ``CalendarUnavailable`` to try
    again later."""

    auth: Any
    monotonic: Any = None
    _cache: dict[tuple[str, str], tuple[Invite, float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _now(self) -> float:
        import time

        return float(self.monotonic() if self.monotonic else time.monotonic())

    def fetch(self, calendar_id: str, event_id: str) -> Invite:
        key = (calendar_id, event_id)
        now = self._now()
        with self._lock:
            held = self._cache.get(key)
            if held and now - held[1] < CACHE_S:
                return held[0]
        body = self.auth.get(f"/calendars/{calendar_id}/events/{event_id}", {"fields": FIELDS})
        invite = parse(body)
        with self._lock:
            self._cache[key] = (invite, now)
            if len(self._cache) > 32:  # a page at a time; never a store
                oldest = min(self._cache, key=lambda k: self._cache[k][1])
                self._cache.pop(oldest, None)
        return invite
