"""Meeting lifecycle: create, enrich, commit, finish, discard, purge.

One place creates meetings, whatever the source — manual Start, the detector, or an
import — so enrichment, folder layout and the state machine have exactly one owner.
"""

from __future__ import annotations

import contextlib
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


#: In a meeting's folder: its deletion was asked for while a stage ran. The next start
#: finishes it (``finish_interrupted_deletes``) if the app closed before the stage stopped.
DELETING_MARKER = ".deleting"

#: A meeting nobody named: the weekday, date and time (``default_title``).
DEFAULT_TITLE = "default"

#: Titles something automatic may replace. A title the user typed never is.
AUTOMATIC_TITLES = frozenset({"window", "llm", "calendar", DEFAULT_TITLE})


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


def event_account(meeting: Meeting) -> str | None:
    """The account whose copy of the event this meeting is matched to: whose token reads
    its invitation. From the snapshot, else the column (both are written together)."""
    ref = calendar_payload(meeting).get("event") or {}
    account = ref.get("account_id") if isinstance(ref, dict) else None
    return str(account) if account else meeting.calendar_account_id


def event_key(payload: dict[str, Any]) -> tuple[str, str] | None:
    """Which calendar occurrence a snapshot is matched to, the same on every account that
    has a copy of it; None unless it is matched. The start tells apart the occurrences of
    a recurring event, which share their iCal UID."""
    if (payload.get("match") or {}).get("state") != "matched":
        return None
    return ref_key(payload.get("event") or {})


def ref_key(ref: Any) -> tuple[str, str] | None:
    """:func:`event_key` of one event reference, matched or not."""
    if not isinstance(ref, dict):
        return None
    ident = ref.get("ical_uid") or ref.get("event_id")
    if not ident:
        return None
    return str(ident), str(ref.get("original_start") or ref.get("start") or "")


def candidate_keys(payload: dict[str, Any]) -> set[tuple[str, str]]:
    """The calendar meetings a snapshot is, or may be: its own when matched, else those
    it proposes."""
    state = (payload.get("match") or {}).get("state")
    if state == "matched":
        key = event_key(payload)
        return {key} if key else set()
    if state == "proposed":
        return {k for c in payload.get("candidates") or [] if (k := ref_key(c)) is not None}
    return set()


def transcribed(meeting: Meeting) -> bool:
    """Whether the meeting's audio has been transcribed (its segments are on disk)."""
    from app.pipeline.stages.transcribe import segments_path

    return segments_path(meeting.path).exists()


def continuable_until(meeting: Meeting, within_s: float) -> datetime:
    """Until when a recording of the same calendar event carries this meeting on (D89): while
    the event is on, and ``within_s`` after the later of its end and the recording's."""
    last = parse_iso(meeting.ended_at) if meeting.ended_at else parse_iso(meeting.started_at)
    ref = calendar_payload(meeting).get("event") or {}
    if isinstance(ref, dict) and ref.get("end"):
        with contextlib.suppress(ValueError):
            last = max(last, parse_iso(str(ref["end"])))
    return last + timedelta(seconds=within_s)


