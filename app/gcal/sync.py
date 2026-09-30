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

Every connected account that is shown syncs, each on its own clock and with its own
failures (D82). A hidden or removed account is not asked at all; its rows stay as they
were, and it catches up when it is shown again.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.clock import Clock, SystemClock
from app.gcal.events import CalendarEvent, EventStore, iso_utc, parse
from app.gcal.oauth import CalendarAccounts, CalendarAuthError, CalendarUnavailable
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


@dataclass
class _AccountState:
    """Each account syncs on its own: one dead token or rate limit stops only that one."""

    next_near: float = 0.0
    next_wide: float = 0.0
    backoff: float = 0.0
    auth_stopped: bool = False
    last_synced_at: datetime | None = None
    last_error: str | None = None


class CalendarSync:
    def __init__(
        self,
        accounts: CalendarAccounts,
        store: EventStore,
        *,
        clock: Clock | None = None,
        on_synced: Callable[[], None] | None = None,
        publish: Callable[..., Any] | None = None,
    ) -> None:
        self.accounts = accounts
        self.store = store
        self.clock = clock or SystemClock()
        self.on_synced = on_synced
        self.publish = publish
        self._states: dict[str, _AccountState] = {}
        self._last_wall: float | None = None
        self._last_mono: float | None = None
        self._kick = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ public

    def connected(self) -> bool:
        return self.accounts.connected()

    def kick(self, account_id: str | None = None) -> None:
        """Sync now: one account after it connects or is shown, or all when asked."""
        with self._lock:
            for key, state in self._states.items():
                if account_id is None or key == account_id:
                    state.auth_stopped = False
                    state.next_near = state.next_wide = state.backoff = 0.0
        self._kick.set()

    def status(self, account_id: str | None = None) -> dict[str, Any]:
        """One account's sync, or the latest across all of them."""
        with self._lock:
            if account_id is not None:
                states = [self._states.get(account_id, _AccountState())]
            else:
                states = list(self._states.values())
        synced = [s.last_synced_at for s in states if s.last_synced_at is not None]
        errors = [s.last_error for s in states if s.last_error]
        return {
            "last_synced_at": iso_utc(max(synced)) if synced else None,
            "sync_error": errors[0] if errors else None,
            "cached_events": self.store.count(account_id),
        }

    # ------------------------------------------------------------------ one pass

    def tick(self) -> bool:
        """Do whatever is due, for every shown and connected account. True when anything
        was synced."""
        mono = self.clock.monotonic()
        self._notice_sleep(mono)
        active = self.accounts.syncable_ids()
        synced_any = False
        count = 0
        for account_id, calendar_ids in self._sources(active).items():
            with self._lock:
                state = self._states.setdefault(account_id, _AccountState())
            done = self._tick_account(account_id, calendar_ids, state, mono)
            if done is not None:
                synced_any = True
                count += done
        if not synced_any:
            return False
        if self.on_synced is not None:
            try:
                self.on_synced()
            except Exception:  # matching is advisory; a failure must not stop syncing
                log.exception("re-matching after a sync failed")
        if self.publish is not None:
            self.publish(state="synced", events=count)
        return True

    def _sources(self, active: frozenset[str]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for account_id, calendar_id in self.accounts.registry.sources():
            if account_id in active:
                out.setdefault(account_id, []).append(calendar_id)
        return out

    def _tick_account(
        self, account_id: str, calendar_ids: list[str], state: _AccountState, mono: float
    ) -> int | None:
        if state.auth_stopped:
            return None
        due_wide = mono >= state.next_wide
        due_near = mono >= state.next_near
        if not (due_wide or due_near):
            return None
        now = self.clock.now()
        if due_wide:
            window = (now - WIDE_SPAN, now + WIDE_SPAN)
        else:
            window = (now - NEAR_BACK, now + NEAR_AHEAD)
        try:
            count = sum(self.sync_window(account_id, cal, *window) for cal in calendar_ids)
        except CalendarAuthError as exc:
            state.last_error = str(exc)
            state.auth_stopped = True
            log.warning("calendar sync of %s stopped until reconnect: %s", account_id, exc)
            return None
        except CalendarUnavailable as exc:
            state.backoff = min(MAX_BACKOFF_S, max(NEAR_EVERY_S, state.backoff * 2))
            state.next_near = mono + state.backoff
            state.last_error = f"Google Calendar could not be reached ({exc})."
            log.info("calendar sync of %s deferred %.0fs: %s", account_id, state.backoff, exc)
            return None
        state.last_synced_at = now
        state.last_error = None
        state.backoff = 0.0
        state.next_near = mono + NEAR_EVERY_S
        if due_wide:
            state.next_wide = mono + WIDE_EVERY_S
        log.info(
            "calendar %s synced %s: %d events", account_id, "wide" if due_wide else "near", count
        )
        return count

    def sync_window(self, account_id: str, calendar_id: str, start: datetime, end: datetime) -> int:
        auth = self.accounts.auth_for(account_id)
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
            body = auth.get(f"/calendars/{calendar_id}/events", params)
            for item in body.get("items") or []:
                parsed = parse(item, calendar_id, account_id)
                if parsed is not None:
                    events.append(parsed)
            token = body.get("nextPageToken")
            if not token:
                break
        self.store.replace_window(
            account_id, calendar_id, start, end, events, synced_at=self.clock.now()
        )
        return len(events)

    def _notice_sleep(self, mono: float) -> None:
        wall = time.time()
        if (
            self._last_wall is not None
            and self._last_mono is not None
            and (wall - self._last_wall) - (mono - self._last_mono) > SLEEP_JUMP_S
        ):
            log.info("the machine slept; resyncing the calendar")
            with self._lock:
                for state in self._states.values():
                    state.next_near = state.next_wide = 0.0
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
