"""Which calendar event a recording belongs to (Calendar 5, z8tj1h8jrm).

The part most likely to be quietly wrong: attaching the wrong attendees to a recording is
worse than attaching none. So the rules are written down here as code, and the verdict
has three values, not two — *matched*, *proposed* (a best guess the user confirms), and
*none* — rather than every recording being forced onto its nearest event.

Scoring is by overlap, never containment: a recording started five minutes late and run
ten minutes over is still that meeting. Only UTC instants are compared, so a DST change
cannot move anything.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.gcal.events import CalendarEvent

#: Below this share of the recording, an event is not a candidate at all.
MIN_OVERLAP = 0.2
#: Auto-match needs the event to cover at least this share of the recording...
AUTO_OVERLAP = 0.5
#: ...to have started within this of the recording, unless the recording lies wholly
#: inside the event (joining twenty minutes late is still that meeting)...
AUTO_START_S = 10 * 60
INSIDE_OVERLAP = 0.9
#: ...and to beat the runner-up by this factor. Otherwise it is only proposed.
AUTO_MARGIN = 1.5
#: At the start of a recording its end is unknown. It is scored as if it ran this long,
#: which is what lets a recording begun just before a meeting match that meeting.
ASSUMED_LENGTH_S = 15 * 60

MATCHED = "matched"
PROPOSED = "proposed"
NONE = "none"


@dataclass(frozen=True)
class Candidate:
    event: CalendarEvent
    overlap: float  # share of the recording the event covers, 0..1
    start_delta_s: float  # |event start - recording start|

    def beats(self, other: Candidate) -> bool:
        if self.overlap != other.overlap:
            return self.overlap > other.overlap
        return self.start_delta_s < other.start_delta_s


@dataclass(frozen=True)
class Verdict:
    state: str  # matched|proposed|none
    best: Candidate | None = None
    candidates: tuple[Candidate, ...] = field(default_factory=tuple)
    reason: str = ""

    @property
    def confidence(self) -> float:
        return round(self.best.overlap, 3) if self.best else 0.0


def meeting_like(event: CalendarEvent) -> str | None:
    """Why an event can never be auto-matched, or None when it can.

    Some of these still come back as proposals — a solo block might be a call nobody
    sent an invitation for — but none of them is ever attached without asking.
    """
    if event.all_day:
        return "all-day events are never meetings"
    if event.declined:
        return "declined"
    if event.status == "cancelled":
        return "cancelled"
    if event.event_type not in (None, "default", "fromGmail"):
        return f"not a meeting ({event.event_type})"
    if event.transparent:
        return "marked as free: a soft hold"
    if not event.others and not event.conference_url:
        return "no one else invited and no meeting link: probably a block of time"
    return None


def excluded(event: CalendarEvent) -> bool:
    """Events that are not even proposed."""
    return (
        event.all_day
        or event.declined
        or event.status == "cancelled"
        or event.event_type not in (None, "default", "fromGmail")
    )


def dedupe(events: Iterable[CalendarEvent]) -> list[CalendarEvent]:
    """The same meeting on two calendars counts once — the copy this user attends.

    An occurrence is its iCalUID plus its original start; a copy where the user is an
    attendee (has a response) beats a copy seen through someone else's calendar.
    """
    chosen: dict[tuple[str, str], CalendarEvent] = {}
    for event in events:
        key = event.occurrence
        held = chosen.get(key)
        if held is None or (held.response is None and event.response is not None):
            chosen[key] = event
    return list(chosen.values())


def match(
    events: Iterable[CalendarEvent],
    started: datetime,
    ended: datetime | None = None,
) -> Verdict:
    """Pick the event a recording from ``started`` to ``ended`` belongs to."""
    end = ended or started + timedelta(seconds=ASSUMED_LENGTH_S)
    length = max(1.0, (end - started).total_seconds())
    candidates: list[Candidate] = []
    for event in dedupe(events):
        if excluded(event):
            continue
        shared = (min(end, event.end) - max(started, event.start)).total_seconds()
        overlap = max(0.0, shared) / length
        if overlap < MIN_OVERLAP:
            continue
        delta = abs((event.start - started).total_seconds())
        candidates.append(Candidate(event, min(1.0, overlap), delta))
    if not candidates:
        return Verdict(NONE, reason="no event overlaps this recording")
    ranked = sorted(candidates, key=lambda c: (-c.overlap, c.start_delta_s))
    best = ranked[0]
    runner = ranked[1] if len(ranked) > 1 else None
    why_not = meeting_like(best.event)
    if why_not:
        return Verdict(PROPOSED, best, tuple(ranked), why_not)
    if best.overlap < AUTO_OVERLAP:
        return Verdict(PROPOSED, best, tuple(ranked), "covers less than half the recording")
    if best.start_delta_s > AUTO_START_S and best.overlap < INSIDE_OVERLAP:
        return Verdict(PROPOSED, best, tuple(ranked), "starts more than ten minutes away")
    if runner is not None and best.overlap < AUTO_MARGIN * runner.overlap:
        return Verdict(PROPOSED, best, tuple(ranked), "another event fits almost as well")
    return Verdict(MATCHED, best, tuple(ranked), "")
