"""Meeting lifecycle: create, enrich, commit, finish, discard, purge.

One place creates meetings, whatever the source — manual Start, the detector, or an
import — so enrichment, folder layout and the state machine have exactly one owner.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Sequence
from datetime import datetime, timedelta
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


def tree_bytes(folder: Path) -> int:
    """How much disk a folder is holding."""
    folder = Path(folder)
    if not folder.is_dir():
        return 0
    return sum(path.stat().st_size for path in folder.rglob("*") if path.is_file())


def audio_bytes(folder: Path) -> int:
    """How much disk a meeting's raw audio is holding."""
    return tree_bytes(Path(folder) / "audio")


def remove_tree(target: Path) -> int:
    """Delete a folder, and report the bytes that actually went.

    `shutil.rmtree(ignore_errors=True)` lies on Windows, which refuses to unlink a file
    something else has open. Measured on the author's machine: a sweep reported success,
    wrote `audio_deleted_at`, and left `them.wav` on disk because a reader held it — so
    the meeting was recorded as swept and never retried. Deleting is not the kind of
    operation that may quietly half-succeed, so the caller is told.
    """
    target = Path(target)
    before = tree_bytes(target)
    shutil.rmtree(target, ignore_errors=True)
    if not target.exists():
        return before
    survivors = sorted(path.name for path in target.rglob("*") if path.is_file())
    raise OSError(
        f"could not remove {target}: {len(survivors)} file(s) still held "
        f"({', '.join(survivors[:4])})"
    )


def slugify(text: str, limit: int = 40) -> str:
    keep = [char if char.isalnum() or char in "-_" else "-" for char in text.strip().lower()]
    slug = "".join(keep).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:limit]


#: Titles something automatic may replace. A title the user typed never is.
AUTOMATIC_TITLES = frozenset({"window", "llm", "calendar"})


def title_is_open(meeting: Meeting) -> bool:
    return not meeting.title or meeting.title_source in AUTOMATIC_TITLES


