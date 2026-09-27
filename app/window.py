"""Upshot's own window: the dashboard in a browser's app mode, with a taskbar button.

The app has two ways of being there. With its window open it is on the taskbar like any
program; with the window closed it goes on in the tray, recording and noticing meetings.
Opening it (the Start menu, the installer's "Launch Upshot", a left-click on the tray
icon) shows the window, or brings it to the front when it is already open.

The window is a Chromium browser started with ``--app=<url>``: no tabs, no address bar,
its own taskbar button with the app's icon. Not an embedded WebView2, because Google
refuses to sign anyone in inside an embedded browser, and connecting the calendar is a
Google sign-in. The user's default browser is used when it is Chromium-based (they are
signed in to Google there); otherwise Edge, which every Windows 10 and 11 has. With
neither, the page opens as an ordinary tab.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import webbrowser
from pathlib import Path, PureWindowsPath
from typing import Any

from app.log import get

log = get(__name__)

#: The page's <title> (frontend/index.html). An app-mode window is titled with it alone;
#: a tab in an ordinary window adds " - Microsoft Edge" or similar, and is not ours.
WINDOW_TITLE = "Upshot"
#: The top-level window class of every Chromium browser.
CHROMIUM_CLASS = "Chrome_WidgetWin_1"
#: Browsers that take ``--app``. Firefox dropped its equivalent.
APP_MODE_BROWSERS = frozenset({"msedge.exe", "chrome.exe", "brave.exe", "vivaldi.exe"})


def exe_from_command(command: str) -> str | None:
    """The executable in a shell open command: ``"C:\\...\\chrome.exe" --single-argument %1``."""
    command = command.strip()
    if command.startswith('"'):
        end = command.find('"', 1)
        return command[1:end] if end > 0 else None
    head = command.split(" ", 1)[0]
    return head or None


def supports_app_mode(exe: str | None) -> bool:
    return bool(exe) and PureWindowsPath(str(exe)).name.lower() in APP_MODE_BROWSERS


def app_command(exe: str, url: str) -> list[str]:
    return [exe, f"--app={url}"]


def _registry(
    hive: str, path: str, name: str = ""
) -> str | None:  # pragma: no cover - Windows only
    """One registry value, or None. ``winreg`` exists only on Windows, hence the import."""
    import importlib

    winreg: Any = importlib.import_module("winreg")
    try:
        with winreg.OpenKey(getattr(winreg, hive), path) as key:
            return str(winreg.QueryValueEx(key, name)[0])
    except OSError:
        return None


def _default_browser() -> str | None:  # pragma: no cover - Windows only
    prog_id = _registry(
        "HKEY_CURRENT_USER",
        r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice",
        "ProgId",
    )
    command = prog_id and _registry("HKEY_CLASSES_ROOT", rf"{prog_id}\shell\open\command")
    return exe_from_command(command) if command else None


def _edge() -> str | None:  # pragma: no cover - Windows only
    for hive in ("HKEY_CURRENT_USER", "HKEY_LOCAL_MACHINE"):
        path = _registry(hive, r"Software\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe")
        if path and Path(path).exists():
            return path
    return None


def app_browser() -> str | None:
    """The browser to open the window with, or None for an ordinary tab."""
    if sys.platform != "win32":
        return None
    default = _default_browser()
    if supports_app_mode(default) and Path(str(default)).exists():
        return default
    return _edge()


def _is_ours(win32gui: Any, hwnd: int) -> bool:  # pragma: no cover - Windows only
    return bool(
        hwnd
        and win32gui.GetClassName(hwnd) == CHROMIUM_CLASS
        and win32gui.GetWindowText(hwnd) == WINDOW_TITLE
    )


def in_front() -> bool:
    """Whether Upshot's window is the one the user is looking at (the foreground)."""
    if sys.platform != "win32":
        return False
    try:  # pragma: no cover - Windows only
        import win32gui

        return _is_ours(win32gui, win32gui.GetForegroundWindow())
    except Exception:
        return False


#: Windows that are the desktop or the shell, never "the window the user was in".
SHELL_CLASSES = frozenset({"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW"})


def give_focus_back() -> str | None:
    """After a notification button: focus the window the user was in. Its title, or None.

    Clicking a toast takes the foreground, and when the toast closes Windows hands it to
    nobody, so the call the user was in stops taking keystrokes (machine B, D72). This
    process was started by that click, which lets it set the foreground: it gives it to
    the topmost ordinary window, which is the one the toast was shown over.
    """
    if sys.platform != "win32":
        return None
    try:  # pragma: no cover - Windows only
        import ctypes

        import win32con
        import win32gui

        def cloaked(hwnd: int) -> bool:
            value = ctypes.c_int(0)
            ctypes.windll.dwmapi.DwmGetWindowAttribute(  # type: ignore[attr-defined,unused-ignore]
                hwnd, 14, ctypes.byref(value), ctypes.sizeof(value)
            )
            return value.value != 0

        current = win32gui.GetForegroundWindow()
        if current and win32gui.GetWindowText(current) not in ("", "New notification"):
            return None  # someone already has it: leave it alone
        found: list[int] = []

        def visit(hwnd: int, _: object) -> bool:
            if found:
                return False
            style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            if (
                win32gui.IsWindowVisible(hwnd)
                and not win32gui.IsIconic(hwnd)
                and win32gui.GetWindowText(hwnd)
                and not win32gui.GetWindow(hwnd, win32con.GW_OWNER)
                and not style & win32con.WS_EX_TOOLWINDOW
                and win32gui.GetClassName(hwnd) not in SHELL_CLASSES
                and not cloaked(hwnd)
            ):
                found.append(hwnd)
            return True

        # EnumWindows reports the early stop as an error.
        with contextlib.suppress(Exception):
            win32gui.EnumWindows(visit, None)
        if not found:
            return None
        win32gui.SetForegroundWindow(found[0])
        return str(win32gui.GetWindowText(found[0]))
    except Exception as exc:
        log.info("could not give the focus back: %s", exc)
        return None


def focus_existing() -> bool:
    """Bring an open Upshot window to the front. False when there is none."""
    if sys.platform != "win32":
        return False
    import win32con  # pragma: no cover - Windows only
    import win32gui

    found: list[int] = []

    def visit(hwnd: int, _: object) -> bool:
        if win32gui.IsWindowVisible(hwnd) and _is_ours(win32gui, hwnd):
            found.append(hwnd)
        return True

    win32gui.EnumWindows(visit, None)
    if not found:
        return False
    hwnd = found[0]
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
    except Exception as exc:  # Windows may refuse the foreground to a background process
        log.info("could not bring the Upshot window forward: %s", exc)
    return True


def open_window(url: str) -> str:
    """Show Upshot's window: focus the open one, or start one at ``url``.

    Returns what it did, for the log and the tests: "focused", "app" or "tab".
    """
    if focus_existing():
        return "focused"
    browser = app_browser()
    if browser:
        try:
            subprocess.Popen(app_command(browser, url), close_fds=True)
            return "app"
        except OSError as exc:
            log.warning("could not open the Upshot window with %s: %s", browser, exc)
    webbrowser.open(url)
    return "tab"
