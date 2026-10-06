"""Install a verified update at a safe moment, like Windows Update (D87, B3).

``UpdateService`` keeps the next installer ready; this decides when to run it.

**A safe moment** is one in which nothing the user cares about can be cut short: no
recording, no call the detector is following, no transcription or summary running or due,
and no calendar meeting starting within ``SAFE_AHEAD_S``.

**When:**

- Automatically (``updates.auto_install``, the default): once the moment has stayed safe
  for ``QUIET_S``, or when the user quits from the tray, whichever comes first. Never when
  the uninstaller or another installer asked the app to quit.
- A mandatory update (critical, or this copy is below ``min_version``) installs at the
  first safe moment whatever the setting.
- "Install now" (the user asked): any moment without a recording.

**How:** Setup runs detached with ``/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /UPDATE=1``
(packaging/installer.iss), and the app quits so the installer can replace it. A marker,
``<app home>/updates/installing.json``, says what was being installed and whether the
window was open; the next start reads it to tell success from failure, to reopen the
window, and to stop retrying a version that failed twice.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.clock import Clock, SystemClock
from app.config import Config
from app.log import get
from app.updates.service import UpdateService

log = get(__name__)

#: No calendar meeting may start within this long of an update.
SAFE_AHEAD_S = 20 * 60.0
#: An automatic install waits for the moment to stay safe this long.
QUIET_S = 10 * 60.0
#: How often the safe moment is looked for while an update is ready.
TICK_S = 60.0
#: A version whose install failed this many times is no longer installed by itself.
MAX_ATTEMPTS = 2
MARKER = "installing.json"
#: What can hold an update back, by the code ``why_not_now`` returns.
REASONS = {
    "recording": "a meeting is being recorded",
    "call": "a call is in progress",
    "jobs": "a meeting is being transcribed or summarized",
    "meeting": "a meeting starts soon",
}

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def spawn_detached(command: list[str]) -> None:  # pragma: no cover - starts a real process
    """Start the installer outside the app's process tree, so quitting cannot take it down."""
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        "cwd": str(Path(command[0]).parent),
    }
    try:
        subprocess.Popen(command, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, **kwargs)
    except OSError:
        # A job that does not allow breakaway refuses the flag; detached is still enough.
        subprocess.Popen(command, creationflags=flags, **kwargs)


