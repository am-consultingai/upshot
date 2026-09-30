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


#: A button that closes the toast and does nothing else (Windows' own Dismiss).
DISMISS = "system.dismiss"
#: A button that opens the meeting's link in the browser, and nothing else.
JOIN = "join"


@dataclass(frozen=True)
class Button:
    label: str
    action: str  # what the button does: an app/actions.py action, or DISMISS
    meeting_id: str | None = None
    #: The calendar event a "Start recording" is for, so the recording starts named.
    calendar_id: str | None = None
    event_id: str | None = None
    #: A meeting link: "Join" opens it; "Join and record" opens it and starts recording.
    join: str | None = None
    #: The connected account the calendar event is on (D82).
    account_id: str | None = None

    def link(self) -> str | None:
        """The link the button launches (app/actions.py); None for Dismiss."""
        if self.action == DISMISS:
            return None
        if self.action == JOIN:
            return self.join
        from app import actions

        return actions.url(
            self.action,
            meeting=self.meeting_id,
            account=self.account_id,
            calendar=self.calendar_id,
            event=self.event_id,
            join=self.join,
        )


@dataclass
class Toast:
    title: str
    body: str = ""
    buttons: tuple[Button, ...] = ()
    key: str = ""
    #: What clicking the notification itself does: show Upshot, at this meeting if any.
    meeting_id: str | None = None
    #: A meeting notice, not shown while Upshot's own window is in front: the bar at the
    #: top of the app says the same there, and a toast over it is noise (D70).
    quiet_in_front: bool = False

    def link(self) -> str:
        from app import actions

        if self.meeting_id:
            return actions.url("meeting.open", meeting=self.meeting_id)
        return actions.url("open")


class Notifier(Protocol):
    name: str

    def show(self, toast: Toast) -> None: ...


