"""The enrichment seam (EXECUTION-PLAN.md Phase 6b).

Calendar is out of scope for V1. What ships is this protocol plus ``NullSource``, so a
later integration is one implementation of an existing interface rather than a change to
the pipeline. Enrichment is **advisory and never blocking**.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from app.log import get

log = get(__name__)

DEFAULT_TIMEOUT_S = 2.0


@dataclass(frozen=True)
class Enrichment:
    title: str | None = None
    participants: tuple[str, ...] = ()
    agenda: str | None = None
    recipients: tuple[str, ...] = ()
    raw: dict[str, Any] | None = None  # stored verbatim in meetings.calendar_json

    def as_raw(self) -> dict[str, Any]:
        if self.raw is not None:
            return dict(self.raw)
        return {
            "title": self.title,
            "participants": list(self.participants),
            "agenda": self.agenda,
            "recipients": list(self.recipients),
        }


@runtime_checkable
class EnrichmentSource(Protocol):
    name: str

    def for_meeting(self, started_at: datetime, ended_at: datetime | None) -> Enrichment | None: ...


@dataclass
class EnrichmentOutcome:
    enrichment: Enrichment | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.enrichment is not None


def fetch(
    source: EnrichmentSource,
    started_at: datetime,
    ended_at: datetime | None = None,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> EnrichmentOutcome:
    """Ask the source, with a hard timeout. A meeting never waits on enrichment."""
    outcome = EnrichmentOutcome()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="enrich")
    try:
        future = executor.submit(source.for_meeting, started_at, ended_at)
        try:
            outcome.enrichment = future.result(timeout=timeout_s)
        except FutureTimeout:
            message = f"enrichment source {source.name!r} timed out after {timeout_s:g}s"
            log.warning("%s", message)
            outcome.warnings.append(message)
        except Exception as exc:
            message = f"enrichment source {source.name!r} failed: {exc}"
            log.warning("%s", message)
            outcome.warnings.append(message)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return outcome
