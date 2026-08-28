"""The icon is dumb: every decision lives here, in a pure function.

This module imports nothing from pystray, which is what makes the tray testable
without a desktop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class RecorderState(StrEnum):
    IDLE = "idle"
    ARMED = "armed"  # Tier 1: streams open, pre-roll filling, nothing on disk
    RECORDING = "recording"
    PAUSED = "paused"


class IconColor(StrEnum):
    GREY = "grey"
    AMBER = "amber"
    RED = "red"
    BLUE = "blue"


COLORS: dict[IconColor, tuple[int, int, int]] = {
    IconColor.GREY: (150, 150, 155),
    IconColor.AMBER: (230, 160, 30),
    IconColor.RED: (215, 45, 45),
    IconColor.BLUE: (50, 110, 220),
}


class Action(StrEnum):
    START = "recording.start"
    STOP = "recording.stop"
    PAUSE = "recording.pause"
    OPEN = "dashboard.open"
    MUTE_HOUR = "detector.mute_1h"
    QUIT = "app.quit"


@dataclass(frozen=True)
class AppState:
    recorder: RecorderState = RecorderState.IDLE
    processing: bool = False
    error: bool = False
    queue_depth: int = 0
    meeting_title: str | None = None
    detector_mode: str = "shadow"
    detector_muted: bool = False
    worker_alive: bool = True


@dataclass(frozen=True)
class MenuItem:
    label: str
    action: Action
    enabled: bool = True
    checked: bool | None = None


@dataclass(frozen=True)
class IconSpec:
    color: IconColor
    tooltip: str
    badge: bool = False
    menu: tuple[MenuItem, ...] = field(default_factory=tuple)

    @property
    def rgb(self) -> tuple[int, int, int]:
        return COLORS[self.color]


def _color(state: AppState) -> IconColor:
    """Recording always wins: the icon is never not red while capturing."""
    if state.recorder is RecorderState.RECORDING:
        return IconColor.RED
    if state.recorder is RecorderState.PAUSED:
        return IconColor.AMBER
    if state.recorder is RecorderState.ARMED:
        return IconColor.AMBER
    if state.processing:
        return IconColor.BLUE
    return IconColor.GREY


def _tooltip(state: AppState) -> str:
    if state.recorder is RecorderState.RECORDING:
        return f"Recording — {state.meeting_title}" if state.meeting_title else "Recording"
    if state.recorder is RecorderState.PAUSED:
        return "Paused — nothing is being written to disk"
    if state.recorder is RecorderState.ARMED:
        return "Evaluating — nothing on disk yet"
    if state.processing:
        depth = state.queue_depth
        return f"Processing {depth} job{'s' if depth != 1 else ''}"
    if not state.worker_alive:
        return "The worker stopped — recording still works"
    if state.error:
        return "Something needs attention"
    if state.detector_muted:
        return "Idle — detection muted"
    return "Idle"


def menu_for(state: AppState) -> tuple[MenuItem, ...]:
    recording = state.recorder is RecorderState.RECORDING
    paused = state.recorder is RecorderState.PAUSED
    active = recording or paused
    return (
        MenuItem("Start recording", Action.START, enabled=not active),
        MenuItem("Stop recording", Action.STOP, enabled=active),
        MenuItem("Resume" if paused else "Pause", Action.PAUSE, enabled=active),
        MenuItem("Open dashboard", Action.OPEN, enabled=True),
        MenuItem(
            "Don't detect for 1 hour",
            Action.MUTE_HOUR,
            enabled=True,
            checked=state.detector_muted,
        ),
        MenuItem("Quit", Action.QUIT, enabled=True),
    )


def icon_for(state: AppState) -> IconSpec:
    """(AppState) -> IconSpec. The whole tray behaviour, as one pure function."""
    return IconSpec(
        color=_color(state),
        tooltip=_tooltip(state),
        badge=state.error or not state.worker_alive,
        menu=menu_for(state),
    )
