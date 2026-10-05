"""``EventStore.add`` stores events without marking anything else removed.

The test seed used ``replace_window`` one event at a time, and since D82 that marks every
other event overlapping the window as removed: tomorrow's all-day event hid the timed
events under it, and the "Up next" specs failed on CI whenever UTC evening put the next
event on tomorrow (2026-10-05).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.gcal.events import CalendarEvent, EventStore
from tests.fixtures.api import build_harness, seed_calendar_account

NOW = datetime(2026, 10, 4, 21, 47, tzinfo=UTC)


def _event(
    account: str, event_id: str, start: datetime, end: datetime, **extra: object
) -> CalendarEvent:
    return CalendarEvent(
        account_id=account,
        calendar_id="primary",
        event_id=event_id,
        title=event_id,
        start=start,
        end=end,
        ical_uid=f"{event_id}@seed",
        response="accepted",
        **extra,  # type: ignore[arg-type]
    )


def test_an_all_day_event_added_after_a_timed_one_leaves_it_live(
    tmp_path: Path, app_home: Path
) -> None:
    harness = build_harness(tmp_path)
    account = seed_calendar_account(harness.services)
    store = EventStore(harness.services.conn)
    vendor = NOW + timedelta(minutes=153)  # tomorrow, in UTC
    tomorrow = datetime(2026, 10, 5, tzinfo=UTC)
    store.add([_event(account, "vendor", vendor, vendor + timedelta(hours=1))], synced_at=NOW)
    store.add(
        [_event(account, "leave", tomorrow, tomorrow + timedelta(days=1), all_day=True)],
        synced_at=NOW,
    )
    live = {e.event_id for e in store.between(NOW, NOW + timedelta(days=2), accounts=None)}
    assert live == {"vendor", "leave"}


def test_a_sync_still_marks_what_google_stopped_returning(tmp_path: Path, app_home: Path) -> None:
    harness = build_harness(tmp_path)
    account = seed_calendar_account(harness.services)
    store = EventStore(harness.services.conn)
    start = NOW + timedelta(hours=1)
    store.add([_event(account, "gone", start, start + timedelta(hours=1))], synced_at=NOW)
    store.replace_window(account, "primary", NOW, NOW + timedelta(days=1), [], synced_at=NOW)
    assert store.between(NOW, NOW + timedelta(days=1), accounts=None) == []
