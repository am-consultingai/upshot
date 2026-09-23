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
    llm: Any = None
    #: Injected in tests; otherwise built from ``llm.fallback_provider`` when needed.
    fallback_llm: Any = None
    detector: Any = None
    calendar: Any = None  # app.gcal.oauth.CalendarAuth
    calendar_sync: Any = None  # app.gcal.sync.CalendarSync
    calendar_invites: Any = None  # app.gcal.invite.InviteReader
    extras: dict[str, Any] = field(default_factory=dict)

    def close(self) -> None:
        if self.worker is not None:
            self.worker.stop()
        # The detector was started but never stopped here, so it kept polling against a
        # closing database during shutdown.
        if self.detector is not None:
            self.detector.stop()
        if self.calendar_sync is not None:
            self.calendar_sync.stop()
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
    queue = JobQueue(conn, clock)
    events = EventBus()
    calendar, calendar_sync, source = build_calendar(cfg, conn, events, clock)
    from app.gcal.invite import InviteReader
    from app.gcal.source import CalendarNow

    invites = InviteReader(calendar)

    calendar_now = CalendarNow(calendar_sync.store, available=calendar.connected)
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
    from app.notify import make_notifier

    services.notifier = make_notifier(cfg, events=events)
    if with_recorder:
        from app.audio.factory import make_capture

        services.recorder = Recorder(cfg, lambda track: make_capture(cfg, track), clock=clock)
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
        )
    return services


def build_calendar(
    cfg: Config, conn: sqlite3.Connection, events: EventBus, clock: Clock
) -> tuple[Any, Any, Any]:
    """The Google connection, its event cache and sync, and the enrichment source.

    Built whether or not an account is connected: every piece asks ``connected()`` and
    does nothing until it is, so connecting in Settings needs no restart.
    """
    from app.gcal.events import EventStore
    from app.gcal.oauth import CalendarAuth
    from app.gcal.sync import CalendarSync

    store = EventStore(conn)
    sync_ref: list[Any] = []

    def on_auth(**payload: Any) -> None:
        events.publish("calendar", **payload)
        if sync_ref and payload.get("state") == "connected":
            sync_ref[0].kick()

    auth = CalendarAuth(
        cfg.secrets,
        publish=on_auth,
        app_url=f"http://127.0.0.1:{cfg.server_port}/settings#calendar",
    )
    sync = CalendarSync(
        auth,
        store,
        clock=clock,
        publish=lambda **payload: events.publish("calendar", **payload),
    )
    sync_ref.append(sync)
    source = None
    if str(cfg.get("enrichment.source", "google")) == "google":
        from app.gcal.source import GoogleCalendarSource

        source = GoogleCalendarSource(store, available=auth.connected)
    return auth, sync, source
