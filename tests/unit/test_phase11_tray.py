from __future__ import annotations

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
