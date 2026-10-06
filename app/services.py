"""The composition root's container: everything a stage or a route may need."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from app.api.security import AuthState
from app.audio.recorder import Recorder
from app.clock import Clock, SystemClock
from app.config import Config
from app.db.dao import Dao, connect
from app.events import EventBus
from app.log import get
from app.mail import Mailer
from app.meetings import MeetingService
from app.pipeline.queue import JobQueue
from app.pipeline.worker import Worker

log = get(__name__)


@dataclass
class Services:
    config: Config
    conn: sqlite3.Connection
    dao: Dao
    queue: JobQueue
    meetings: MeetingService
    events: EventBus
    auth: AuthState
    clock: Clock
    mailer: Mailer
    recorder: Recorder | None = None
    worker: Worker | None = None
    notifier: Any = None
    asr: Any = None  # overridden in tests via config-selected fakes
    #: app.asr.classify.Classifier: the meeting-language decision; injected in tests.
    classifier: Any = None
    llm: Any = None
    #: Injected in tests; otherwise built from ``llm.fallback_provider`` when needed.
    fallback_llm: Any = None
    detector: Any = None
    #: app.prompts.Prompts: the offer to record that the banner and the toasts share.
    prompts: Any = None
    calendar: Any = None  # app.gcal.oauth.CalendarAuth
    calendar_sync: Any = None  # app.gcal.sync.CalendarSync
    calendar_invites: Any = None  # app.gcal.invite.InviteReader
    terms: Any = None  # app.legal.terms.TermsService
    #: app.transcription.store.TranscriptionStore: file transcription jobs (D86).
    transcriptions: Any = None
    #: app.transcription.scheduler.Scheduler: one FIFO across meeting and file jobs.
    scheduler: Any = None
    updates: Any = None  # app.updates.service.UpdateService
    installer: Any = None  # app.updates.install.UpdateInstaller
    reporter: Any = None  # app.diagnostics.reporter.CrashReporter
    feedback: Any = None  # app.diagnostics.feedback.FeedbackSender
    extras: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Here rather than in build(): tests and the selftest assemble Services by hand,
        # and file transcription is part of every one of them.
        if self.transcriptions is None:
            from app.transcription.store import TranscriptionStore

            config = self.config
            self.transcriptions = TranscriptionStore(
                self.conn, lambda: config.data_root, self.clock, events=self.events
            )
        if self.scheduler is None:
            from app.transcription.scheduler import Scheduler

            self.scheduler = Scheduler(self.queue, self.transcriptions)

    def close(self) -> None:
        if self.worker is not None:
            self.worker.stop()
        # The detector was started but never stopped here, so it kept polling against a
        # closing database during shutdown.
        if self.detector is not None:
            self.detector.stop()
        if self.calendar_sync is not None:
            self.calendar_sync.stop()
        if self.terms is not None:
            self.terms.stop()
        if self.updates is not None:
            self.updates.stop()
        if self.installer is not None:
            self.installer.stop()
        # A connect in progress holds a listening socket open for up to five minutes.
        if self.calendar is not None:
            self.calendar.close()
        self.conn.close()


def build(
    config: Config | None = None,
    *,
    clock: Clock | None = None,
    with_worker: bool = True,
    with_recorder: bool = True,
) -> Services:
    cfg = config or Config.load()
    clock = clock or SystemClock()
    conn = connect(fts=str(cfg.get("db.fts", "auto")) != "off")
    dao = Dao(conn, clock)
    try:
        # Summaries written before they were searchable (D61); each folder is read once.
        from app.pipeline.stages.render import backfill_search

        backfill_search(dao)
    except Exception:  # search is not worth failing a start over
        log.exception("could not backfill summaries into search")
    queue = JobQueue(conn, clock)
    events = EventBus()
    calendar, calendar_sync, source = build_calendar(cfg, conn, events, clock)
    from app.gcal.invite import InviteReader
    from app.gcal.source import CalendarNow

    invites = InviteReader(calendar)
    calendar.on_hidden.append(invites.forget)

    calendar_now = CalendarNow(calendar_sync.store, active=calendar.active_ids)
    services = Services(
        config=cfg,
        conn=conn,
        dao=dao,
        queue=queue,
        meetings=MeetingService(cfg, dao, queue, clock=clock, source=source),
        events=events,
        auth=AuthState(),
        clock=clock,
        mailer=Mailer(cfg),
        calendar=calendar,
        calendar_sync=calendar_sync,
        calendar_invites=invites,
    )
    calendar_sync.on_synced = lambda: services.meetings.rematch_recent()
    from app.legal.terms import TermsService

    services.terms = TermsService(
        cfg, clock=clock, publish=lambda **payload: events.publish("legal", **payload)
    )
    from app.notify import make_notifier

    services.notifier = make_notifier(cfg, events=events)
    from app.prompts import Prompts

    services.prompts = Prompts(events)
    if with_recorder:
        from app.audio.factory import make_capture

        services.recorder = Recorder(cfg, lambda track: make_capture(cfg, track), clock=clock)
    from app.diagnostics.reporter import CrashReporter

    # Crash reports, only with consent and a DSN (D87).
    services.reporter = CrashReporter(cfg)
    from app import paths as _paths
    from app.diagnostics.feedback import FeedbackSender
    from app.version import build_info as _build_info

    # Feedback from inside the app, anonymous unless the user adds an email (D87).
    _info = _build_info()
    services.feedback = FeedbackSender(
        cfg,
        dsn=_info.sentry_dsn,
        version=_info.version,
        commit=_info.commit,
        home=_paths.app_home(),
    )
    from app.updates.service import UpdateService

    recorder = services.recorder
    # A download pauses while a meeting is recorded (D87): the recording owns the machine.
    services.updates = UpdateService(
        cfg,
        clock=clock,
        busy=(lambda: recorder.is_active()) if recorder is not None else (lambda: False),
        publish=lambda **payload: events.publish("updates", **payload),
    )
    # Built whatever the mode is. `tick` does nothing while detection is off, and
    # building it unconditionally is what lets Settings turn detection on without a
    # restart — a switch that needs the application restarted is not a switch.
    if with_recorder:
        from app.detect.detector import Detector
        from app.detect.factory import make_sources

        services.detector = Detector(
            cfg,
            dao,
            services.meetings,
            services.recorder,  # type: ignore[arg-type]
            make_sources(cfg, services.recorder),
            clock=clock,
            notifier=services.notifier,
            events=events,
            calendar=calendar_now,
            prompts=services.prompts,
        )
    from app import window
    from app.detect.detector import DetectorState
    from app.updates.install import UpdateInstaller, meeting_soon

    detector = services.detector
    # When the ready update may install (D87): never over a recording, a call, a
    # transcription, or a calendar meeting about to start.
    services.installer = UpdateInstaller(
        cfg,
        services.updates,
        recording=(lambda: recorder.is_active()) if recorder is not None else (lambda: False),
        in_call=(lambda: detector.state is not DetectorState.IDLE)
        if detector is not None
        else (lambda: False),
        # Meeting jobs and file jobs (D86): both are a transcription an update must wait for.
        jobs_busy=services.scheduler.busy if services.scheduler is not None else queue.busy,
        meeting_soon=lambda: meeting_soon(calendar_now, clock),
        window_open=lambda: window.count_open() > 0,
        clock=clock,
    )
    if with_worker:
        from app.pipeline.stages import registry

        services.worker = Worker(
            dao=dao,
            queue=queue,
            config=cfg,
            stages=registry(),
            clock=clock,
            recorder=services.recorder,
            services=services,
            transcriptions=services.transcriptions,
            scheduler=services.scheduler,
        )
    return services


def build_calendar(
    cfg: Config, conn: sqlite3.Connection, events: EventBus, clock: Clock
) -> tuple[Any, Any, Any]:
    """The Google accounts, their event cache and sync, and the enrichment source.

    Built whether or not any account is connected: every piece asks which accounts are
    active and does nothing until one is, so connecting in Settings needs no restart.
    An older install's one account is adopted first (D82).
    """
    from app import window
    from app.gcal.accounts import AccountRegistry, adopt_legacy
    from app.gcal.events import EventStore
    from app.gcal.oauth import CalendarAccounts
    from app.gcal.sync import CalendarSync

    store = EventStore(conn)
    registry = AccountRegistry(conn, clock)
    sync_ref: list[Any] = []

    def on_auth(**payload: Any) -> None:
        events.publish("calendar", **payload)
        if sync_ref and payload.get("account_id"):
            # Connected, restored or shown again: that account catches up now.
            sync_ref[0].kick(payload["account_id"])

    accounts = CalendarAccounts(
        cfg.secrets,
        registry,
        publish=on_auth,
        # The sign-in ran in the user's browser: Upshot's window comes back in front.
        on_complete=lambda ok: window.bring_to_front(),
    )
    _adopt(conn, registry, cfg, accounts, adopt_legacy)
    sync = CalendarSync(
        accounts,
        store,
        clock=clock,
        publish=lambda **payload: events.publish("calendar", **payload),
    )
    sync_ref.append(sync)
    source = None
    if str(cfg.get("enrichment.source", "google")) == "google":
        from app.gcal.source import GoogleCalendarSource

        source = GoogleCalendarSource(store, active=accounts.active_ids)
    return accounts, sync, source


def _adopt(conn: sqlite3.Connection, registry: Any, cfg: Config, accounts: Any, adopt: Any) -> None:
    """Adopt an older install's account, and rewrite the meta.json of the meetings whose
    snapshot now names it. Never fatal: the next start tries again."""
    from app import meta
    from app.db.dao import Dao

    try:
        adopted = adopt(registry, cfg.secrets, accounts.probe_refresh)
    except Exception:
        log.exception("could not adopt the older Google connection")
        return
    if not adopted:
        return
    dao = Dao(conn)
    for meeting_id in adopted[1]:
        meeting = dao.get_meeting(meeting_id)
        if meeting is not None and meeting.path.is_dir():
            try:
                meta.mirror(meeting)
            except OSError as exc:
                log.warning("meta.json of %s not rewritten: %s", meeting_id, exc)
