"""Time. Nothing outside this module calls ``datetime.now()`` or ``time.sleep()``."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """The only source of time in the application."""

    def now(self) -> datetime:
        """Timezone-aware local time."""
        ...

    def monotonic(self) -> float:
        """Seconds from an arbitrary origin; never goes backwards."""
        ...

    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    """Real time."""

    def now(self) -> datetime:
        return datetime.now().astimezone()

    def monotonic(self) -> float:
        return time.perf_counter()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class FakeClock:
    """Deterministic time. ``sleep`` advances instead of blocking."""

    def __init__(
        self,
        start: datetime | None = None,
        monotonic_start: float = 0.0,
    ) -> None:
        if start is None:
            start = datetime.fromisoformat("2026-08-28T14:00:00+03:00")
        if start.tzinfo is None:
            raise ValueError("FakeClock needs an aware datetime")
        self._now = start
        self._mono = monotonic_start
        self.slept: list[float] = []

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._mono

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)
        self._mono += seconds

    def set(self, when: datetime) -> None:
        if when.tzinfo is None:
            raise ValueError("FakeClock needs an aware datetime")
        delta = (when - self._now).total_seconds()
        self._now = when
        self._mono += delta


def iso(when: datetime, timespec: str = "milliseconds") -> str:
    """ISO-8601 with offset — the only timestamp format written anywhere.

    Millisecond precision by default: ``jobs.not_before`` carries a jittered sub-second
    backoff, and every timestamp in the database is compared as a string, so they all
    have to agree on precision.
    """
    if when.tzinfo is None:
        raise ValueError("refusing to serialize a naive datetime")
    return when.isoformat(timespec=timespec)


def parse_iso(text: str) -> datetime:
    when = datetime.fromisoformat(text)
    if when.tzinfo is None:
        raise ValueError(f"timestamp without offset: {text!r}")
    return when
