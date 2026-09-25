"""A CLI's sign-in run with no window: the app reads the link and the page shows it.

The alternative, still available per CLI (``codex_cli.SIGNIN_CONSOLE``), is a PowerShell
window of its own. For Codex that window was pure ceremony: ``codex login`` asks for
nothing on its console. It starts a callback server on ``localhost:1455``, prints the
authorization link, opens the default browser on it and waits. A user who never types in
the window still has to look at it, and one who closes it (the browser it picked was the
wrong one, 2026-09-25) has cancelled the sign-in without being told so.

Run hidden, the output goes to the same log file the window's transcript used, and the
link it prints is handed to the Settings page, which offers it in the browser the user is
already using. The process is the application's to stop: Cancel kills it, and so does a
second Sign in, since the old one still holds the callback port.
"""

from __future__ import annotations

import ctypes
import re
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import IO

from app.llm.claude_cli import creation_flags
from app.log import get

log = get(__name__)

#: How long Sign in waits for the link before answering the page without one. Codex
#: prints it within a second; the rest is a slow first start of the npm shim.
URL_WAIT_S = 15.0


def _job_that_dies_with_us() -> int | None:
    """A Windows job object whose processes are killed when this process ends.

    A sign-in outlives nothing it should: stopped with the app (Ctrl+C in the launcher,
    Stop-Process, a crash), an orphaned ``codex login`` would go on holding its callback
    port and make the next Sign in fail. The job's only handle is ours, so Windows closes
    it when this process ends, however it ends, and kills what is in it. Children the
    login starts join the job too. None off Windows or if the calls fail: the login then
    runs unguarded rather than not at all.
    """
    if sys.platform != "win32":
        return None
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined,unused-ignore]

    class Basic(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class Counters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ("r", "w", "o", "rt", "wt", "ot")]

    class Extended(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", Basic),
            ("IoInfo", Counters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = Extended()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = kernel32.SetInformationJobObject(
        wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info)
    )  # 9: JobObjectExtendedLimitInformation
    return int(job) if ok else None


def _join(job: int | None, process: subprocess.Popen[str]) -> None:
    if job is None or sys.platform != "win32":
        return
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined,unused-ignore]
    handle = getattr(process, "_handle", None)
    if handle is None or not kernel32.AssignProcessToJobObject(
        wintypes.HANDLE(job), wintypes.HANDLE(int(handle))
    ):
        log.warning("sign-in process %d runs outside the kill-on-exit job", process.pid)


#: One job for every sign-in this process starts; it lives as long as the process does.
_JOB: int | None = None
_JOB_MADE = False


def _job() -> int | None:
    global _JOB, _JOB_MADE
    if not _JOB_MADE:
        _JOB_MADE = True
        try:
            _JOB = _job_that_dies_with_us()
        except Exception:
            log.warning("could not create the sign-in job object", exc_info=True)
    return _JOB


class HiddenLogin:
    """One ``<cli> login`` running with no window, its output read as it arrives."""

    def __init__(
        self,
        argv: Sequence[str],
        *,
        url_pattern: re.Pattern[str],
        log_file: Path,
        cwd: str,
        env: dict[str, str],
    ) -> None:
        self.url_pattern = url_pattern
        self.log_file = log_file
        self.url: str | None = None
        self.lines: list[str] = []
        self._found = threading.Event()
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as out:
            out.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} hidden: {' '.join(argv)}\n")
        self.process = subprocess.Popen(
            list(argv),
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creation_flags(visible=False),
        )
        _join(_job(), self.process)
        assert self.process.stdout is not None
        threading.Thread(
            target=self._read, args=(self.process.stdout,), name="signin-reader", daemon=True
        ).start()

    def _read(self, stream: IO[str]) -> None:
        with self.log_file.open("a", encoding="utf-8") as out:
            for line in stream:
                out.write(line)
                out.flush()
                self.lines.append(line.rstrip())
                if self.url is None:
                    match = self.url_pattern.search(line)
                    if match:
                        self.url = match.group(0)
                        self._found.set()
            out.write(f"=== exited with {self.process.wait()}\n")
        self._found.set()  # an exit without a link must not leave the caller waiting

    def wait_for_url(self, timeout: float = URL_WAIT_S) -> str | None:
        self._found.wait(timeout)
        return self.url

    def running(self) -> bool:
        return self.process.poll() is None

    def stop(self) -> None:
        """End the login and everything it started.

        The npm install is a chain (``codex.cmd`` → ``node`` → ``codex.exe``): killing only
        the process we started would leave the one holding the callback port alive.
        """
        if not self.running():
            return
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(self.process.pid)],
                capture_output=True,
                creationflags=creation_flags(visible=False),
                check=False,
            )
        else:
            self.process.kill()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            log.warning("sign-in process %d did not exit after being stopped", self.process.pid)