class BaseNotifier:
    """Shared debounce so a duplicate event within the window emits nothing."""

    name = "base"

    def __init__(
        self,
        *,
        debounce_s: float = DEBOUNCE_S,
        clock: Any = None,
        app_in_front: Callable[[], bool] | None = None,
    ) -> None:
        self.debounce_s = debounce_s
        self._last: dict[str, float] = {}
        from app.clock import SystemClock

        self.clock = clock or SystemClock()
        #: Whether Upshot's window is the one the user is looking at (app/window.py).
        self.app_in_front = app_in_front or (lambda: False)

    def _should_emit(self, toast: Toast) -> bool:
        if toast.quiet_in_front and self.app_in_front():
            log.info("toast %r not shown: Upshot's window is in front", toast.key)
            return False
        key = toast.key
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

    def withdraw(self, meeting_id: str) -> None:
        """Take back every notification about this meeting (it was deleted): a
        "Transcribing..." left on screen for a meeting that is gone misleads."""

    # -- the vocabulary the app uses

    def recording_started(self, meeting_id: str, title: str) -> None:
        self.show(
            Toast(
                title=f"Recording started: {title or 'meeting'}",
                body="Upshot is recording both sides of the call.",
                buttons=(
                    Button("Stop", "recording.stop", meeting_id),
                    Button("Not a meeting", "meeting.discard", meeting_id),
                ),
                key=f"started:{meeting_id}",
                meeting_id=meeting_id,
                quiet_in_front=True,
            )
        )

    def recording_ended(self, meeting_id: str, minutes: int, reason: str = "") -> None:
        why = {
            "the microphone was released": "You left the call, so Upshot stopped recording.",
            "both tracks were silent": "Nobody spoke for 5 minutes, so Upshot stopped recording.",
            "the maximum meeting duration was reached": "The recording reached its length limit.",
        }.get(reason, "")
        length = f"{minutes} min" if minutes >= 1 else "under a minute"
        self.show(
            Toast(
                title=f"Meeting ended — {length}. Transcribing…",
                body=why,
                buttons=(Button("Open", "meeting.open", meeting_id),),
                key=f"ended:{meeting_id}",
                meeting_id=meeting_id,
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
                meeting_id=meeting_id,
            )
        )

    def failed(self, meeting_id: str, stage: str) -> None:
        self.show(
            Toast(
                title=f"{stage.title()} failed — audio is safe",
                buttons=(Button("Open", "meeting.open", meeting_id),),
                key=f"failed:{meeting_id}:{stage}",
                meeting_id=meeting_id,
            )
        )

    def meeting_soon(
        self, key: str, title: str, *, minutes: int, conference_url: str | None = None
    ) -> None:
        """A calendar meeting starts in a few minutes (D76). Said once per meeting."""
        unit = "minute" if minutes == 1 else "minutes"
        self.show(
            Toast(
                title=f"{title or 'A meeting'} starts in {minutes} {unit}",
                body="Upshot will ask to record it when it starts.",
                buttons=(Button("Join", JOIN, join=conference_url),) if conference_url else (),
                key=f"soon:{key}",
            )
        )

    def meeting_starting(
        self,
        key: str,
        title: str,
        *,
        calendar_id: str | None = None,
        event_id: str | None = None,
        minutes_ago: int = 0,
        conference_url: str | None = None,
        account_id: str | None = None,
    ) -> None:
        """A calendar meeting's start time has come and nothing records it. Said once."""
        name = title or "A meeting"
        buttons: tuple[Button, ...] = self._start_buttons(calendar_id, event_id, account_id)
        if conference_url:
            buttons = (
                Button(
                    "Join and record",
                    "recording.start",
                    None,
                    calendar_id,
                    event_id,
                    conference_url,
                    account_id,
                ),
                *buttons,
            )
        self.show(
            Toast(
                title=f"{name} started {minutes_ago} min ago"
                if minutes_ago >= 2
                else f"{name} has started",
                body="Upshot isn't recording it.",
                buttons=buttons,
                key=f"starting:{key}",
                quiet_in_front=True,
            )
        )

    def call_ended(self, meeting_id: str, title: str, *, seconds: int) -> None:
        """The call's app let go: say so at once, with the countdown's two ways out (D77)."""
        self.show(
            Toast(
                title=f"{title} ended" if title else "The call ended",
                body=f"Upshot saves the recording in {seconds} seconds, unless you rejoin.",
                buttons=(
                    Button("Stop now", "recording.stop", meeting_id),
                    Button("Keep recording", "recording.keep", meeting_id),
                ),
                key=f"call-ended:{meeting_id}",
                meeting_id=meeting_id,
                quiet_in_front=True,
            )
        )

    def still_recording(self, meeting_id: str, title: str) -> None:
        """A recording runs past its meeting's scheduled end (D76): ask, never cut."""
        self.show(
            Toast(
                title=f"{title or 'The meeting'} was scheduled to end",
                body="Upshot is still recording. Stop now, or it stops when you leave the call.",
                buttons=(
                    Button("Stop recording", "recording.stop", meeting_id),
                    Button("Keep recording", DISMISS),
                ),
                key=f"overrun:{meeting_id}",
                meeting_id=meeting_id,
            )
        )

    def call_detected(
        self,
        process: str,
        title: str | None,
        *,
        calendar_id: str | None = None,
        event_id: str | None = None,
        account_id: str | None = None,
    ) -> None:
        """A call started and, in the default mode, nothing records it: say so (D64).

        "Detect and notify" is the default capture mode. Before this, a call noticed in
        that mode surfaced only inside Upshot's own window, which is exactly the window
        nobody has open when a call starts. Start recording works from the toast (D70).
        """
        app = process.removesuffix(".exe") or "an app"
        self.show(
            Toast(
                title=f"Meeting started: {title or app}",
                body="Upshot isn't recording it.",
                buttons=self._start_buttons(calendar_id, event_id, account_id),
                key=f"call:{process}",
                quiet_in_front=True,
            )
        )

    def could_not_start(self, reason: str) -> None:
        """A Start pressed on a notification failed: the only place to say so is another."""
        self.show(
            Toast(
                title="Upshot couldn't start recording",
                body=reason,
                key=f"could_not_start:{reason}",
            )
        )

    @staticmethod
    def _start_buttons(
        calendar_id: str | None, event_id: str | None, account_id: str | None = None
    ) -> tuple[Button, ...]:
        return (
            Button(
                "Start recording",
                "recording.start",
                None,
                calendar_id,
                event_id,
                account_id=account_id,
            ),
            Button("Dismiss", DISMISS),
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
        app_in_front: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__(debounce_s=debounce_s, clock=clock, app_in_front=app_in_front)
        self.shown = []
        self.activations = []
        self.withdrawn: list[str] = []
        self.on_action = on_action
        self.name = "fake"

    def show(self, toast: Toast) -> None:
        if not self._should_emit(toast):
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

    def withdraw(self, meeting_id: str) -> None:
        self.withdrawn.append(meeting_id)
        self.shown = [toast for toast in self.shown if toast_meeting(toast) != meeting_id]


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
        app_in_front: Callable[[], bool] | None = None,
    ) -> None:
        if app_in_front is None:
            from app.window import in_front

            app_in_front = in_front
        super().__init__(debounce_s=debounce_s, clock=clock, app_in_front=app_in_front)
        self.app_id = app_id
        self.spawn = spawn or self._popen

    def command(self, toast: Toast) -> list[str]:
        payload = json.dumps(
            {
                "app_id": self.app_id,
                # Only an installed app has its identity registered (the installer's
                # Start-menu shortcut); a toast under an unregistered one never appears.
                "aumid": self.app_id if getattr(sys, "frozen", False) else None,
                "title": toast.title,
                "body": toast.body,
                "launch": toast.link(),
                # Grouped by meeting, so deleting it can take its notifications back.
                "group": toast_group(toast_meeting(toast)),
                "buttons": [
                    {"label": button.label, "action": button.action, "launch": button.link()}
                    for button in toast.buttons
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
        if not self._should_emit(toast):
            return
        log.info(
            "notification: %r%s",
            toast.title,
            f" [{' | '.join(b.label for b in toast.buttons)}]" if toast.buttons else "",
        )
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

    def withdraw(self, meeting_id: str) -> None:
        group = toast_group(meeting_id)
        payload = json.dumps(
            {
                "remove_group": group,
                "app_id": self.app_id,
                "aumid": self.app_id if getattr(sys, "frozen", False) else None,
            }
        )
        command = (
            [sys.executable, "--toast", payload]
            if getattr(sys, "frozen", False)
            else [sys.executable, "-m", "app.notify_toast", payload]
        )
        try:
            process = self.spawn(command)
        except Exception as exc:
            log.warning("could not withdraw the notifications of %s: %s", meeting_id, exc)
            return
        log.info("notifications of %s withdrawn", meeting_id)
        if process is not None:
            threading.Thread(
                target=self._reap,
                args=(process, Toast(title="withdraw", key=f"withdraw:{meeting_id}")),
                name="toast",
                daemon=True,
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


def toast_meeting(toast: Toast) -> str | None:
    """The meeting a notification is about: its own, or its buttons'."""
    if toast.meeting_id:
        return toast.meeting_id
    return next((b.meeting_id for b in toast.buttons if b.meeting_id), None)


def toast_group(meeting_id: str | None) -> str | None:
    """Windows' group for a meeting's notifications: short, since a group is at most 64
    characters and a meeting id can be longer."""
    if not meeting_id:
        return None
    import hashlib

    return "m-" + hashlib.sha1(meeting_id.encode("utf-8")).hexdigest()[:20]


def make_notifier(config: Config, *, events: Any = None, clock: Any = None) -> BaseNotifier:
    kind = str(config.get("delivery.notifier", "windows"))
    if kind == "fake":
        return FakeNotifier(clock=clock)
    return WindowsToastNotifier(clock=clock)
