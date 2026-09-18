from __future__ import annotations

import threading

import pytest

from app.clock import FakeClock
from app.config import default_config
from app.notify import Button, FakeNotifier, Toast, make_notifier
from app.tray_state import (
    Action,
    AppState,
    IconColor,
    RecorderState,
    icon_for,
    menu_for,
)

# ------------------------------------------------------------------ icon table


@pytest.mark.parametrize(
    ("state", "color", "tooltip_fragment", "badge"),
    [
        (AppState(), IconColor.GREY, "Idle", False),
        (AppState(recorder=RecorderState.ARMED), IconColor.AMBER, "Evaluating", False),
        (
            AppState(recorder=RecorderState.RECORDING, meeting_title="Weekly Sync"),
            IconColor.RED,
            "Recording — Weekly Sync",
            False,
        ),
        (AppState(recorder=RecorderState.PAUSED), IconColor.AMBER, "Paused", False),
        (AppState(processing=True, queue_depth=3), IconColor.BLUE, "Processing 3 jobs", False),
        (AppState(processing=True, queue_depth=1), IconColor.BLUE, "Processing 1 job", False),
        (AppState(error=True), IconColor.GREY, "attention", True),
        (AppState(worker_alive=False), IconColor.GREY, "worker stopped", True),
        (AppState(detector_muted=True), IconColor.GREY, "muted", False),
    ],
)
def test_icon_state_table(
    state: AppState, color: IconColor, tooltip_fragment: str, badge: bool
) -> None:
    spec = icon_for(state)
    assert spec.color is color
    assert tooltip_fragment in spec.tooltip
    assert spec.badge is badge
    assert len(spec.rgb) == 3


def test_recording_beats_everything() -> None:
    """The icon is never not red while capturing."""
    spec = icon_for(
        AppState(recorder=RecorderState.RECORDING, processing=True, error=True, queue_depth=9)
    )
    assert spec.color is IconColor.RED


def test_menu_items_enabled() -> None:
    idle = {item.action: item for item in menu_for(AppState())}
    assert idle[Action.START].enabled is True
    assert idle[Action.STOP].enabled is False
    assert idle[Action.PAUSE].enabled is False
    assert Action.MUTE_HOUR in idle, "the mute button is always present"

    recording = {item.action: item for item in menu_for(AppState(recorder=RecorderState.RECORDING))}
    assert recording[Action.START].enabled is False
    assert recording[Action.STOP].enabled is True
    assert recording[Action.PAUSE].enabled is True
    assert recording[Action.PAUSE].label == "Pause"

    paused = {item.action: item for item in menu_for(AppState(recorder=RecorderState.PAUSED))}
    assert paused[Action.PAUSE].label == "Resume"
    assert paused[Action.STOP].enabled is True

    muted = {item.action: item for item in menu_for(AppState(detector_muted=True))}
    assert muted[Action.MUTE_HOUR].checked is True


def test_every_state_produces_a_menu() -> None:
    for recorder in RecorderState:
        spec = icon_for(AppState(recorder=recorder))
        assert len(spec.menu) == 6
        assert spec.tooltip


# ------------------------------------------------------------------ toasts


def test_notifications_fire_once() -> None:
    clock = FakeClock()
    notifier = FakeNotifier(clock=clock, debounce_s=5.0)
    notifier.recording_started("m1", "Weekly Sync")
    notifier.recording_started("m1", "Weekly Sync")
    assert len(notifier.shown) == 1
    clock.advance(6)
    notifier.recording_started("m1", "Weekly Sync")
    assert len(notifier.shown) == 2
    notifier.recording_started("m2", "Other")
    assert len(notifier.shown) == 3, "a different meeting is a different toast"


def test_toast_buttons_present() -> None:
    notifier = FakeNotifier(clock=FakeClock())
    notifier.recording_started("m1", "Weekly Sync")
    started = notifier.shown[-1]
    assert [button.label for button in started.buttons] == ["Stop", "Not a meeting"]

    notifier.summary_ready("m1", "Weekly Sync")
    ready = notifier.shown[-1]
    assert [button.label for button in ready.buttons] == ["Open", "Email"]

    notifier.failed("m1", "transcribe")
    assert [button.label for button in notifier.shown[-1].buttons] == ["Retry", "Open"]
    assert "audio is safe" in notifier.shown[-1].title

    notifier.near_miss("Teams.exe", "14:03")
    assert [b.label for b in notifier.shown[-1].buttons] == ["It was a meeting"]


def test_notifier_is_selected_by_config() -> None:
    assert make_notifier(default_config(delivery__notifier="fake")).name == "fake"
    assert make_notifier(default_config()).name == "windows"


