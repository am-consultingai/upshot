"""The calendar as an enrichment source (Calendar 4, z8tj1h8jrk).

It implements the ``EnrichmentSource`` seam that has existed since V1, so creating a
meeting did not change. It reads only the local cache, never Google: the meeting-start
path stays a database query inside the 2-second advisory timeout, and works offline.

What a match leaves on a meeting is a *snapshot*, stored in ``calendar_json``: the event's
title and attendee names as they were when matched. Renaming the event next week does not
rename the recording, and deleting it does not lose the name.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable
from datetime import datetime, timedelta
from typing import Any

from app.enrich.source import Enrichment
from app.gcal.events import CalendarEvent, EventStore, iso_utc
from app.gcal.match import MATCHED, NONE, PROPOSED, Verdict, dedupe, match, meeting_like

#: How far around a recording to look in the cache.
SEARCH_MARGIN = timedelta(hours=3)


def event_ref(event: CalendarEvent) -> dict[str, Any]:
    return {
        "account_id": event.account_id,
        "calendar_id": event.calendar_id,
        "event_id": event.event_id,
        "ical_uid": event.ical_uid,
        "recurring_event_id": event.recurring_event_id,
        "original_start": event.original_start,
        "start": iso_utc(event.start),
        "end": iso_utc(event.end),
    }


def snapshot(
    event: CalendarEvent,
    *,
    state: str,
    source: str,
    confidence: float | None = None,
    reason: str = "",
    accounts: Iterable[str] = (),
) -> dict[str, Any]:
    """What a meeting keeps of the event it was matched to. Names, never addresses.

    ``accounts`` is every connected account the same meeting appears on (D82): a meeting
    belongs to all of them, and stays shown while any one of them is.
    """
    names, more = event.participants()
    members = sorted({a for a in (event.account_id, *accounts) if a})
    return {
        "event": event_ref(event),
        "accounts": members,
        "title": event.title,
        "participants": names,
        "participants_more": more,
        "private": event.private,
        "conference_url": event.conference_url,
        "match": {
            "state": state,
            "source": source,
            "confidence": confidence,
            "reason": reason,
        },
    }


def verdict_payload(verdict: Verdict, copies: Iterable[str] = ()) -> dict[str, Any]:
    if verdict.state == MATCHED and verdict.best:
        return snapshot(
            verdict.best.event,
            state=MATCHED,
            source="auto",
            confidence=verdict.confidence,
            accounts=copies,
        )
    payload: dict[str, Any] = {
        "match": {
            "state": verdict.state,
            "source": "auto",
            "confidence": verdict.confidence or None,
            "reason": verdict.reason,
        }
    }
    if verdict.state == PROPOSED:
        payload["candidates"] = [
            {**event_ref(c.event), "title": c.event.title, "overlap": round(c.overlap, 3)}
            for c in verdict.candidates[:5]
        ]
    return payload


class GoogleCalendarSource:
    name = "google"

    def __init__(self, store: EventStore, *, active: Callable[[], Collection[str]]) -> None:
        self.store = store
        #: The accounts whose events count: connected, and neither hidden nor removed.
        #: None of them means there is nothing to say at all, not even "no event matched".
        self.active = active

    def for_meeting(self, started_at: datetime, ended_at: datetime | None) -> Enrichment | None:
        accounts = self.active()
        if not accounts:
            return None
        end = ended_at or started_at
        events = self.store.between(
            started_at - SEARCH_MARGIN, end + SEARCH_MARGIN, accounts=accounts
        )
        verdict = match(events, started_at, ended_at)
        copies: list[str] = []
        if verdict.state == MATCHED and verdict.best:
            copies = self.store.copies(verdict.best.event, accounts=accounts)
        payload = verdict_payload(verdict, copies)
        if verdict.state == MATCHED and verdict.best:
            names = tuple(payload["participants"])
            return Enrichment(title=verdict.best.event.title, participants=names, raw=payload)
        if verdict.state == NONE and not events and not any(self.store.count(a) for a in accounts):
            # Connected, but nothing has synced yet: no evidence either way.
            return None
        return Enrichment(raw=payload)


class CalendarNow:
    """What the detector asks once a second: is a real meeting on right now, or about to
    start? Answered from the cache, so it costs a query and never a request.

    A real meeting means one ``meeting_like`` accepts: not declined, not all-day, not a
    soft hold, and not a solo block. A blocked-out hour is not a call.
    """

    def __init__(self, store: EventStore, *, active: Callable[[], Collection[str]]) -> None:
        self.store = store
        #: Only these accounts give reminders and a detector signal: a hidden one is quiet.
        self.active = active

    def soon(self, now: datetime, *, ahead_s: float) -> list[CalendarEvent]:
        """Meetings on now or starting within ``ahead_s``, earliest first (reminders, D76).

        The same meeting on two accounts is reminded of once."""
        accounts = self.active()
        if not accounts:
            return []
        events = dedupe(
            self.store.between(now, now + timedelta(seconds=ahead_s), accounts=accounts)
        )
        events.sort(key=lambda e: (e.start, e.account_id, e.event_id))
        return [e for e in events if meeting_like(e) is None]

    def current(self, now: datetime, *, lead_s: float = 120.0) -> CalendarEvent | None:
        accounts = self.active()
        if not accounts:
            return None
        events = self.store.between(now, now + timedelta(seconds=lead_s), accounts=accounts)
        live = [e for e in dedupe(events) if meeting_like(e) is None]
        if not live:
            return None
        # The one that started most recently: in back-to-back meetings, the new one.
        return max(live, key=lambda e: e.start)
