"""Which calendar meeting a call is (D89).

With one calendar meeting on, a call is that meeting, unless its link says another app.
With several (overbooking, or meetings that overlap), the clues decide:

- the window titles: Google Meet titles its tab "Meet - <meeting title>", Teams shows the
  meeting's name too (Zoom does not);
- the meeting's link against the app holding the microphone: a Zoom link and Zoom.exe, a
  Meet link and a browser.

Exactly one meeting fitting best is an assignment; so is, among meetings the clues do
not tell apart, the one that began last (back to back, the next meeting). Meetings booked
at the same time that nothing tells apart are not: the user is offered them and picks.

On machine B (2026-10-06) two meetings were on at 14:00 and the one that happened to
sort last was offered, though Chrome's window named the other.
"""

from __future__ import annotations

import ntpath
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

BROWSERS = (
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
    "brave.exe",
    "opera.exe",
    "vivaldi.exe",
    "arc.exe",
    "iexplore.exe",
)

#: A meeting link's host, and the apps that join such a meeting.
PROVIDERS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("zoom.us", "zoomgov.com"), ("zoom.exe", *BROWSERS)),
    (("meet.google.com",), BROWSERS),
    (("teams.microsoft.com", "teams.live.com"), ("ms-teams.exe", "teams.exe", *BROWSERS)),
    (("webex.com",), ("webex.exe", "ciscocollabhost.exe", "atmgr.exe", *BROWSERS)),
)

#: A window title must name at least this much of a meeting's title to count.
MIN_TITLE_CHARS = 4
TITLE_WEIGHT = 2
APP_WEIGHT = 1


@dataclass(frozen=True)
class Assignment:
    #: The calendar meeting the call is, when the clues settle it.
    event: Any | None
    #: Settled: one meeting fits best. Unsettled assignments are offered, never saved.
    certain: bool
    #: Every calendar meeting it could be, best first.
    candidates: tuple[Any, ...] = field(default_factory=tuple)
    reason: str = ""


def exe(process: str | None) -> str:
    return ntpath.basename(process or "").lower()


def _host(url: str | None) -> str:
    if not url:
        return ""
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def apps_for(url: str | None) -> tuple[str, ...] | None:
    """The apps that join a meeting at ``url``; None for a link of no known kind."""
    host = _host(url)
    if not host:
        return None
    for hosts, apps in PROVIDERS:
        if any(host == h or host.endswith("." + h) for h in hosts):
            return apps
    return None


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def named_in(title: str | None, windows: Iterable[str]) -> bool:
    wanted = _norm(title or "")
    if len(wanted) < MIN_TITLE_CHARS:
        return False
    return any(wanted in _norm(window) for window in windows)


def app_fits(url: str | None, process: str | None) -> bool | None:
    """True when ``process`` can join ``url``, False when it cannot, None when unknown."""
    apps = apps_for(url)
    if apps is None or not process:
        return None
    return exe(process) in apps


def clues(event: Any, process: str | None, windows: Sequence[str]) -> int:
    score = 0
    if named_in(getattr(event, "title", None), windows):
        score += TITLE_WEIGHT
    fits = app_fits(getattr(event, "conference_url", None), process)
    if fits is True:
        score += APP_WEIGHT
    elif fits is False:
        score -= APP_WEIGHT
    return score


def assign(
    events: Iterable[Any], *, process: str | None = None, windows: Sequence[str] = ()
) -> Assignment:
    """The calendar meeting a call by ``process`` is, among ``events`` (those on now)."""
    live = list(events)
    if not live:
        return Assignment(None, False, (), "no calendar meeting is on")
    scored = sorted(
        ((clues(event, process, windows), index, event) for index, event in enumerate(live)),
        key=lambda item: (-item[0], item[1]),
    )
    ranked = tuple(event for _, _, event in scored)
    best = scored[0][0]
    if len(live) == 1:
        if best < 0:
            return Assignment(
                None, False, ranked, f"its link is not for {exe(process) or 'this app'}"
            )
        return Assignment(ranked[0], True, ranked, "the only calendar meeting on")
    leaders = [event for score, _, event in scored if score == best]
    if best > 0 and len(leaders) == 1:
        return Assignment(ranked[0], True, ranked, "the window or the app names it")
    if best >= 0:
        # Overlapping meetings that began at different times: a call starting now is the
        # one that began last (back to back, the next meeting). Booked at the same time,
        # nothing here tells them apart.
        latest = max(event.start for event in leaders)
        newest = [event for event in leaders if event.start == latest]
        if len(newest) == 1:
            ranked = (newest[0], *(e for e in ranked if e is not newest[0]))
            return Assignment(newest[0], True, ranked, "the meeting that began last")
    return Assignment(
        None, False, ranked, f"{len(live)} calendar meetings are on and nothing tells them apart"
    )
