"""The one "should Upshot offer to record?" state, shared by the banner and the toasts (D76).

Before this the offer lived in three places that never heard of each other: a toast, a
banner built from a one-off server event, and the recorder. On machine B (2026-09-28) the
banner kept saying "Test is under way and is not being recorded" while the meeting was
being recorded, and after it had ended. Now the detector and the calendar reminders
*offer*; the page shows whatever is on offer (``/api/status``); and the offer is withdrawn
the moment anything records, the meeting's time is up, the app that made it a meeting lets
go of the microphone, or the user says it is not a meeting.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.clock import iso
from app.log import get

log = get(__name__)


@dataclass(frozen=True)
class Prompt:
    #: "calendar": a calendar meeting's time has come. "detected": an app took the
    #: microphone and it looks like a call.
    kind: str
    title: str
    at: datetime
    #: The calendar meeting, when there is one, and the account it is on (D82).
    calendar_id: str | None = None
    event_id: str | None = None
    conference_url: str | None = None
    #: When the offer lapses by itself: the meeting's scheduled end.
    until: datetime | None = None
    #: The app holding the microphone that made it a meeting; the offer ends when it lets go.
    process: str | None = None
    #: Whether letting go of the microphone withdraws it. Off only for the e2e seed, whose
    #: "Zoom" never held one.
    watch_process: bool = True
    account_id: str | None = None

    @property
    def key(self) -> str:
        if self.calendar_id and self.event_id:
            return f"{self.account_id or ''}:{self.calendar_id}:{self.event_id}"
        return f"process:{self.process or ''}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "at": iso(self.at),
            "account_id": self.account_id,
            "calendar_id": self.calendar_id,
            "event_id": self.event_id,
            "conference_url": self.conference_url,
            "until": iso(self.until) if self.until else None,
            "process": self.process,
        }


class Prompts:
    def __init__(self, events: Any = None) -> None:
        self.events = events
        self.current: Prompt | None = None
        #: Offers the user turned down, by key: "Not a meeting" is said once per meeting.
        self.dismissed: set[str] = set()
        self._lock = threading.Lock()

    def offer(self, prompt: Prompt, *, recording: bool) -> bool:
        """Put ``prompt`` on offer. False when nothing should be offered."""
        if recording or prompt.key in self.dismissed:
            return False
        with self._lock:
            if self.current == prompt:
                return False
            self.current = prompt
        log.info("offering to record %r (%s)", prompt.title, prompt.kind)
        self._publish("offered")
        return True

    def withdraw(self, why: str) -> None:
        with self._lock:
            had = self.current
            self.current = None
        if had is not None:
            log.info("offer to record %r withdrawn: %s", had.title, why)
            self._publish("withdrawn", reason=why)

    def reset(self) -> None:
        """Forget everything: the e2e seed's reset, between specs."""
        self.dismissed.clear()
        self.withdraw("reset")

    def dismiss(self) -> None:
        """ "Not a meeting": withdrawn, and not offered again for the same meeting."""
        current = self.current
        if current is not None:
            self.dismissed.add(current.key)
        self.withdraw("the user said it is not a meeting")

    def tick(self, now: datetime, *, recording: bool, holders: Iterable[str]) -> None:
        """Withdraw an offer that no longer holds. Called every second by the detector."""
        holding = set(holders)
        # "Not a meeting" on a call with no calendar meeting is about that call: once its
        # app lets go of the microphone, the next call from the same app is a new one.
        self.dismissed = {
            key
            for key in self.dismissed
            if not key.startswith("process:") or key.removeprefix("process:") in holding
        }
        current = self.current
        if current is None:
            return
        if recording:
            self.withdraw("a recording started")
        elif current.until is not None and now >= current.until:
            self.withdraw("the meeting's time is up")
        elif (
            current.kind == "detected" and current.watch_process and current.process not in holding
        ):
            self.withdraw("the app let go of the microphone")

    def snapshot(self) -> dict[str, Any] | None:
        current = self.current
        return current.as_dict() if current is not None else None

    def _publish(self, state: str, **payload: Any) -> None:
        if self.events is not None:
            self.events.publish("prompt", state=state, **payload)
