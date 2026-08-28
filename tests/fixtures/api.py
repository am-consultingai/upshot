"""A running app wired to fakes, with the real middleware stack."""

from __future__ import annotations

import random
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.api.security import CSRF_HEADER
from app.clock import FakeClock
from app.config import Config, default_config
from app.db.dao import Dao, connect
from app.events import EventBus
from app.mail import Mailer
from app.meetings import MeetingService
from app.notify import FakeNotifier
from app.pipeline.queue import JobQueue
from app.pipeline.stages import registry
from app.pipeline.worker import Worker
from app.services import Services

BASE_URL = "http://127.0.0.1:8000"


def free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def build_config(tmp_path: Path, **overrides: Any) -> Config:
    config = default_config(
        asr__backend="fake",
        llm__provider="fake",
        audio__capture="synthetic",
        audio__vad="energy",
        delivery__notifier="fake",
        enrichment__source="null",
        secrets__backend="memory",
        job_policy="asap",
        **overrides,
    )
    config.set("data_root", str(tmp_path / "meetings"))
    return config


@dataclass
class ApiHarness:
    services: Services
    app: Any
    clock: FakeClock
    captures: dict[str, Any] = None  # type: ignore[assignment]

    def emit(self, seconds: float) -> None:
        """Produce ``seconds`` of device audio, the way a callback thread would."""
        for capture in self.captures.values():
            blocks = int(seconds * capture.format.rate / capture.block_frames)
            for _ in range(blocks):
                capture.emit()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.services.config.server_port}"

    def client(self, *, authorized: bool = True) -> Any:
        from fastapi.testclient import TestClient

        client = TestClient(self.app, base_url=self.base_url, raise_server_exceptions=False)
        if authorized:
            token = self.services.auth.issue_token()
            response = client.get(f"/?k={token}")
            assert response.status_code == 200, response.text
            client.headers[CSRF_HEADER] = self.services.auth.csrf_secret
        return client


@contextmanager
def serve(harness: ApiHarness) -> Any:
    """Run the app on a real socket — the only way to read an unbounded SSE stream."""
    import httpx

    from app.server import LocalServer

    port = harness.services.config.server_port
    server = LocalServer(harness.app, host="127.0.0.1", port=port).start()
    base = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(base_url=base, timeout=10.0) as client:
            token = harness.services.auth.issue_token()
            response = client.get("/", params={"k": token})
            assert response.status_code == 200, response.text
            client.headers[CSRF_HEADER] = harness.services.auth.csrf_secret
            yield client
    finally:
        server.stop()


def build_harness(tmp_path: Path, **overrides: Any) -> ApiHarness:
    from app.audio.fake import SyntheticCapture
    from app.audio.recorder import Recorder
    from app.main import create_app

    config = build_config(tmp_path, **overrides)
    config.set("server.port", free_port())
    clock = FakeClock()
    conn = connect(tmp_path / "index.db")
    dao = Dao(conn, clock)
    dao.seed_ids(3)
    queue = JobQueue(conn, clock, random.Random(4))
    events = EventBus()
    services = Services(
        config=config,
        conn=conn,
        dao=dao,
        queue=queue,
        meetings=MeetingService(config, dao, queue, clock=clock),
        events=events,
        auth=__import__("app.api.security", fromlist=["AuthState"]).AuthState(),
        clock=clock,
        mailer=Mailer(config),
        notifier=FakeNotifier(clock=clock),
    )
    captures: dict[str, Any] = {}

    def make(track: str) -> SyntheticCapture:
        # generate_on_read=False so the recorder's writer thread blocks on an empty
        # queue instead of generating audio forever under a FakeClock.
        capture = SyntheticCapture(track, "tone", queue_seconds=30.0, generate_on_read=False)
        captures[track] = capture
        return capture

    services.recorder = Recorder(config, make, clock=clock)
    services.worker = Worker(
        dao=dao,
        queue=queue,
        config=config,
        stages=registry(),
        clock=clock,
        recorder=services.recorder,
        services=services,
    )
    return ApiHarness(services=services, app=create_app(services), clock=clock, captures=captures)
