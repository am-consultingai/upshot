"""uvicorn in a thread, bound explicitly to 127.0.0.1 — never 0.0.0.0."""

from __future__ import annotations

import threading
import time
from typing import Any

from app.log import get

log = get(__name__)


class LocalServer:
    def __init__(self, app: Any, *, host: str = "127.0.0.1", port: int = 8000) -> None:
        import uvicorn

        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError(f"refusing to bind to {host!r}: the UI is local-only")
        self.config = uvicorn.Config(
            app, host=host, port=port, log_level="warning", access_log=False, lifespan="on"
        )
        self.server = uvicorn.Server(self.config)
        self._thread: threading.Thread | None = None

    @property
    def sockets(self) -> list[Any]:
        servers = getattr(self.server, "servers", []) or []
        return [socket for server in servers for socket in server.sockets]

    @property
    def bound_port(self) -> int:
        for socket in self.sockets:
            return int(socket.getsockname()[1])
        return int(self.config.port)

    def run(self) -> None:  # pragma: no cover - blocks
        self.server.run()

    def start(self, timeout: float = 10.0) -> LocalServer:
        thread = threading.Thread(target=self.server.run, name="http", daemon=True)
        thread.start()
        self._thread = thread
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.server.started and self.sockets:
                return self
            time.sleep(0.02)
        raise TimeoutError("the local server did not start")

    def stop(self, timeout: float = 5.0) -> None:
        self.server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None