def test_toast_dataclasses() -> None:
    toast = Toast(title="x", buttons=(Button("Stop", "recording.stop", "m1"),))
    assert toast.buttons[0].meeting_id == "m1"
    assert toast.body == ""


# ------------------------------------------------- toasts run in their own process


class FakeProcess:
    """Enough of ``Popen`` for the reaper: it waits, reads stderr and checks the code."""

    def __init__(self, returncode: int = 0, errors: str = "", hang: bool = False) -> None:
        self.returncode = returncode
        self.errors = errors
        self.hang = hang
        self.killed = False

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        if self.hang:
            import subprocess

            raise subprocess.TimeoutExpired(cmd="toast", timeout=timeout or 0)
        return "", self.errors

    def kill(self) -> None:
        self.killed = True


def _windows_notifier(process: FakeProcess | None, seen: list[list[str]]):  # type: ignore[no-untyped-def]
    from app.notify import WindowsToastNotifier

    def spawn(command: list[str]) -> FakeProcess | None:
        seen.append(command)
        return process

    return WindowsToastNotifier(clock=FakeClock(), spawn=spawn)


def test_a_toast_is_handed_to_another_process() -> None:
    """Nothing in the server process touches WinRT: using it from a request thread is
    what killed the application mid-Stop on 2026-09-18, after the meeting was filed."""
    import json
    import sys

    seen: list[list[str]] = []
    notifier = _windows_notifier(FakeProcess(), seen)
    notifier.recording_ended("m1", 12)

    assert len(seen) == 1
    command = seen[0]
    assert command[:3] == [sys.executable, "-m", "app.notify_toast"]
    payload = json.loads(command[3])
    assert payload["title"].startswith("Meeting ended")
    assert [button["label"] for button in payload["buttons"]] == ["Open"]


def test_a_toast_that_cannot_start_never_reaches_the_caller() -> None:
    from app.notify import WindowsToastNotifier

    def spawn(command: list[str]) -> None:
        raise OSError("no interpreter here")

    notifier = WindowsToastNotifier(clock=FakeClock(), spawn=spawn)
    notifier.recording_ended("m1", 12)  # the recording is filed; the toast is not its problem


def test_a_failing_toast_process_is_reported_not_raised(caplog) -> None:  # type: ignore[no-untyped-def]
    import logging

    seen: list[list[str]] = []
    notifier = _windows_notifier(FakeProcess(returncode=1, errors="toast failed: boom"), seen)
    with caplog.at_level(logging.WARNING):
        notifier.recording_ended("m1", 12)
        for thread in threading.enumerate():
            if thread.name == "toast":
                thread.join(5)
    assert "toast failed: boom" in caplog.text


def test_a_hung_toast_process_is_killed() -> None:
    process = FakeProcess(hang=True)
    notifier = _windows_notifier(process, [])
    notifier.recording_ended("m1", 12)
    for thread in threading.enumerate():
        if thread.name == "toast":
            thread.join(5)
    assert process.killed


def test_the_frozen_build_shows_a_toast_through_its_own_flag(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import sys

    from app.notify import Toast as AppToast
    from app.notify import WindowsToastNotifier

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    command = WindowsToastNotifier(clock=FakeClock()).command(AppToast(title="x"))
    assert command[:2] == [sys.executable, "--toast"]


def test_the_toast_process_falls_back_to_a_toast_without_buttons(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """An interactable toaster needs a registered AUMID and is not available everywhere."""
    import json
    import sys
    import types

    from app import notify_toast

    built: list[str] = []

    class FakeToast:
        def __init__(self) -> None:
            self.text_fields: list[str] = []

        def AddAction(self, button: object) -> None:
            built.append("action")

    class Toaster:
        kind = "plain"

        def __init__(self, app_id: str) -> None:
            built.append(self.kind)

        def show_toast(self, toast: FakeToast) -> None:
            if self.kind == "interactable":
                raise RuntimeError("no AUMID")
            built.append("shown")

    class Interactable(Toaster):
        kind = "interactable"

    module = types.ModuleType("windows_toasts")
    module.WindowsToaster = Toaster  # type: ignore[attr-defined]
    module.InteractableWindowsToaster = Interactable  # type: ignore[attr-defined]
    module.Toast = FakeToast  # type: ignore[attr-defined]
    module.ToastButton = lambda label, arguments: (label, arguments)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "windows_toasts", module)
    monkeypatch.setattr(notify_toast, "SETTLE_S", 0)

    payload = json.dumps({"title": "Meeting ended", "buttons": [{"label": "Open", "action": "o"}]})
    assert notify_toast.main([payload]) == 0
    assert built == ["interactable", "action", "plain", "shown"]
    assert notify_toast.main([]) == 2
    assert notify_toast.main(["not json"]) == 2
