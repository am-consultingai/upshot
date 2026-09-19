"""The calendar as an enrichment source (Calendar 4, z8tj1h8jrk).

It implements the ``EnrichmentSource`` seam that has existed since V1, so creating a
meeting did not change. It reads only the local cache, never Google: the meeting-start
path stays a database query inside the 2-second advisory timeout, and works offline.

What a match leaves on a meeting is a *snapshot*, stored in ``calendar_json``: the event's
title and attendee names as they were when matched. Renaming the event next week does not
rename the recording, and deleting it does not lose the name.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from app.enrich.source import Enrichment
from app.gcal.events import CalendarEvent, EventStore, iso_utc
from app.gcal.match import MATCHED, NONE, PROPOSED, Verdict, match, meeting_like

#: How far around a recording to look in the cache.
SEARCH_MARGIN = timedelta(hours=3)


def event_ref(event: CalendarEvent) -> dict[str, Any]:
    return {
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
) -> dict[str, Any]:
    """What a meeting keeps of the event it was matched to. Names, never addresses."""
    names, more = event.participants()
    return {
        "event": event_ref(event),
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


def verdict_payload(verdict: Verdict) -> dict[str, Any]:
    if verdict.state == MATCHED and verdict.best:
        return snapshot(
            verdict.best.event, state=MATCHED, source="auto", confidence=verdict.confidence
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

    def __init__(self, store: EventStore, *, available: Callable[[], bool]) -> None:
        self.store = store
        #: False when no account is connected: then there is nothing to say at all, not
        #: even "no event matched".
        self.available = available

    def for_meeting(self, started_at: datetime, ended_at: datetime | None) -> Enrichment | None:
        if not self.available():
            return None
        end = ended_at or started_at
        events = self.store.between(started_at - SEARCH_MARGIN, end + SEARCH_MARGIN)
        verdict = match(events, started_at, ended_at)
        payload = verdict_payload(verdict)
        if verdict.state == MATCHED and verdict.best:
            names = tuple(payload["participants"])
            return Enrichment(title=verdict.best.event.title, participants=names, raw=payload)
        if verdict.state == NONE and not events and self.store.count() == 0:
            # Connected, but nothing has synced yet: no evidence either way.
            return None
        return Enrichment(raw=payload)


class CalendarNow:
    """What the detector asks once a second: is a real meeting on right now, or about to
    start? Answered from the cache, so it costs a query and never a request.

    A real meeting means one ``meeting_like`` accepts: not declined, not all-day, not a
    soft hold, and not a solo block. A blocked-out hour is not a call.
    """

    def __init__(self, store: EventStore, *, available: Callable[[], bool]) -> None:
        self.store = store
        self.available = available

    def current(self, now: datetime, *, lead_s: float = 120.0) -> CalendarEvent | None:
        if not self.available():
            return None
        events = self.store.between(now, now + timedelta(seconds=lead_s))
        live = [e for e in events if meeting_like(e) is None]
        if not live:
            return None
        # The one that started most recently: in back-to-back meetings, the new one.
        return max(live, key=lambda e: e.start)
