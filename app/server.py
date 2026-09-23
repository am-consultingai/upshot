"""uvicorn in a thread, bound explicitly to 127.0.0.1 — never 0.0.0.0."""

from __future__ import annotations

import threading
import time
from typing import Any

from app.log import get

log = get(__name__)

# Long enough for a normal request to finish, short enough that a held-open SSE stream
# cannot keep the process alive.
GRACEFUL_SHUTDOWN_S = 3

#: Tried in order when the configured port is taken: the range the developer launcher
#: has always fallen back to.
FALLBACK_PORTS = range(8010, 8041)


def port_is_free(host: str, port: int) -> bool:
    import socket
    import sys

    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        if sys.platform != "win32":
            # As uvicorn binds on POSIX, so a port our own last run left in TIME_WAIT
            # reads as free. On Windows the same flag would mean "share a bound port".
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def choose_port(host: str, preferred: int) -> int:
    """The configured port if it is free, else the first free one in the fallback range.

    Chosen before the app is built, because the auth link, the host-header check and the
    tray's Open all read the port from the config. uvicorn on a taken port raises
    SystemExit inside its thread, where nobody sees it: the windowed build just vanished.
    """
    candidates: list[int] = [preferred, *FALLBACK_PORTS]
    for port in candidates:
        if port_is_free(host, port):
            if port != preferred:
                log.warning("port %d is in use; using %d", preferred, port)
            return port
    last = FALLBACK_PORTS[-1]
    raise OSError(f"no free port: {preferred} and {FALLBACK_PORTS[0]}-{last} are all in use")


class LocalServer:
    def __init__(self, app: Any, *, host: str = "127.0.0.1", port: int = 8000) -> None:
        import uvicorn

        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError(f"refusing to bind to {host!r}: the UI is local-only")
        self.config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="warning",
            access_log=False,
            lifespan="on",
            # uvicorn otherwise installs its own dictConfig with propagate=False, so its
            # loggers — including the ASGI exception tracebacks — go to the console and
            # never reach app.log. None leaves logging alone: its records propagate to the
            # root logger and land in the file with everything else.
            log_config=None,
            # Without this, shutdown waits for in-flight responses to finish — and an SSE
            # stream never finishes. The observed result is a process that releases the
            # port on SIGTERM and then lives forever, which is how a stale instance came
            # to shadow a later one for two days.
            timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_S,
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