def _members(payload: dict[str, Any]) -> tuple[str | None, list[str]]:
    """The matched account and every account of a snapshot; nothing unless it is matched."""
    if (payload.get("match") or {}).get("state") != "matched":
        return None, []
    ref = payload.get("event") or {}
    account = ref.get("account_id") if isinstance(ref, dict) else None
    others = [str(a) for a in payload.get("accounts") or [] if a]
    return (str(account) if account else None), others


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
        # A date, a time and a random suffix: never the title (D89). Names live in the
        # database, so renaming or reassigning a meeting touches no folder.
        meeting_id = self.dao.new_meeting_id(started)
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
        meeting = self.enrich(meeting)
        if not meeting.title:
            meeting = self.dao.update_meeting(
                meeting.id, title=self.default_title(started), title_source=DEFAULT_TITLE
            )
        return meeting

    def default_title(self, when: datetime) -> str:
        """The weekday, the date and the time as Windows shows them ("Tuesday 30/09/2026
        14:05"), and " (2)", " (3)"... when a meeting already has that name. Anything
        else replaces it: a calendar event, the summary's title, the user's own."""
        from app.locale_formats import default_meeting_title

        base = default_meeting_title(when.astimezone(), str(self.config.get("ui.language", "en")))
        taken = self.dao.titles_starting_with(base)
        if base not in taken:
            return base
        number = 2
        while f"{base} ({number})" in taken:
            number += 1
        return f"{base} ({number})"

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
        if previous_match.get("state") == "proposed" and state == "none":
            # Nor does "nothing overlaps" undo the meetings it may be (D89): the detector
            # proposed them from the call itself, which the clock alone cannot see.
            return meeting
        fields: dict[str, Any] = {"calendar_json": json.dumps(raw, ensure_ascii=False)}
        if enrichment.title and title_is_open(meeting):
            fields["title"] = enrichment.title
            fields["title_source"] = "calendar"
        updated = self._write_calendar(meeting.id, raw, fields)
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
            return self._write_calendar(meeting_id, raw, fields)
        fields = {"calendar_json": json.dumps(payload, ensure_ascii=False)}
        if payload.get("title") and meeting.title_source != "user":
            fields["title"] = payload["title"]
            fields["title_source"] = "calendar"
        return self._write_calendar(meeting_id, payload, fields)

    def _write_calendar(
        self, meeting_id: str, payload: dict[str, Any], fields: dict[str, Any]
    ) -> Meeting:
        """Store a snapshot and, from it, which calendar accounts the meeting is on (D82).
        A meeting matched to nothing belongs to no account and is always shown."""
        self.dao.update_meeting(meeting_id, **fields)
        account, others = _members(payload)
        self.dao.set_calendar_accounts(meeting_id, account, others)
        return self.dao.require_meeting(meeting_id)

    # -- lifecycle ---------------------------------------------------------

    def committed(self, meeting: Meeting, folder: Path) -> Meeting:
        """The recorder has started writing. Mirror the record to disk."""
        meta.mirror(meeting, committed_at=iso(self.clock.now()), folder=str(folder))
        return meeting

    def to_continue(self, payload: dict[str, Any] | None = None) -> Meeting | None:
        """The meeting a recording starting now carries on, if there is one (D88).

        A recording of the same calendar event whose transcription has not begun, while
        the event is on and until ``detection.continue_within_s`` after the later of its
        end and the recording's. The event is ``payload`` (a snapshot, when the start
        named one), else the one the calendar matches now. On machine B one call became
        seven meetings: each restart of the recording, whatever caused it, made a new one.
        """
        if payload is None:
            outcome = fetch(
                self.enrichment_source,
                self.clock.now(),
                None,
                timeout_s=float(self.config.get("enrichment.timeout_s", 2.0)),
            )
            payload = outcome.enrichment.as_raw() if outcome.enrichment else {}
        # A proposal counts too: the clock alone may not settle a rejoin near a meeting's
        # end (it assumes a recording runs 15 minutes), but a recording of one of the
        # meetings it may be, just ended, does.
        keys = candidate_keys(payload)
        if not keys:
            return None
        now = self.clock.now()
        within = float(self.config.get("detection.continue_within_s", 900))
        found: Meeting | None = None
        merged = self.dao.aliases()  # merged away, its folder not yet gone: never continued
        for meeting in self.dao.list_meetings(
            state=str(MeetingState.RECORDED), include_hidden=True, limit=50
        ):
            if meeting.id in merged:
                continue
            if not meeting.ended_at or event_key(calendar_payload(meeting)) not in keys:
                continue
            ended = parse_iso(meeting.ended_at)
            if now < ended or now > continuable_until(meeting, within):
                continue
            if found is None or ended > parse_iso(found.ended_at or ""):
                found = meeting
        return found

    def reopen(self, meeting_id: str) -> tuple[Meeting, int] | None:
        """Record more of a meeting that just ended (D88): it is recording again, and its
        waiting jobs leave the queue until this part ends. With how long ago it ended, in
        milliseconds, for the recorder to keep as silence. None when a stage of it has
        started meanwhile: it is being transcribed as it was, and stays as it was."""
        meeting = self.dao.require_meeting(meeting_id)
        if meeting.state != MeetingState.RECORDED or not meeting.ended_at:
            return None
        if not self.queue.withdraw(meeting_id):
            return None
        after = self.clock.now() - parse_iso(meeting.ended_at)
        self.dao.set_state(meeting_id, MeetingState.RECORDING)
        updated = self.dao.update_meeting(meeting_id, ended_at=None, duration_s=None)
        meta.mirror(updated)
        log.info("meeting %s continues, %d s after it ended", meeting_id, after.total_seconds())
        return updated, max(0, round(after.total_seconds() * 1000))

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
        # The minimum catches a recording the detector started by accident. One the user
        # started (Start, a notification's button, a file imported) is one they chose,
        # however short: a 99-second test call vanished on machine B (D76), and a
        # 41-second clip after "imported".
        if seconds < minimum and meeting.source == "detected":
            log.info("meeting %s is %ss (< %ss) — discarding", meeting_id, seconds, minimum)
            return self.discard(meeting_id)
        updated = self.dao.set_state(meeting_id, MeetingState.RECORDED)
        # Now the end is known, which is what tells a late start from the next meeting.
        updated = self.rematch(meeting_id)
        meta.mirror(self.dao.require_meeting(meeting_id))
        if enqueue:
            self.queue.enqueue(meeting_id, JobStage.TRANSCRIBE)
            # Another recording of the same calendar meeting, made before this one and too
            # far along to be continued (D88): one meeting, so one recording (D89).
            merged = self.merge_into_existing(meeting_id)
            if merged is not None:
                return merged
        return updated

    # -- one meeting, one recording (D89) ----------------------------------

    def merge_target(self, meeting: Meeting) -> Meeting | None:
        """The other recording of ``meeting``'s calendar meeting, if there is one: both
        matched to the same occurrence, which only happens when it is the same meeting."""
        from app.merge import NOT_MERGEABLE

        key = event_key(calendar_payload(meeting))
        if key is None:
            return None
        since = iso(parse_iso(meeting.started_at) - timedelta(days=1))
        found: Meeting | None = None
        merged = self.dao.aliases()
        for other in self.dao.list_meetings(frm=since, include_hidden=True, limit=200):
            if other.id == meeting.id or MeetingState(other.state) in NOT_MERGEABLE:
                continue
            if other.id in merged:
                continue
            if event_key(calendar_payload(other)) != key:
                continue
            if found is None or other.started_at < found.started_at:
                found = other
        return found

    def merge_into_existing(self, meeting_id: str) -> Meeting | None:
        """Merge ``meeting_id`` with the other recording of its calendar meeting, when there
        is one and both can be merged now. The surviving meeting, or None."""
        from app.merge import MergeRefused

        meeting = self.dao.require_meeting(meeting_id)
        other = self.merge_target(meeting)
        if other is None:
            return None
        if parse_iso(other.started_at) > parse_iso(meeting.started_at):
            meeting, other = other, meeting  # the later one waits, the earlier stays
        if transcribed(other) and not transcribed(meeting):
            # The earlier part has its transcript: transcribe only the later part, then
            # join the two, rather than the whole meeting again.
            meta.update(meeting.path, merge_into=other.id)
            log.info("%s joins %s once it is transcribed", meeting.id, other.id)
            return None
        try:
            return self.merge(other.id, meeting.id)
        except (MergeRefused, OSError) as exc:
            # Tried again once a stage of the later part is done (the worker's merge_pending).
            meta.update(meeting.path, merge_into=other.id)
            log.info("%s and %s not merged yet: %s", other.id, meeting.id, exc)
            return None

    def merge_pending(self, meeting_id: str) -> Meeting | None:
        """Join ``meeting_id`` to the earlier recording it waits for, if it can be now."""
        from app.merge import MergeRefused

        meeting = self.dao.get_meeting(meeting_id)
        if meeting is None:
            return None
        waiting = meta.read(meeting.path).get("merge_into")
        if not waiting:
            return None
        target = self.dao.get_meeting(self.dao.resolve(str(waiting)))
        if target is None or target.id == meeting.id:
            meta.update(meeting.path, merge_into=None)
            return None
        if transcribed(target) and not transcribed(meeting):
            return None
        try:
            merged = self.merge(target.id, meeting.id)
        except MergeRefused as exc:
            log.info("%s still waits to join %s: %s", meeting.id, target.id, exc)
            return None
        meta.update(merged.path, merge_into=None)
        return merged

    def merge(self, first_id: str, second_id: str) -> Meeting:
        """Join two recordings of one meeting; the earlier one survives (D89)."""
        from app.merge import merge

        return merge(self, first_id, second_id)

    def resume_jobs(self, meeting_id: str) -> None:
        """Put back the jobs a refused merge took out of the queue."""
        self.queue.restore(meeting_id)

    def redo_after_merge(self, meeting: Meeting, *, transcribed: bool) -> None:
        """A merged meeting is processed again: from the joined transcript when both parts
        had one, else transcribed whole. Every later stage is redone too."""
        from app.pipeline.states import STAGE_ORDER

        stage = JobStage.ASSEMBLE if transcribed else JobStage.TRANSCRIBE
        entry = MeetingState.TRANSCRIBING if transcribed else MeetingState.RECORDED
        if meeting.state != entry:
            self.dao.set_state(meeting.id, entry)
        for later in STAGE_ORDER[STAGE_ORDER.index(stage) :]:
            self.queue.request_rerun(meeting.id, later)
        self.queue.retry(meeting.id, stage)
        meta.mirror(self.dao.require_meeting(meeting.id))

    def resummarize(self, meeting_id: str) -> bool:
        """The calendar meeting changed: summarize again, when an AI is configured and a
        summary was already written (D89). The previous one is kept beside it, as
        ``*.prev.*``, in case the new one is worse. Whether it was asked for."""
        from app.pipeline.states import STAGE_ORDER

        if str(self.config.get("llm.provider", "none")) == "none":
            return False
        meeting = self.dao.require_meeting(meeting_id)
        if MeetingState(meeting.state) not in (
            MeetingState.SUMMARIZED,
            MeetingState.RENDERED,
            MeetingState.DELIVERED,
        ):
            return False  # not summarized yet: when it is, it reads the new meeting
        for name in ("notes.json", "summary.html"):
            current = meeting.path / name
            if current.exists():
                stem, _, suffix = name.partition(".")
                shutil.copyfile(current, meeting.path / f"{stem}.prev.{suffix}")
        for later in STAGE_ORDER[STAGE_ORDER.index(JobStage.SUMMARIZE) :]:
            self.queue.request_rerun(meeting_id, later)
        self.queue.retry(meeting_id, JobStage.SUMMARIZE)
        log.info("meeting %s is summarized again for its new calendar meeting", meeting_id)
        return True

    def ask_if_unsettled(self, meeting_id: str, notifier: Any, *, calendar_connected: bool) -> bool:
        """After a recording ends: "Which meeting was this?" when its calendar meeting is
        not settled (D89). Whether it asked."""
        meeting = self.dao.get_meeting(self.dao.resolve(meeting_id))
        if meeting is None or notifier is None:
            return False
        if not self.needs_meeting(meeting, calendar_connected=calendar_connected):
            return False
        candidates = calendar_payload(meeting).get("candidates") or []
        notifier.which_meeting(meeting.id, meeting.title or "", list(candidates))
        return True

    def needs_meeting(self, meeting: Meeting, *, calendar_connected: bool) -> bool:
        """A recording whose calendar meeting is not settled: the user is asked (D89).

        Settled is matched (by the user, the detector or the calendar) or "not on my
        calendar" said by the user. With no calendar connected there is nothing to ask,
        and a recording made before one was (nothing stored on it) is matched for display
        when its page opens rather than asked about."""
        if not calendar_connected or not meeting.calendar_json:
            return False
        if MeetingState(meeting.state) in (
            MeetingState.RECORDING,
            MeetingState.ARMED,
            MeetingState.DISCARDED,
        ):
            return False
        match = calendar_payload(meeting).get("match") or {}
        if match.get("state") == "matched":
            return False
        return match.get("source") != "user"

    def discard(self, meeting_id: str) -> Meeting:
        meeting = self.dao.set_state(meeting_id, MeetingState.DISCARDED)
        # A committed folder keeps its audio; its meta.json must not still say RECORDING
        # (machine B, job 014). One never committed gets no folder made for it here.
        if meta.path_for(meeting.path).exists():
            meta.mirror(meeting)
        return meeting

    def recover_orphans(self, recording: str | None = None) -> list[str]:
        """Meetings left "recording" with no recorder behind them. Their ids.

        A process that died mid-recording, or a start that failed before the fix (machine
        B, D76: a meeting stuck at "recording 615:48" whose Stop answered "not recording")
        leaves a RECORDING row nothing will ever end. Audio on disk is kept and
        transcribed; a row with none is discarded. ``recording`` is the one the recorder
        is writing right now, never touched.
        """
        from app.audio.writer import track_files

        repaired: list[str] = []
        for meeting in self.dao.list_meetings(
            state=MeetingState.RECORDING, limit=1000, include_hidden=True
        ):
            if meeting.id == recording:
                continue
            tracks = track_files(meeting.path)
            if tracks:
                import wave

                seconds = 0
                for path in tracks.values():
                    try:
                        with wave.open(str(path), "rb") as audio:
                            seconds = max(seconds, audio.getnframes() // audio.getframerate())
                    except (OSError, EOFError, wave.Error):
                        continue  # a header never finalised: the length is not known
                log.warning("meeting %s was left recording; keeping its audio", meeting.id)
                ended = parse_iso(meeting.started_at) + timedelta(seconds=seconds)
                self.finish(meeting.id, ended_at=ended, duration_s=seconds)
            else:
                log.warning("meeting %s was left recording with no audio; discarding", meeting.id)
                self.discard(meeting.id)
            repaired.append(meeting.id)
        return repaired

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

    def finish_interrupted_deletes(self) -> int:
        """At start: meetings whose deletion was asked for as the app closed. How many."""
        done = 0
        for meeting in self.dao.list_meetings(limit=100_000, include_hidden=True):
            if (Path(meeting.folder) / DELETING_MARKER).exists():
                try:
                    self.purge(meeting)
                    done += 1
                except (OSError, ValueError) as exc:
                    log.warning("could not finish deleting %s: %s", meeting.id, exc)
        return done

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
