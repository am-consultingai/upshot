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
        #: The foreground window, and each window's process and its executable.
        self.front = 0
        self.pids: dict[int, int] = {}
        self.images: dict[int, str] = {}
        self.sleeps: list[float] = []

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

    def SetForegroundWindow(self, hwnd: int) -> None:
        if hwnd in self.refuse_foreground or hwnd not in self.windows:
            self.windows.pop(hwnd, None)
            raise RuntimeError("(1400, 'SetForegroundWindow', 'Invalid window handle.')")
        self.foreground.append(hwnd)
        self.front = hwnd

    def GetForegroundWindow(self) -> int:
        return self.front

    def GetWindow(self, hwnd: int, which: int) -> int:
        return 0  # no owner

    def GetWindowLong(self, hwnd: int, index: int) -> int:
        return 0  # no tool windows

    def GetWindowThreadProcessId(self, hwnd: int) -> tuple[int, int]:
        return (hwnd * 10, self.pids.get(hwnd, hwnd * 100))  # thread, process

    def browser(self, hwnd: int, title: str, pid: int, image: str = "chrome.exe") -> None:
        self.add(hwnd, title)
        self.pids[hwnd] = pid
        self.images[pid] = image


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
    con.GW_OWNER = 4  # type: ignore[attr-defined]
    con.GWL_EXSTYLE = -20  # type: ignore[attr-defined]
    con.WS_EX_TOOLWINDOW = 0x80  # type: ignore[attr-defined]
    api = types.ModuleType("win32api")
    api.keybd_event = fake.keybd_event  # type: ignore[attr-defined]
    proc = types.ModuleType("win32process")
    proc.GetWindowThreadProcessId = fake.GetWindowThreadProcessId  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "win32process", proc)
    monkeypatch.setattr(window, "_cloaked", lambda hwnd: False)
    monkeypatch.setattr(window, "_process_image", lambda pid: fake.images.get(pid, ""))
    monkeypatch.setattr(
        window, "_default_browser", lambda: r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    )
    monkeypatch.setitem(sys.modules, "win32gui", gui)
    monkeypatch.setitem(sys.modules, "win32con", con)
    monkeypatch.setitem(sys.modules, "win32api", api)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(window, "_closing", set())
    monkeypatch.setattr(window.time, "sleep", lambda s: fake.sleeps.append(s))
    monkeypatch.setattr(window, "_in_background", lambda work: work())
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


MEET = "https://meet.google.com/abc-defg-hij"


@pytest.fixture
def clicked(desktop: FakeDesktop) -> FakeDesktop:
    """The user clicked a link in Upshot's window (101, Chrome as Upshot's own process
    300). Their browser is Chrome too: a window behind (202) and one further back (203)."""
    desktop.browser(101, window.WINDOW_TITLE, pid=300)
    desktop.browser(202, "Inbox - Google Chrome", pid=500)
    desktop.browser(203, "Docs - Google Chrome", pid=500)
    desktop.front = 101
    return desktop


