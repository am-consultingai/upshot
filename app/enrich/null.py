"""The only enrichment source that ships in V1: one that knows nothing."""

from __future__ import annotations

from datetime import datetime

from app.enrich.source import Enrichment


class NullSource:
    name = "null"

    def for_meeting(self, started_at: datetime, ended_at: datetime | None) -> Enrichment | None:
        return None
