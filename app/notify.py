"""Toasts (DETECTION.md §6). The buttons are the whole learning mechanism."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.config import Config
from app.log import get

log = get(__name__)

DEBOUNCE_S = 5.0
#: A toast that has not appeared in this long is never going to.
TOAST_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class Button:
    label: str
    action: str  # the API call the button performs
    meeting_id: str | None = None


@dataclass
class Toast:
    title: str
    body: str = ""
    buttons: tuple[Button, ...] = ()
    key: str = ""


class Notifier(Protocol):
    name: str

    def show(self, toast: Toast) -> None: ...


class BaseNotifier:
    """Shared debounce so a duplicate event within the window emits nothing."""

    name = "base"

    def __init__(self, *, debounce_s: float = DEBOUNCE_S, clock: Any = None) -> None:
        self.debounce_s = debounce_s
        self._last: dict[str, float] = {}
        from app.clock import SystemClock

        self.clock = clock or SystemClock()

    def _should_emit(self, key: str) -> bool:
        if not key:
            return True
        now = self.clock.monotonic()
        previous = self._last.get(key)
        if previous is not None and now - previous < self.debounce_s:
            return False
        self._last[key] = now
        return True

    def show(self, toast: Toast) -> None:
        raise NotImplementedError

    # -- the vocabulary the app uses

    def recording_started(self, meeting_id: str, title: str) -> None:
        self.show(
            Toast(
                title=f"Recording — {title or 'meeting'}",
                body="Both tracks are being captured.",
                buttons=(
                    Button("Stop", "recording.stop", meeting_id),
                    Button("Not a meeting", "meeting.discard", meeting_id),
                ),
                key=f"started:{meeting_id}",
            )
        )

    def recording_ended(self, meeting_id: str, minutes: int) -> None:
        self.show(
            Toast(
                title=f"Meeting ended — {minutes} min. Transcribing…",
                buttons=(Button("Open", "meeting.open", meeting_id),),
                key=f"ended:{meeting_id}",
            )
        )

    def summary_ready(self, meeting_id: str, title: str) -> None:
        self.show(
            Toast(
                title=f"Summary ready — {title or 'meeting'}",
                buttons=(
                    Button("Open", "meeting.open", meeting_id),
                    Button("Email", "meeting.email", meeting_id),
                ),
                key=f"summary:{meeting_id}",
            )
        )

    def failed(self, meeting_id: str, stage: str) -> None:
        self.show(
            Toast(
                title=f"{stage.title()} failed — audio is safe",
                buttons=(
                    Button("Retry", "meeting.retry", meeting_id),
                    Button("Open", "meeting.open", meeting_id),
                ),
                key=f"failed:{meeting_id}:{stage}",
            )
        )

    def near_miss(self, process: str, when: str) -> None:
        self.show(
            Toast(
                title=f"Didn't record {process} at {when}",
                buttons=(Button("It was a meeting", "detector.promote"),),
                key=f"near_miss:{process}:{when}",
            )
        )


@dataclass
class FakeNotifier(BaseNotifier):
    """Records toasts and can activate a button, which posts to the local API."""

    shown: list[Toast] = field(default_factory=list)
    activations: list[Button] = field(default_factory=list)
    on_action: Callable[[Button], None] | None = None
    name: str = "fake"

    def __init__(
        self,
        *,
        debounce_s: float = DEBOUNCE_S,
        clock: Any = None,
        on_action: Callable[[Button], None] | None = None,
    ) -> None:
        super().__init__(debounce_s=debounce_s, clock=clock)
        self.shown = []
        self.activations = []
        self.on_action = on_action
        self.name = "fake"

    def show(self, toast: Toast) -> None:
        if not self._should_emit(toast.key):
            return
        self.shown.append(toast)

    def activate(self, label: str) -> Button:
        """Simulate a user pressing a toast button."""
        for toast in reversed(self.shown):
            for button in toast.buttons:
                if button.label == label:
                    self.activations.append(button)
                    if self.on_action is not None:
                        self.on_action(button)
                    return button
        raise KeyError(f"no toast button labelled {label!r}")

    def titles(self) -> Sequence[str]:
        return [toast.title for toast in self.shown]


class WindowsToastNotifier(BaseNotifier):
    """windows-toasts, in a process of its own — see ``app/notify_toast.py`` for why.

    Nothing here imports WinRT. The notification is handed to a child process and this
    one goes straight back to what it was doing, so a toast can neither block the caller
    nor, as it did on 2026-09-18, take the application down with it when it faults.
    """

    name = "windows"

    def __init__(
        self,
        *,
        app_id: str = "Upshot.App",
        debounce_s: float = DEBOUNCE_S,
        clock: Any = None,
        spawn: Callable[[list[str]], Any] | None = None,
    ) -> None:
        super().__init__(debounce_s=debounce_s, clock=clock)
        self.app_id = app_id
        self.spawn = spawn or self._popen

    def command(self, toast: Toast) -> list[str]:
        payload = json.dumps(
            {
                "app_id": self.app_id,
                "title": toast.title,
                "body": toast.body,
                "buttons": [
                    {"label": button.label, "action": button.action} for button in toast.buttons
                ],
            }
        )
        if getattr(sys, "frozen", False):
            # In a freeze there is no interpreter to hand a module to: the executable
            # itself grows a flag, the way --selftest already works.
            return [sys.executable, "--toast", payload]
        return [sys.executable, "-m", "app.notify_toast", payload]

    def _popen(self, command: list[str]) -> Any:
        # PYTHONPATH rather than cwd: `-m app.notify_toast` has to resolve wherever the
        # launcher happened to leave the working directory, and on Windows the source
        # tree is reached over a UNC path that cannot be one.
        import os
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        environment = dict(os.environ)
        existing = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = f"{root}{os.pathsep}{existing}" if existing else str(root)
        return subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def show(self, toast: Toast) -> None:
        if not self._should_emit(toast.key):
            return
        try:
            process = self.spawn(self.command(toast))
        except Exception as exc:  # a missing interpreter must not fail the recording
            log.warning("could not start the toast process: %s", exc)
            return
        if process is None:
            return
        # Reaped on a thread of its own: the caller has a meeting to file, and a toast
        # nobody waits for is still worth reporting when it fails.
        threading.Thread(
            target=self._reap, args=(process, toast), name="toast", daemon=True
        ).start()

    def _reap(self, process: Any, toast: Toast) -> None:
        try:
            _, errors = process.communicate(timeout=TOAST_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            process.kill()
            log.warning("toast %r timed out", toast.key)
            return
        except Exception as exc:
            log.warning("toast %r could not be reaped: %s", toast.key, exc)
            return
        code = process.returncode
        if code != 0:
            log.warning("toast %r failed (exit %s): %s", toast.key, code, (errors or "").strip())
        elif errors:
            # The child fell back to a toast without buttons and said so.
            log.warning("toast %r: %s", toast.key, errors.strip())


def make_notifier(config: Config, *, events: Any = None, clock: Any = None) -> BaseNotifier:
    kind = str(config.get("delivery.notifier", "windows"))
    if kind == "fake":
        return FakeNotifier(clock=clock)
    return WindowsToastNotifier(clock=clock)