def test_a_browser_left_behind_upshot_is_brought_forward(
    clicked: FakeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "Join the meeting" opened the link in a browser window behind Upshot's, and the
    click looked as if it did nothing (z8tj1hfdwz)."""
    opened: list[str] = []
    monkeypatch.setattr(os, "startfile", opened.append, raising=False)
    assert window.open_external(MEET) is True
    assert opened == [MEET]
    assert clicked.front == 202, "the browser's frontmost window, never Upshot's own"
    assert clicked.keys == [(0x12, 0), (0x12, 2)], "with an Alt press around it"


def test_a_browser_that_came_forward_by_itself_is_left_alone(
    clicked: FakeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    def browser_comes_forward(url: str) -> None:
        clicked.front = 203

    monkeypatch.setattr(os, "startfile", browser_comes_forward, raising=False)
    assert window.open_external(MEET) is True
    assert clicked.front == 203 and clicked.foreground == [] and clicked.keys == []


def test_a_window_the_user_went_to_meanwhile_keeps_the_foreground(
    clicked: FakeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    clicked.add(404, "Notepad")
    monkeypatch.setattr(os, "startfile", lambda url: None, raising=False)
    sleeps = iter(range(1000))

    def user_switches(seconds: float) -> None:
        if next(sleeps) == 3:
            clicked.front = 404

    monkeypatch.setattr(window.time, "sleep", user_switches)
    assert window.open_external(MEET) is True
    assert clicked.front == 404 and clicked.foreground == []


def test_only_the_default_browsers_windows_are_candidates(
    clicked: FakeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upshot's own window is Chrome as well, in a process of its own: never chosen."""
    clicked.windows.clear()
    clicked.browser(101, window.WINDOW_TITLE, pid=300)
    clicked.browser(150, "Mail - Mozilla Firefox", pid=700, image="firefox.exe")
    clicked.browser(160, "A Chrome window of Upshot's process", pid=300)
    assert window.browser_forward() == "no chrome.exe window"
    assert clicked.front == 101

    monkeypatch.setattr(window, "_default_browser", lambda: r"C:\Mozilla Firefox\firefox.exe")
    assert window.browser_forward() == "brought the browser forward"
    assert clicked.front == 150


def test_an_unknown_default_browser_is_not_waited_for(
    clicked: FakeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(window, "_default_browser", lambda: None)
    assert window.browser_forward() == "default browser unknown"
    assert clicked.sleeps == [] and clicked.front == 101


def test_a_browser_that_came_forward_by_itself_is_told_from_another_window(
    clicked: FakeDesktop,
) -> None:
    clicked.front = 203
    assert window.browser_forward() == "the browser came forward"
    clicked.add(404, "Notepad")
    clicked.front = 404
    assert window.browser_forward() == "another window is in front"


def test_no_window_in_front_for_a_moment_is_not_taken_for_the_browser(
    clicked: FakeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows reports no foreground window while one hands over to another."""
    clicked.front = 0
    seen: list[int] = []
    real = FakeDesktop.GetForegroundWindow

    def flicker() -> int:
        if len(seen) == 3:
            clicked.front = 101  # the hand-over is done: Upshot's window again
        seen.append(real(clicked))
        return seen[-1]

    monkeypatch.setattr(sys.modules["win32gui"], "GetForegroundWindow", flicker)
    assert window.browser_forward() == "brought the browser forward"
    assert seen[:3] == [0, 0, 0], "it saw no window in front, and kept looking"
    assert clicked.front == 202


def test_a_browser_starting_cold_is_waited_for(
    clicked: FakeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    clicked.windows.pop(202)
    clicked.windows.pop(203)

    def window_appears(seconds: float) -> None:
        clicked.sleeps.append(seconds)
        if len(clicked.sleeps) == 15:  # half a second after the first second
            clicked.browser(250, "New Tab - Google Chrome", pid=900)

    monkeypatch.setattr(window.time, "sleep", window_appears)
    assert window.browser_forward() == "brought the browser forward"
    assert clicked.front == 250


def test_an_error_while_looking_still_counts_as_opened(
    clicked: FakeDesktop, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A window that goes while it is looked at raises pywintypes.error, not OSError: the
    link opened all the same, and the page must not open it a second time."""

    def gone(hwnd: int) -> str:
        raise RuntimeError("(1400, 'GetClassName', 'Invalid window handle.')")

    monkeypatch.setattr(sys.modules["win32gui"], "GetClassName", gone)
    monkeypatch.setattr(os, "startfile", lambda url: None, raising=False)
    with caplog.at_level("INFO", logger=window.log.name):
        assert window.open_external(MEET) is True
    assert "could not bring it forward" in caplog.text


def test_a_refused_bring_forward_still_counts_as_opened(
    clicked: FakeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    clicked.refuse_foreground.add(202)
    monkeypatch.setattr(os, "startfile", lambda url: None, raising=False)
    assert window.open_external(MEET) is True
    assert clicked.keys == [(0x12, 0), (0x12, 2)], "Alt is let go even then"


def test_a_link_that_cannot_be_opened_brings_nothing_forward(
    clicked: FakeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(url: str) -> None:
        raise OSError("no association")

    monkeypatch.setattr(os, "startfile", refuse, raising=False)
    assert window.open_external(MEET) is False
    assert clicked.front == 101 and clicked.keys == []
