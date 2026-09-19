"""Which enrichment source is wired when none is handed in.

``google`` needs the event cache and the connection, so ``services.build`` makes it and
passes it to ``MeetingService``; anything that builds a service without one gets null.
"""

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
