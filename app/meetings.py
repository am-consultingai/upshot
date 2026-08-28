"""Meeting lifecycle: create, enrich, commit, finish, discard.

One place creates meetings, whatever the source — manual Start, the detector, or an
import — so enrichment, folder layout and the state machine have exactly one owner.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from app import meta
from app.clock import Clock, SystemClock, iso, parse_iso
from app.config import Config
from app.db.dao import Dao, Meeting
from app.enrich.source import Enrichment, EnrichmentSource, fetch
from app.log import get
from app.pipeline.queue import JobQueue
from app.pipeline.states import JobStage, MeetingState

log = get(__name__)


def slugify(text: str, limit: int = 40) -> str:
    keep = [char if char.isalnum() or char in "-_" else "-" for char in text.strip().lower()]
    slug = "".join(keep).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:limit]


class MeetingService:
    def __init__(
        self,
        config: Config,
        dao: Dao,
        queue: JobQueue,
        *,
        clock: Clock | None = None,
        source: EnrichmentSource | None = None,
    ) -> None:
        self.config = config
        self.dao = dao
        self.queue = queue
        self.clock = clock or SystemClock()
        if source is None:
            from app.enrich.factory import make_source

            source = make_source(config)
        self.enrichment_source = source
        self.warnings: list[str] = []

    # -- creation ----------------------------------------------------------

    def folder_for(self, meeting_id: str) -> Path:
        return (self.config.data_root / meeting_id).resolve()

    def create(
        self,
        *,
        source: str = "manual",
        started_at: datetime | None = None,
        title: str | None = None,
        title_source: str | None = None,
        evidence: Sequence[dict[str, Any]] | None = None,
        sensitive: bool = False,
    ) -> Meeting:
        started = started_at or self.clock.now()
        meeting_id = self.dao.new_meeting_id(started)
        if title:
            slug = slugify(title)
            if slug:
                meeting_id = f"{meeting_id}_{slug}"
        meeting = self.dao.insert_meeting(
            meeting_id=meeting_id,
            folder=self.folder_for(meeting_id),
            source=source,
            state=MeetingState.RECORDING,
            started_at=started,
            profile=self._profile(),
            title=title,
            title_source=title_source,
            sensitive=sensitive,
            evidence=evidence,
        )
        log.info("created meeting %s (source=%s)", meeting.id, source)
        return self.enrich(meeting)

    def _profile(self) -> str:
        configured = self.config.profile
        return configured if configured != "auto" else "cpu-deferred"

    # -- enrichment --------------------------------------------------------

    def enrich(self, meeting: Meeting) -> Meeting:
        """Advisory: fills only empty fields, never blocks, never fails a meeting."""
        outcome = fetch(
            self.enrichment_source,
            parse_iso(meeting.started_at),
            None,
            timeout_s=float(self.config.get("enrichment.timeout_s", 2.0)),
        )
        self.warnings.extend(outcome.warnings)
        enrichment = outcome.enrichment
        if enrichment is None:
            return meeting
        return self.apply_enrichment(meeting, enrichment)

    def apply_enrichment(self, meeting: Meeting, enrichment: Enrichment) -> Meeting:
        fields: dict[str, Any] = {}
        if enrichment.title and not meeting.title:
            fields["title"] = enrichment.title
            fields["title_source"] = "calendar"
        fields["calendar_json"] = json.dumps(enrichment.as_raw(), ensure_ascii=False)
        updated = self.dao.update_meeting(meeting.id, **fields)
        log.info("enrichment from %s applied to %s", self.enrichment_source.name, meeting.id)
        return updated

    # -- lifecycle ---------------------------------------------------------

    def committed(self, meeting: Meeting, folder: Path) -> Meeting:
        """The recorder has started writing. Mirror the record to disk."""
        meta.mirror(meeting, committed_at=iso(self.clock.now()), folder=str(folder))
        return meeting

    def finish(
        self,
        meeting_id: str,
        *,
        ended_at: datetime | None = None,
        duration_s: int | None = None,
        enqueue: bool = True,
    ) -> Meeting:
        ended = ended_at or self.clock.now()
        meeting = self.dao.require_meeting(meeting_id)
        seconds = duration_s
        if seconds is None:
            seconds = int((ended - parse_iso(meeting.started_at)).total_seconds())
        minimum = self.config.min_meeting_s
        self.dao.update_meeting(meeting_id, ended_at=iso(ended), duration_s=seconds)
        if seconds < minimum:
            log.info("meeting %s is %ss (< %ss) — discarding", meeting_id, seconds, minimum)
            return self.dao.set_state(meeting_id, MeetingState.DISCARDED)
        updated = self.dao.set_state(meeting_id, MeetingState.RECORDED)
        meta.mirror(self.dao.require_meeting(meeting_id))
        if enqueue:
            self.queue.enqueue(meeting_id, JobStage.TRANSCRIBE)
        return updated

    def discard(self, meeting_id: str) -> Meeting:
        return self.dao.set_state(meeting_id, MeetingState.DISCARDED)

    def interrupted(self, meeting_id: str) -> Meeting:
        return self.dao.set_state(meeting_id, MeetingState.INTERRUPTED)

    def participants(self, meeting: Meeting) -> tuple[str, ...]:
        if not meeting.calendar_json:
            return ()
        try:
            payload = json.loads(meeting.calendar_json)
        except ValueError:
            return ()
        names = payload.get("participants") if isinstance(payload, dict) else None
        return tuple(str(name) for name in names or ())