class UpdateInstaller:
    def __init__(
        self,
        config: Config,
        updates: UpdateService,
        *,
        recording: Callable[[], bool] = lambda: False,
        in_call: Callable[[], bool] = lambda: False,
        jobs_busy: Callable[[], bool] = lambda: False,
        meeting_soon: Callable[[], bool] = lambda: False,
        window_open: Callable[[], bool] = lambda: False,
        clock: Clock | None = None,
        spawn: Callable[[list[str]], None] = spawn_detached,
        can_install: bool | None = None,
    ) -> None:
        self.config = config
        self.updates = updates
        self.recording = recording
        self.in_call = in_call
        self.jobs_busy = jobs_busy
        self.meeting_soon = meeting_soon
        self.window_open = window_open
        self.clock = clock or SystemClock()
        self.spawn = spawn
        # Only an installed Windows copy has an installer to run.
        self.can_install = (
            (sys.platform == "win32" and updates.frozen) if can_install is None else can_install
        )
        #: Called after the installer starts, to quit the app (the tray sets it).
        self.quit: Callable[[], None] | None = None
        #: What the start after an update found (``after_start``), for the tray and toast.
        self.outcome: dict[str, Any] | None = None
        self._safe_since: Any = None
        self._installing = False
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ the moment

    def why_not_now(self, *, user_asked: bool = False) -> str | None:
        """None when this is a safe moment; otherwise what is in the way, as a code from
        ``REASONS`` (the interface says it in the user's language)."""
        if self.recording():
            return "recording"
        if user_asked:
            return None
        if self.in_call():
            return "call"
        if self.jobs_busy():
            return "jobs"
        if self.meeting_soon():
            return "meeting"
        return None

    def ready(self) -> dict[str, Any] | None:
        """The verified installer, unless its version has failed too often to retry alone."""
        ready = self.updates.ready()
        if ready is None:
            return None
        if self._attempts(ready["version"]) >= MAX_ATTEMPTS:
            return None
        return ready

    def tick(self) -> bool:
        """Install if the moment is right. True when the installer was started."""
        ready = self.ready()
        if ready is None or not self.can_install:
            self._safe_since = None
            return False
        mandatory = bool(ready.get("mandatory"))
        if not mandatory and not self.config.get("updates.auto_install", True):
            return False
        reason = self.why_not_now()
        now = self.clock.now()
        if reason is not None:
            if self._safe_since is not None:
                log.info("update %s waits: %s", ready["version"], REASONS[reason])
            self._safe_since = None
            return False
        if self._safe_since is None:
            self._safe_since = now
        quiet_for = (now - self._safe_since).total_seconds()
        quiet_s = float(self.config.get("updates.quiet_minutes") or QUIET_S / 60) * 60
        if not mandatory and quiet_for < quiet_s:
            return False
        return self.install(ready, why="mandatory" if mandatory else "idle")

    def at_quit(self) -> bool:
        """The user quit from the tray: a good moment for an automatic update."""
        ready = self.ready()
        if ready is None or not self.can_install:
            return False
        if not ready.get("mandatory") and not self.config.get("updates.auto_install", True):
            return False
        if self.why_not_now() is not None:
            return False
        return self.install(ready, why="quit", quit_after=False)

    def install_now(self) -> dict[str, Any]:
        """For "Install now" and "Restart to update": the user asked, so only a recording waits."""
        ready = self.updates.ready()
        if ready is None:
            raise LookupError("no update is ready to install")
        if not self.can_install:
            raise RuntimeError("this copy cannot install updates (run from source)")
        reason = self.why_not_now(user_asked=True)
        if reason is not None:
            raise RuntimeError(f"not now: {REASONS[reason]}")
        self.install(ready, why="asked")
        return {"installing": ready["version"]}

    # ------------------------------------------------------------------ installing

    def install(self, ready: dict[str, Any], *, why: str, quit_after: bool = True) -> bool:
        with self._lock:
            if self._installing:
                return False
            self._installing = True
        version = str(ready["version"])
        home = self.updates.home
        marker = self._read_marker()
        attempts = marker.get("attempts", 0) if marker.get("to") == version else 0
        self._write_marker(
            {
                "from": self.updates.current_version,
                "to": version,
                "at": self.clock.now().isoformat(timespec="seconds"),
                "why": why,
                "window_open": bool(self.window_open()),
                "attempts": attempts + 1,
            }
        )
        command = [
            str(ready["path"]),
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/UPDATE=1",
            f"/LOG={home / f'install-{version}.log'}",
        ]
        log.info("installing update %s (%s)", version, why)
        try:
            self.spawn(command)
        except OSError as exc:
            log.warning("the update installer did not start: %s", exc)
            self.updates.last_error = f"install: {exc}"
            with self._lock:
                self._installing = False
            return False
        if quit_after and self.quit is not None:
            self.quit()
        return True

    # ------------------------------------------------------------------ the start after

    def after_start(self) -> dict[str, Any] | None:
        """Read the marker an install left: did it work? Clean up, and say what happened."""
        marker = self._read_marker()
        if not marker:
            return None
        current = self.updates.current_version
        if current == marker.get("to"):
            outcome = {"result": "updated", **marker}
            self.config.set("updates.last_installed", current)
            self.config.save()
            self._marker_path().unlink(missing_ok=True)
            self.updates.forget_ready()
            log.info("updated from %s to %s", marker.get("from"), current)
        else:
            outcome = {"result": "failed", **marker}
            self.updates.last_error = (
                f"the update to {marker.get('to')} did not install "
                f"(attempt {marker.get('attempts', 1)} of {MAX_ATTEMPTS})"
            )
            log.warning("%s; still on %s", self.updates.last_error, current)
        self.outcome = outcome
        return outcome

    def _attempts(self, version: str) -> int:
        marker = self._read_marker()
        return int(marker.get("attempts", 0)) if marker.get("to") == version else 0

    def _marker_path(self) -> Path:
        return self.updates.home / MARKER

    def _read_marker(self) -> dict[str, Any]:
        try:
            data = json.loads(self._marker_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_marker(self, data: dict[str, Any]) -> None:
        self.updates.home.mkdir(parents=True, exist_ok=True)
        self._marker_path().write_text(json.dumps(data), encoding="utf-8")

    # ------------------------------------------------------------------ the loop

    def start(self) -> None:
        if self._thread is not None or not self.can_install:
            return
        self._thread = threading.Thread(target=self._run, name="update-install", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(TICK_S):
            try:
                if self.tick():
                    return
            except Exception:  # the loop must survive anything one tick does
                log.exception("update install check failed")


def meeting_soon(calendar: Any, clock: Clock, ahead_s: float = SAFE_AHEAD_S) -> bool:
    """A calendar meeting is on now or starts within ``ahead_s``. Advisory: never raises."""
    if calendar is None:
        return False
    try:
        return bool(calendar.soon(clock.now(), ahead_s=ahead_s))
    except Exception:
        log.exception("calendar lookup for the update failed")
        return False
