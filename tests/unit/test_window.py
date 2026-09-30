"""Upshot's window on Windows, against stand-in win32 modules (the real ones are
Windows-only). The case that matters: a start right after a quit (machine B,
2026-09-30), when the old window is still closing."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from app import window


class FakeDesktop:
    """Top-level windows by handle. A closed window lingers for ``linger`` IsWindow
    checks, as a real one does while its process tears down."""

    def __init__(self, *, linger: int = 3) -> None:
        self.windows: dict[int, dict[str, Any]] = {}
        self.linger = linger
        self.foreground: list[int] = []
        self.refuse_foreground: set[int] = set()

    def add(self, hwnd: int, title: str = window.WINDOW_TITLE) -> None:
        self.windows[hwnd] = {"title": title, "closing": None}

    # -- win32gui
    def EnumWindows(self, visit: Any, extra: Any) -> None:
        for hwnd in list(self.windows):
            visit(hwnd, extra)

    def GetClassName(self, hwnd: int) -> str:
        return window.CHROMIUM_CLASS

    def GetWindowText(self, hwnd: int) -> str:
        return str(self.windows[hwnd]["title"]) if hwnd in self.windows else ""

    def IsWindowVisible(self, hwnd: int) -> bool:
        return hwnd in self.windows

    def IsIconic(self, hwnd: int) -> bool:
        return False

    def ShowWindow(self, hwnd: int, how: int) -> None:
        pass

    def IsWindow(self, hwnd: int) -> bool:
        entry = self.windows.get(hwnd)
        if entry is None:
            return False
        if entry["closing"] is not None:
            entry["closing"] -= 1
            if entry["closing"] <= 0:
                del self.windows[hwnd]
                return False
        return True

    def PostMessage(self, hwnd: int, message: int, *args: Any) -> None:
        self.windows[hwnd]["closing"] = self.linger

    def SetForegroundWindow(self, hwnd: int) -> None:
        if hwnd in self.refuse_foreground or hwnd not in self.windows:
            self.windows.pop(hwnd, None)
            raise RuntimeError("(1400, 'SetForegroundWindow', 'Invalid window handle.')")
        self.foreground.append(hwnd)


@pytest.fixture
def desktop(monkeypatch: pytest.MonkeyPatch) -> FakeDesktop:
    fake = FakeDesktop()
    gui = types.ModuleType("win32gui")
    for name in dir(FakeDesktop):
        if name[0].isupper():
            setattr(gui, name, getattr(fake, name))
    con = types.ModuleType("win32con")
    con.WM_CLOSE = 0x10  # type: ignore[attr-defined]
    con.SW_RESTORE = 9  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "win32gui", gui)
    monkeypatch.setitem(sys.modules, "win32con", con)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(window, "_closing", set())
    monkeypatch.setattr(window.time, "sleep", lambda s: None)
    return fake


def test_close_all_waits_until_the_old_window_is_gone(desktop: FakeDesktop) -> None:
    desktop.add(101)
    desktop.add(202, title="Something else")
    assert window.close_all() == 1
    assert 101 not in desktop.windows, "returned before the window was gone"
    assert 202 in desktop.windows, "only Upshot's windows are closed"


def test_a_window_still_closing_is_not_focused_instead_of_opening_one(
    desktop: FakeDesktop,
) -> None:
    """Even if it outlives the wait, a window asked to close is never the one shown."""
    desktop.linger = 10_000
    desktop.add(101)
    window.close_all(wait_s=0.0)
    assert window.focus_existing() is False
    assert desktop.foreground == []


def test_a_window_that_vanishes_while_brought_forward_means_open_a_new_one(
    desktop: FakeDesktop,
) -> None:
    desktop.add(101)
    desktop.refuse_foreground.add(101)
    assert window.focus_existing() is False


def test_a_live_window_is_focused(desktop: FakeDesktop) -> None:
    desktop.add(101)
    assert window.focus_existing() is True and desktop.foreground == [101]


def test_a_start_after_a_quit_opens_a_window(
    desktop: FakeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The machine B report: started from the Start menu, it sat in the tray."""
    launched: list[list[str]] = []
    monkeypatch.setattr(window, "app_browser", lambda: "msedge.exe")
    monkeypatch.setattr(window.subprocess, "Popen", lambda cmd, **kw: launched.append(cmd))
    desktop.add(101)  # the window the previous run left behind
    window.close_all()
    assert window.open_window("http://127.0.0.1:8078/") == "app"
    assert launched, "no new window was started"
