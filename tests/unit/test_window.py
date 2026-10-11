"""Upshot's window on Windows, against stand-in win32 modules (the real ones are
Windows-only). The case that matters: a start right after a quit (the test machine,
2026-09-30), when the old window is still closing."""

from __future__ import annotations

import os
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
        self.keys: list[tuple[int, int]] = []
        #: The window the user is in, and what was done to share its input.
        self.front = 0
        self.events: list[str] = []
        self.refuse_attach = False

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

    def keybd_event(self, key: int, scan: int, flags: int, extra: int) -> None:
        self.keys.append((key, flags))

    def GetForegroundWindow(self) -> int:
        return self.front

    # -- win32process / win32api
    def GetWindowThreadProcessId(self, hwnd: int) -> tuple[int, int]:
        return (hwnd * 10, hwnd * 100)  # thread, process

    def AttachThreadInput(self, mine: int, theirs: int, attach: bool) -> None:
        if self.refuse_attach:
            raise RuntimeError("(5, 'AttachThreadInput', 'Access is denied.')")
        self.events.append(f"{'attach' if attach else 'detach'} {mine}->{theirs}")

    def GetCurrentThreadId(self) -> int:
        return self.thread

    thread = 7

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
    con.VK_MENU = 0x12  # type: ignore[attr-defined]
    con.KEYEVENTF_KEYUP = 2  # type: ignore[attr-defined]
    api = types.ModuleType("win32api")
    api.keybd_event = fake.keybd_event  # type: ignore[attr-defined]
    api.GetCurrentThreadId = fake.GetCurrentThreadId  # type: ignore[attr-defined]
    proc = types.ModuleType("win32process")
    proc.GetWindowThreadProcessId = fake.GetWindowThreadProcessId  # type: ignore[attr-defined]
    proc.AttachThreadInput = fake.AttachThreadInput  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "win32process", proc)
    monkeypatch.setattr(
        window, "_allow_any_foreground", lambda: fake.events.append("allow any"), raising=False
    )
    monkeypatch.setitem(sys.modules, "win32gui", gui)
    monkeypatch.setitem(sys.modules, "win32con", con)
    monkeypatch.setitem(sys.modules, "win32api", api)
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
    """The test machine report: started from the Start menu, it sat in the tray."""
    launched: list[list[str]] = []
    monkeypatch.setattr(window, "app_browser", lambda: "msedge.exe")
    monkeypatch.setattr(window.subprocess, "Popen", lambda cmd, **kw: launched.append(cmd))
    desktop.add(101)  # the window the previous run left behind
    window.close_all()
    assert window.open_window("http://127.0.0.1:8078/") == "app"
    assert launched, "no new window was started"


def test_after_the_sign_in_upshot_is_brought_to_the_front(desktop: FakeDesktop) -> None:
    """With an Alt press around it, which Windows takes as the user's own switch."""
    desktop.add(101)
    assert window.bring_to_front() is True
    assert desktop.foreground == [101]
    assert desktop.keys == [(0x12, 0), (0x12, 2)], "Alt down, then up"


def test_nothing_to_bring_forward_without_a_window(desktop: FakeDesktop) -> None:
    assert window.bring_to_front() is False and desktop.keys == []


def test_setup_opens_maximized(desktop: FakeDesktop, monkeypatch: pytest.MonkeyPatch) -> None:
    """Setup's steps are laid out for a full screen; the app's window keeps its own size."""
    launched: list[list[str]] = []
    monkeypatch.setattr(window, "app_browser", lambda: "msedge.exe")
    monkeypatch.setattr(window.subprocess, "Popen", lambda cmd, **kw: launched.append(cmd))
    window.open_window("http://127.0.0.1:8078/", maximized=True)
    window.open_window("http://127.0.0.1:8078/")
    assert "--start-maximized" in launched[0]
    assert "--start-maximized" not in launched[1]


def test_a_link_is_opened_from_the_foreground_so_the_browser_can_come_forward(
    desktop: FakeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "Join the meeting" opened the link in a browser window behind Upshot's
    (z8tj1hfdwz): Windows lets only the program the user is in take the foreground. The
    server shares the input of the window the user is in, passes the right on to any
    process, opens the link, then lets go."""
    desktop.front = 5  # Upshot's window, whose thread is 50
    monkeypatch.setattr(
        os, "startfile", lambda url: desktop.events.append(f"open {url}"), raising=False
    )
    assert window.open_external("https://meet.google.com/abc-defg-hij") is True
    assert desktop.events == [
        "attach 7->50",
        "allow any",
        "open https://meet.google.com/abc-defg-hij",
        "detach 7->50",
    ]
    assert desktop.keys == [], "no synthetic key presses"


def test_the_input_is_let_go_even_when_the_link_cannot_be_opened(
    desktop: FakeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(url: str) -> None:
        raise OSError("no association")

    desktop.front = 5
    monkeypatch.setattr(os, "startfile", refuse, raising=False)
    assert window.open_external("https://meet.google.com/abc-defg-hij") is False
    assert desktop.events == ["attach 7->50", "allow any", "detach 7->50"]


def test_a_link_still_opens_when_windows_refuses_to_share_the_input(
    desktop: FakeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    desktop.front = 5
    desktop.refuse_attach = True
    monkeypatch.setattr(
        os, "startfile", lambda url: desktop.events.append(f"open {url}"), raising=False
    )
    assert window.open_external("https://meet.google.com/abc-defg-hij") is True
    assert desktop.events == ["allow any", "open https://meet.google.com/abc-defg-hij"]


def test_nothing_to_attach_to_when_no_window_is_in_front(
    desktop: FakeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        os, "startfile", lambda url: desktop.events.append(f"open {url}"), raising=False
    )
    assert window.open_external("https://meet.google.com/abc-defg-hij") is True
    assert desktop.events == ["allow any", "open https://meet.google.com/abc-defg-hij"]


def test_no_attachment_when_the_window_in_front_is_on_this_thread(
    desktop: FakeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    desktop.front = 5
    desktop.thread = 50  # GetWindowThreadProcessId(5) is thread 50
    monkeypatch.setattr(
        os, "startfile", lambda url: desktop.events.append(f"open {url}"), raising=False
    )
    assert window.open_external("https://meet.google.com/abc-defg-hij") is True
    assert desktop.events == ["allow any", "open https://meet.google.com/abc-defg-hij"]
