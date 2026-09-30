"""Upshot's own window: the dashboard in a browser's app mode, with a taskbar button.

The app has two ways of being there. With its window open it is on the taskbar like any
program; with the window closed it goes on in the tray, recording and noticing meetings.
Opening it (the Start menu, the installer's "Launch Upshot", a left-click on the tray
icon) shows the window, or brings it to the front when it is already open.

The window is a Chromium browser started with ``--app=<url>``: no tabs, no address bar,
its own taskbar button with the app's icon. Not an embedded WebView2, because Google
refuses to sign anyone in inside an embedded browser. The user's default browser is used
when it is Chromium-based; otherwise Edge, which every Windows 10 and 11 has. With
neither, the page opens as an ordinary tab.

The window runs in a browser profile of its own (``<app home>/browser``), never one of the
user's. Chrome and Edge open a link from another program in the *last-used* profile, and
an app window counts: clicking Upshot's window made its profile the last used one, so
Google's sign-in kept opening there, whichever profile the user had been in (ClickUp
z8tj1hca86). In a profile of its own, the window leaves the user's choice alone, and every
link leaving the app (``open_external``) goes to the default browser, into the profile
the user was last in — as any desktop program's links do.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import time
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


def app_profile_dir() -> Path:
    """The window's own browser profile: settings and cache, never the user's accounts."""
    from app import paths

    return paths.app_home() / "browser"


def app_command(exe: str, url: str, profile_dir: Path | None = None) -> list[str]:
    folder = profile_dir or app_profile_dir()
    return [
        exe,
        f"--app={url}",
        f"--user-data-dir={folder}",
        # A new profile would otherwise open with the browser's welcome and
        # default-browser prompts in front of the app.
        "--no-first-run",
        "--no-default-browser-check",
    ]


def open_external(url: str) -> bool:
    """Open a link in the user's default browser, the way Windows opens any link: in the
    profile they were last in. False when it was not opened here (not Windows, or not an
    http(s) link) and the page should open it itself.

    Only http and https: ``os.startfile`` would run anything else, a file path included.
    """
    from urllib.parse import urlparse

    if urlparse(url).scheme not in ("http", "https"):
        raise ValueError("only http and https links are opened")
    if sys.platform != "win32":
        return False
    try:
        import os

        os.startfile(url)  # type: ignore[attr-defined,unused-ignore]
    except OSError as exc:
        log.warning("could not open a link in the default browser: %s", exc)
        return False
    return True


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


#: Windows asked to close by ``close_all`` and not gone yet: never focused instead of
#: opening a new one.
_closing: set[int] = set()

#: How long ``close_all`` waits for a closed window to be gone.
CLOSE_WAIT_S = 3.0


def close_all(*, wait_s: float = CLOSE_WAIT_S) -> int:
    """Close every Upshot window, and wait (up to ``wait_s``) until they are gone. How
    many were asked to close.

    The window is a browser window, so it outlives the app: quitting (the tray, or the
    installer stopping the app for an upgrade) left a dead page behind, and the next start
    found that window and focused it instead of opening one, which showed nothing on
    machine B (D74). Posting WM_CLOSE alone was not enough: the close is asynchronous, so
    the start that followed still found the closing window, failed to bring it forward
    ("Invalid window handle") and counted that as shown — the app sat in the tray with
    no window and no taskbar button (machine B, 2026-09-30).
    """
    if sys.platform != "win32":
        return 0
    try:
        import win32con
        import win32gui

        found: list[int] = []

        def visit(hwnd: int, _: object) -> bool:
            if _is_ours(win32gui, hwnd):
                found.append(hwnd)
            return True

        win32gui.EnumWindows(visit, None)
        for hwnd in found:
            _closing.add(hwnd)
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        deadline = time.monotonic() + wait_s
        while any(win32gui.IsWindow(hwnd) for hwnd in found) and time.monotonic() < deadline:
            time.sleep(0.05)
        return len(found)
    except Exception as exc:
        log.info("could not close the Upshot windows: %s", exc)
        return 0


def focus_existing() -> bool:
    """Bring an open Upshot window to the front. False when there is none, or when the
    one found turns out to be gone (then a new one is opened instead)."""
    if sys.platform != "win32":
        return False
    import win32con
    import win32gui

    found: list[int] = []

    def visit(hwnd: int, _: object) -> bool:
        if hwnd not in _closing and win32gui.IsWindowVisible(hwnd) and _is_ours(win32gui, hwnd):
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
        if not win32gui.IsWindow(hwnd):
            log.info("the Upshot window closed as it was brought forward; opening one")
            return False
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
