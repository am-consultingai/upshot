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
    detector: Any = None
    extras: dict[str, Any] = field(default_factory=dict)

    def close(self) -> None:
        if self.worker is not None:
            self.worker.stop()
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
    services = Services(
        config=cfg,
        conn=conn,
        dao=dao,
        queue=queue,
        meetings=MeetingService(cfg, dao, queue, clock=clock),
        events=events,
        auth=AuthState(),
        clock=clock,
        mailer=Mailer(cfg),
    )
    from app.notify import make_notifier

    services.notifier = make_notifier(cfg, events=events)
    if with_recorder:
        from app.audio.factory import make_capture

        services.recorder = Recorder(cfg, lambda track: make_capture(cfg, track), clock=clock)
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
