"""Keeping the event cache current (Calendar 2, z8tj1h8jrh).

Polling, because push is not available to this app: ``events.watch`` needs a public HTTPS
endpoint with a CA-signed certificate, and 127.0.0.1 is neither. Two rhythms:

* **near**, every 60 s: twelve hours back to two hours ahead. It is what the detector
  reads to know a meeting is starting, and what matching reads for the recording just
  finished.
* **wide**, every 10 minutes: thirty days either side, for the calendar view.

The quota arithmetic, done before coding: 1,440 near polls and 144 wide polls a day,
each one or two requests, is about 1,600-3,200 requests per user per day against
1,000,000 per project per day and 600 per user per minute. Three orders of magnitude of
headroom, so nothing here needs to be cleverer than a timer.

A plain windowed ``events.list`` rather than sync tokens: ``timeMin``/``timeMax`` are
not allowed alongside ``syncToken``, so a windowed token is under-specified, and a
re-list of the window is one or two idempotent requests with no 410 recovery path.

Offline is normal, not an error: the cache is served, the status says when it last
synced, and the next tick tries again. A dead token is the opposite: syncing stops until
the user reconnects, and nothing retries it.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from app.clock import Clock, SystemClock
from app.gcal.events import CalendarEvent, EventStore, iso_utc, parse
from app.gcal.oauth import CalendarAuth, CalendarAuthError, CalendarUnavailable
from app.log import get

log = get(__name__)

NEAR_EVERY_S = 60.0
WIDE_EVERY_S = 600.0
NEAR_BACK = timedelta(hours=12)
NEAR_AHEAD = timedelta(hours=2)
WIDE_SPAN = timedelta(days=30)
PAGE_SIZE = 250
MAX_PAGES = 20
#: Backoff after a failure, doubling to this ceiling: rate limits and outages both pass.
MAX_BACKOFF_S = 300.0
#: The wall clock moved this much further than the monotonic clock: the machine slept,
#: and everything cached about "now" is stale.
SLEEP_JUMP_S = 120.0


class CalendarSync:
    def __init__(
        self,
        auth: CalendarAuth,
        store: EventStore,
        *,
        calendars: tuple[str, ...] = ("primary",),
        clock: Clock | None = None,
        on_synced: Callable[[], None] | None = None,
        publish: Callable[..., Any] | None = None,
    ) -> None:
        self.auth = auth
        self.store = store
        self.calendars = calendars
        self.clock = clock or SystemClock()
        self.on_synced = on_synced
        self.publish = publish
        self.last_synced_at: datetime | None = None
        self.last_error: str | None = None
        self._next_near = 0.0
        self._next_wide = 0.0
        self._backoff = 0.0
        self._last_wall: float | None = None
        self._last_mono: float | None = None
        self._kick = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ public

    def connected(self) -> bool:
        return self.auth.connected()

    def kick(self) -> None:
        """Sync everything now: after connecting, and when the user asks."""
        self._next_near = self._next_wide = 0.0
        self._backoff = 0.0
        self._kick.set()

    def status(self) -> dict[str, Any]:
        return {
            "last_synced_at": iso_utc(self.last_synced_at) if self.last_synced_at else None,
            "sync_error": self.last_error,
            "cached_events": self.store.count(),
        }

    def forget(self) -> int:
        """Delete the cache. Disconnecting does this; so does the Settings button."""
        with self._lock:
            gone = self.store.clear()
            self.last_synced_at = None
            self.last_error = None
        log.info("calendar cache cleared (%d events)", gone)
        return gone

    # ------------------------------------------------------------------ one pass

    def tick(self) -> bool:
        """Do whatever is due. True when something was synced."""
        if not self.connected():
            return False
        mono = self.clock.monotonic()
        self._notice_sleep(mono)
        due_wide = mono >= self._next_wide
        due_near = mono >= self._next_near
        if not (due_wide or due_near):
            return False
        now = self.clock.now()
        if due_wide:
            window = (now - WIDE_SPAN, now + WIDE_SPAN)
        else:
            window = (now - NEAR_BACK, now + NEAR_AHEAD)
        try:
            with self._lock:
                count = sum(self.sync_window(cal, *window) for cal in self.calendars)
                self.last_synced_at = now
                self.last_error = None
        except CalendarAuthError as exc:
            self.last_error = str(exc)
            log.warning("calendar sync stopped until reconnect: %s", exc)
            return False
        except CalendarUnavailable as exc:
            self._backoff = min(MAX_BACKOFF_S, max(NEAR_EVERY_S, self._backoff * 2))
            self._next_near = mono + self._backoff
            self.last_error = f"Google Calendar could not be reached ({exc})."
            log.info("calendar sync deferred %.0fs: %s", self._backoff, exc)
            return False
        self._backoff = 0.0
        self._next_near = mono + NEAR_EVERY_S
        if due_wide:
            self._next_wide = mono + WIDE_EVERY_S
        log.info("calendar synced %s: %d events", "wide" if due_wide else "near", count)
        if self.on_synced is not None:
            try:
                self.on_synced()
            except Exception:  # matching is advisory; a failure must not stop syncing
                log.exception("re-matching after a sync failed")
        if self.publish is not None:
            self.publish(state="synced", events=count)
        return True

    def sync_window(self, calendar_id: str, start: datetime, end: datetime) -> int:
        events: list[CalendarEvent] = []
        token: str | None = None
        for _ in range(MAX_PAGES):
            params: dict[str, Any] = {
                "timeMin": iso_utc(start),
                "timeMax": iso_utc(end),
                "singleEvents": "true",  # recurrences arrive as their instances
                "orderBy": "startTime",
                "maxResults": PAGE_SIZE,
                # Birthdays, out-of-office and working-location entries are not meetings.
                "eventTypes": ["default", "fromGmail", "focusTime"],
            }
            if token:
                params["pageToken"] = token
            body = self.auth.get(f"/calendars/{calendar_id}/events", params)
            for item in body.get("items") or []:
                parsed = parse(item, calendar_id)
                if parsed is not None:
                    events.append(parsed)
            token = body.get("nextPageToken")
            if not token:
                break
        self.store.replace_window(calendar_id, start, end, events, synced_at=self.clock.now())
        return len(events)

    def _notice_sleep(self, mono: float) -> None:
        wall = time.time()
        if (
            self._last_wall is not None
            and self._last_mono is not None
            and (wall - self._last_wall) - (mono - self._last_mono) > SLEEP_JUMP_S
        ):
            log.info("the machine slept; resyncing the calendar")
            self._next_near = self._next_wide = 0.0
        self._last_wall, self._last_mono = wall, mono

    # ------------------------------------------------------------------ thread

    def run_forever(self, interval_s: float = 5.0) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("calendar sync tick failed")
            self._kick.wait(interval_s)
            self._kick.clear()

    def start(self) -> threading.Thread:
        if self._thread and self._thread.is_alive():
            return self._thread
        self._stop.clear()
        self._thread = threading.Thread(target=self.run_forever, name="gcal-sync", daemon=True)
        self._thread.start()
        return self._thread

    def stop(self) -> None:
        self._stop.set()
        self._kick.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