def calendar_payload(meeting: Meeting) -> dict[str, Any]:
    if not meeting.calendar_json:
        return {}
    try:
        payload = json.loads(meeting.calendar_json)
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


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
        """Store what the calendar says, and take its title where the title is open.

        Title precedence (Calendar 4): the user's own name, then the calendar, then the
        model, then the window title. ``title_source`` records which one won, and a title
        given at creation with no source was typed by someone and is treated as theirs.
        """
        raw = enrichment.as_raw()
        state = str((raw.get("match") or {}).get("state", "matched"))
        previous = calendar_payload(meeting)
        previous_match = previous.get("match") or {}
        if previous_match.get("source") == "user":
            return meeting  # the user chose the event; nothing automatic overrides that
        if previous_match.get("state") == "matched" and state != "matched":
            # A later, vaguer look never undoes a confident match.
            return meeting
        fields: dict[str, Any] = {"calendar_json": json.dumps(raw, ensure_ascii=False)}
        if enrichment.title and title_is_open(meeting):
            fields["title"] = enrichment.title
            fields["title_source"] = "calendar"
        updated = self.dao.update_meeting(meeting.id, **fields)
        log.info(
            "enrichment from %s applied to %s (%s)",
            self.enrichment_source.name,
            meeting.id,
            state,
        )
        return updated

    def rematch(self, meeting_id: str) -> Meeting:
        """Match again with what is known now: the real end time, or events that arrived
        after the recording started. Never a meeting whose title the user wrote."""
        meeting = self.dao.require_meeting(meeting_id)
        if meeting.title_source == "user":
            return meeting
        outcome = fetch(
            self.enrichment_source,
            parse_iso(meeting.started_at),
            parse_iso(meeting.ended_at) if meeting.ended_at else None,
            timeout_s=float(self.config.get("enrichment.timeout_s", 2.0)),
        )
        if outcome.enrichment is None:
            return meeting
        return self.apply_enrichment(meeting, outcome.enrichment)

    def rematch_recent(self, days: int = 30) -> int:
        """After a sync: give every recent meeting without a confident match another look."""
        since = iso(self.clock.now() - timedelta(days=days))
        changed = 0
        for meeting in self.dao.list_meetings(frm=since, limit=500):
            match_state = (calendar_payload(meeting).get("match") or {}).get("state")
            if match_state == "matched" or meeting.title_source == "user":
                continue
            if meeting.state in (MeetingState.RECORDING, MeetingState.DISCARDED):
                continue
            if self.rematch(meeting.id).calendar_json != meeting.calendar_json:
                changed += 1
        return changed

    def choose_event(self, meeting_id: str, payload: dict[str, Any] | None) -> Meeting:
        """The user picked the event, or said there is none. Final: nothing re-matches it.

        ``payload`` is a snapshot from ``app.gcal.source.snapshot`` or None for "no event".
        """
        meeting = self.dao.require_meeting(meeting_id)
        if payload is None:
            raw: dict[str, Any] = {"match": {"state": "none", "source": "user"}}
            fields: dict[str, Any] = {"calendar_json": json.dumps(raw)}
            if meeting.title_source == "calendar":
                fields["title_source"] = "user"  # keep the name, but it is theirs now
            return self.dao.update_meeting(meeting_id, **fields)
        fields = {"calendar_json": json.dumps(payload, ensure_ascii=False)}
        if payload.get("title") and meeting.title_source != "user":
            fields["title"] = payload["title"]
            fields["title_source"] = "calendar"
        return self.dao.update_meeting(meeting_id, **fields)

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
        # Now the end is known, which is what tells a late start from the next meeting.
        updated = self.rematch(meeting_id)
        meta.mirror(self.dao.require_meeting(meeting_id))
        if enqueue:
            self.queue.enqueue(meeting_id, JobStage.TRANSCRIBE)
        return updated

    def discard(self, meeting_id: str) -> Meeting:
        return self.dao.set_state(meeting_id, MeetingState.DISCARDED)

    def interrupted(self, meeting_id: str) -> Meeting:
        return self.dao.set_state(meeting_id, MeetingState.INTERRUPTED)

    # -- removal -----------------------------------------------------------

    def inside_root(self, folder: Path) -> bool:
        """The folder comes from the database, so it must never aim a delete elsewhere."""
        root = self.config.data_root.resolve()
        folder = Path(folder).resolve()
        return root == folder or root in folder.parents

    def purge(self, meeting: Meeting) -> Path:
        """Remove a meeting entirely — folder and rows. There is no undo."""
        folder = Path(meeting.path).resolve()
        if not self.inside_root(folder):
            raise ValueError(f"{folder} is outside the data folder")
        if folder.exists():
            # Before the row, not after: a row deleted against a folder that survived
            # orphans the folder with nothing left pointing at it.
            remove_tree(folder)
        # Jobs and indexed turns go with it: both cascade on the meeting row.
        self.dao.delete_meeting(meeting.id)
        log.info("deleted meeting %s and %s", meeting.id, folder)
        return folder

    def drop_audio(self, meeting: Meeting, *, when: datetime | None = None) -> int:
        """Remove the raw audio and keep everything derived from it (D38).

        The number of bytes freed is returned, and the moment is written to ``meta.json``
        so the meeting page can say *the audio was deleted* rather than *this meeting has
        no audio*, which is what a broken recording looks like.
        """
        folder = Path(meeting.path).resolve()
        if not self.inside_root(folder):
            raise ValueError(f"{folder} is outside the data folder")
        audio = folder / "audio"
        # The mark goes on afterwards. Marking first would record a deletion that did not
        # happen, and the next sweep would skip the meeting for ever.
        freed = remove_tree(audio) if audio.exists() else 0
        meta.update(
            folder,
            audio_deleted_at=iso(when or self.clock.now()),
            audio_bytes_freed=freed,
        )
        log.info("retention removed %d bytes of audio from %s", freed, meeting.id)
        return freed

    def participants(self, meeting: Meeting) -> tuple[str, ...]:
        if not meeting.calendar_json:
            return ()
        try:
            payload = json.loads(meeting.calendar_json)
        except ValueError:
            return ()
        names = payload.get("participants") if isinstance(payload, dict) else None
        return tuple(str(name) for name in names or ())
