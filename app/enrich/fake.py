"""A test source that proves the seam without implementing an integration."""

from __future__ import annotations

import time
from datetime import datetime

from app.enrich.source import Enrichment


class FakeSource:
    name = "fake"

    def __init__(
        self,
        enrichment: Enrichment | None = None,
        *,
        delay_s: float = 0.0,
        raises: BaseException | None = None,
    ) -> None:
        self.enrichment = enrichment or Enrichment(
            title="Weekly Sync",
            participants=("יוסי כהן", "Dana Levi"),
            agenda="status, blockers, next release",
            recipients=("team@example.com",),
            raw={"id": "evt-1", "title": "Weekly Sync", "participants": ["יוסי כהן", "Dana Levi"]},
        )
        self.delay_s = delay_s
        self.raises = raises
        self.calls: list[tuple[datetime, datetime | None]] = []

    def for_meeting(self, started_at: datetime, ended_at: datetime | None) -> Enrichment | None:
        self.calls.append((started_at, ended_at))
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.raises is not None:
            raise self.raises
        return self.enrichment
