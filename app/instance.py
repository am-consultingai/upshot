"""Single instance: a named mutex on Windows, an exclusive lock file elsewhere.

A second launch exits 3 after opening the first one's page in the browser: the running
instance records its port in the app home for exactly this.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from app import paths
from app.log import get

log = get(__name__)

# Local\, not Global\: one instance per signed-in user. A Global\ name let one user's
# Upshot stop every other user's on the same PC, each with their own app home.
MUTEX_NAME = r"Local\upshot"
ALREADY_RUNNING = 3
PORT_FILE = "server.port"


def record_port(port: int, home: Path | None = None) -> Path:
    """Where a second launch finds the running instance (the port may be a fallback)."""
    target = (home or paths.app_home()) / PORT_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(port), encoding="utf-8")
    return target


def recorded_port(home: Path | None = None) -> int | None:
    try:
        port = int((home or paths.app_home()).joinpath(PORT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return port if 0 < port < 65536 else None


def show_running(home: Path | None = None, *, opener: Any = None) -> bool:
    """Open the running instance's page, as a double-click on the icon is asking for."""
    port = recorded_port(home)
    if port is None:
        return False
    if opener is None:
        import webbrowser

        opener = webbrowser.open
    opener(f"http://127.0.0.1:{port}/")
    return True


class SingleInstance:
    def __init__(self, name: str = MUTEX_NAME, lock_path: Path | None = None) -> None:
        self.name = name
        self.lock_path = lock_path or (paths.app_home() / "instance.lock")
        self._handle: Any = None
        self._file: Any = None
        self.acquired = False

    def acquire(self) -> bool:
        if sys.platform == "win32":
            self.acquired = self._acquire_mutex()
        else:
            self.acquired = self._acquire_lockfile()
        if not self.acquired:
            log.warning("already running — this instance is exiting")
        return self.acquired

    def _acquire_mutex(self) -> bool:  # pragma: no cover - Windows only
        import win32api
        import win32event
        import winerror

        self._handle = win32event.CreateMutex(None, True, self.name)
        return bool(win32api.GetLastError() != winerror.ERROR_ALREADY_EXISTS)

    def _acquire_lockfile(self) -> bool:
        import fcntl

        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.lock_path.open("a+")
        try:
            # On Windows this branch never runs and fcntl has no flock, which mypy
            # checking for that platform is right about; the ignore is itself unused on
            # POSIX, so both are named.
            fcntl.flock(  # type: ignore[attr-defined,unused-ignore]
                self._file.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,  # type: ignore[attr-defined,unused-ignore]
            )
        except OSError:
            self._file.close()
            self._file = None
            return False
        self._file.seek(0)
        self._file.truncate()
        self._file.write(str(os.getpid()))
        self._file.flush()
        return True

    def release(self) -> None:
        if self._handle is not None:  # pragma: no cover - Windows only
            import win32api

            win32api.CloseHandle(self._handle)
            self._handle = None
        if self._file is not None:
            import fcntl

            fcntl.flock(  # type: ignore[attr-defined,unused-ignore]
                self._file.fileno(),
                fcntl.LOCK_UN,  # type: ignore[attr-defined,unused-ignore]
            )
            self._file.close()
            self._file = None
        self.acquired = False

    def __enter__(self) -> SingleInstance:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def main(argv: list[str] | None = None) -> int:
    """``python -m app.instance hold`` — used by the single-instance test."""
    parser = argparse.ArgumentParser(prog="python -m app.instance")
    parser.add_argument("command", choices=["hold"])
    parser.add_argument("--seconds", type=float, default=30.0)
    args = parser.parse_args(argv)

    guard = SingleInstance()
    if not guard.acquire():
        print("already running", flush=True)
        return ALREADY_RUNNING
    print("acquired", flush=True)
    try:
        import time

        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            if sys.stdin.closed or sys.stdin.readline() == "":
                break
    except Exception:  # pragma: no cover - stdin closed abruptly
        pass
    finally:
        guard.release()
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    from app.instance import main as _main

    raise SystemExit(_main())
