"""Toasts (DETECTION.md §6). The buttons are the whole learning mechanism."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.config import Config
from app.log import get

log = get(__name__)

DEBOUNCE_S = 5.0


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
    """windows-toasts. Buttons post to the local API — the same path the UI uses."""

    name = "windows"

    def __init__(
        self,
        *,
        app_id: str = "MeetingAgent.App",
        on_action: Callable[[Button], None] | None = None,
        debounce_s: float = DEBOUNCE_S,
        clock: Any = None,
    ) -> None:
        super().__init__(debounce_s=debounce_s, clock=clock)
        self.app_id = app_id
        self.on_action = on_action
        self._toaster: Any = None

    def toaster(self) -> Any:
        if self._toaster is None:
            from windows_toasts import WindowsToaster

            self._toaster = WindowsToaster(self.app_id)
        return self._toaster

    def show(self, toast: Toast) -> None:
        if not self._should_emit(toast.key):
            return
        try:
            from windows_toasts import Toast as WinToast
            from windows_toasts import ToastButton

            payload = WinToast()
            payload.text_fields = [toast.title, toast.body]
            for button in toast.buttons:
                payload.AddAction(ToastButton(button.label, arguments=button.action))
            if self.on_action is not None:
                payload.on_activated = lambda event: self._activated(toast, event)
            self.toaster().show_toast(payload)
        except Exception as exc:
            log.warning("toast failed: %s", exc)

    def _activated(self, toast: Toast, event: Any) -> None:  # pragma: no cover - Windows only
        argument = str(getattr(event, "arguments", ""))
        for button in toast.buttons:
            if button.action == argument and self.on_action is not None:
                self.on_action(button)


def make_notifier(config: Config, *, events: Any = None, clock: Any = None) -> BaseNotifier:
    kind = str(config.get("delivery.notifier", "windows"))
    if kind == "fake":
        return FakeNotifier(clock=clock)
    return WindowsToastNotifier(clock=clock)
