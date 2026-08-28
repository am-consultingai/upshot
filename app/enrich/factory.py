"""Which enrichment source is wired — ``null`` unless a test says otherwise."""

from __future__ import annotations

from app.config import Config
from app.enrich.source import EnrichmentSource


def make_source(config: Config) -> EnrichmentSource:
    kind = str(config.get("enrichment.source", "null"))
    if kind == "fake":
        from app.enrich.fake import FakeSource

        return FakeSource()
    from app.enrich.null import NullSource

    return NullSource()
