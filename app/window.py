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
import threading
import time
import webbrowser
from pathlib import Path, PureWindowsPath
from typing import Any

from app.log import get

log = get(__name__)

#: The page's <title> (frontend/index.html). An app-mode window is titled with it alone;
#: a tab in an ordinary window adds " - Microsoft Edge" or similar, and is not ours.
#: The page never changes it: Upshot's window is found by it (``_is_ours``).
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


def app_command(
    exe: str, url: str, profile_dir: Path | None = None, *, maximized: bool = False
) -> list[str]:
    folder = profile_dir or app_profile_dir()
    command = [
        exe,
        f"--app={url}",
        f"--user-data-dir={folder}",
        # A new profile would otherwise open with the browser's welcome and
        # default-browser prompts in front of the app.
        "--no-first-run",
        "--no-default-browser-check",
    ]
    # Setup fills the screen, so no step needs scrolling (product owner, 2026-10-08).
    if maximized:
        command.append("--start-maximized")
    return command


def open_external(url: str) -> bool:
    """Open a link in the user's default browser, the way Windows opens any link: in the
    profile they were last in. False when it was not opened here (not Windows, or not an
    http(s) link) and the page should open it itself.

    Only http and https: ``os.startfile`` would run anything else, a file path included.
    The browser is then put in front if it did not come forward by itself
    (``browser_forward``).
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("only http and https links are opened")
    if sys.platform != "win32":
        return False
    import os

    try:
        os.startfile(url)  # type: ignore[attr-defined,unused-ignore]
    except OSError as exc:
        log.warning("could not open a link in the default browser: %s", exc)
        return False
    log.info("opened a link to %s in the default browser", parsed.hostname)
    _in_background(_bring_browser_forward)
    return True


def _in_background(work: Any) -> None:
    """The page waits for ``/api/open`` before anything else, and needs only whether the
    link opened: the browser is brought forward after the answer."""
    threading.Thread(target=work, name="browser-forward", daemon=True).start()


def _bring_browser_forward() -> None:
    try:
        outcome = browser_forward()
    except Exception as exc:  # pywintypes.error: a window went while it was looked at
        outcome = f"could not bring it forward: {exc}"
    log.info("after the link: %s", outcome)


#: Windows Terminal's window, and the classic console's.
TERMINAL_CLASSES = frozenset({"CASCADIA_HOSTING_WINDOW_CLASS", "ConsoleWindowClass"})


def terminal_windows(title: str) -> set[int]:
    """The visible terminal windows titled ``title``: taken before a launch, so the
    window that opens is told from Upshot's terminals already open."""
    if sys.platform != "win32":
        return set()
    try:  # pragma: no cover - Windows only
        import win32gui
    except ImportError:  # pragma: no cover - pywin32 ships with the Windows build
        return set()
    found: set[int] = set()  # pragma: no cover - Windows only, from here on

    def visit(hwnd: int, _: object) -> bool:
        # A window can close between two of these calls; it is skipped, not the click.
        with contextlib.suppress(Exception):
            if (
                win32gui.IsWindowVisible(hwnd)
                and win32gui.GetClassName(hwnd) in TERMINAL_CLASSES
                and win32gui.GetWindowText(hwnd) == title
            ):
                found.add(hwnd)
        return True

    try:
        win32gui.EnumWindows(visit, None)
    except Exception as exc:  # pywintypes.error: the list is a nicety, not the terminal
        log.info("could not list the terminal windows: %s", exc)
    return found


def bring_terminal_forward(title: str, before: set[int]) -> None:
    """After the server opened a terminal (Settings → AI apps), put it in front, in the
    background: the page has its answer already. ``before``: the windows of that title
    that were open already, which are left where they are."""

    def work() -> None:
        try:
            outcome = terminal_forward(title, before)
        except Exception as exc:  # pywintypes.error: the window went while it was looked at
            outcome = f"could not bring it forward: {exc}"
        log.info("after opening a terminal: %s", outcome)

    threading.Thread(target=work, name="terminal-forward", daemon=True).start()


def terminal_forward(title: str, before: set[int], *, window_s: float = 5.0) -> str:
    """Put the terminal this server just opened in front of Upshot's window.

    The same reason as ``browser_forward``: Windows gives the foreground to the program
    the user is in, Upshot's window, not this server, so the terminal opened behind it
    (seen on machine A, 2026-10-14). The terminal is found by its title, which the server
    set, and its class, which tells it from Upshot's own window of the same name; a
    terminal of that title open before the launch (``before``) is not the new one. Only
    while Upshot's window is in front: a window the user went to meanwhile stays.
    """
    if sys.platform != "win32":
        return "not Windows"
    try:
        import win32api
        import win32con
        import win32gui
    except ImportError:  # pragma: no cover - pywin32 ships with the Windows build
        return "no pywin32"
    target = None  # pragma: no cover - Windows only, from here on
    for _ in range(max(1, round(window_s / POLL_S))):
        new = terminal_windows(title) - before
        if new:
            target = min(new)
            break
        time.sleep(POLL_S)
    if not target and len(before) == 1:
        # Windows Terminal set to open new tabs in an existing window put it in the one
        # already showing this app's terminal: that window is the one to bring forward.
        (target,) = before
    if not target:
        return "no terminal window"
    front = win32gui.GetForegroundWindow()
    if front == target:
        return "the terminal came forward"
    if not _is_ours(win32gui, front):
        return "another window is in front"
    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
    try:
        win32gui.SetForegroundWindow(target)
    finally:
        win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    return "brought the terminal forward"


#: How long a browser has to take the foreground by itself once it has a link, and how
#: long to wait for a window from one that was not running.
BROWSER_OWN_S = 1.0
BROWSER_WINDOW_S = 5.0
POLL_S = 0.1


def browser_forward(*, own_s: float = BROWSER_OWN_S, window_s: float = BROWSER_WINDOW_S) -> str:
    """Put the default browser in front of Upshot's window after a link was opened.
    What happened, for the log and the tests: "the browser came forward",
    "another window is in front", "brought the browser forward", "no <exe> window",
    "default browser unknown" or "could not bring it forward: ...".

    Windows gives the foreground only to the program the user is in, which is Upshot's
    window, not this server, so a browser already running often took the link into a
    window left behind Upshot's, and "Join the meeting" looked as if it did nothing
    (z8tj1hfdwz). Passing the right on is refused to this process (AllowSetForegroundWindow
    fails with access denied), and whether the browser comes forward by itself varied
    from try to try on machines A and B. So when Upshot's window is still in front once
    the browser has had a moment, this process puts the browser's window in front itself,
    as ``bring_to_front`` does for Upshot's: an Alt press around SetForegroundWindow.
    Only while Upshot's window is in front: a window the user went to meanwhile stays.
    """
    try:
        import win32api
        import win32con
        import win32gui
        import win32process
    except ImportError:  # pragma: no cover - pywin32 ships with the Windows build
        return "default browser unknown"
    exe = _default_browser()
    name = PureWindowsPath(exe).name.lower() if exe else ""
    if not name:
        return "default browser unknown"

    def elsewhere(front: int) -> str:
        pid = win32process.GetWindowThreadProcessId(front)[1]
        if _process_image(pid).lower() == name:
            return "the browser came forward"
        return "another window is in front"

    ours = 0
    for _ in range(max(1, round(own_s / POLL_S))):
        front = win32gui.GetForegroundWindow()
        if front and not _is_ours(win32gui, front):
            return elsewhere(front)
        if front:
            ours = front
        time.sleep(POLL_S)
    if not ours:
        return "another window is in front"
    our_pid = win32process.GetWindowThreadProcessId(ours)[1]
    target = None
    for _ in range(max(1, round(window_s / POLL_S))):
        target = _browser_window(win32gui, win32con, win32process, name, our_pid)
        if target:
            break
        time.sleep(POLL_S)
    if not target:
        return f"no {name} window"
    front = win32gui.GetForegroundWindow()
    if front != ours:
        return elsewhere(front) if front else "another window is in front"
    try:
        if win32gui.IsIconic(target):
            win32gui.ShowWindow(target, win32con.SW_RESTORE)
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        try:
            win32gui.SetForegroundWindow(target)
        finally:
            win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    except Exception as exc:  # pywintypes.error: Windows refused, or the window closed
        return f"could not bring it forward: {exc}"
    return "brought the browser forward"


def _browser_window(
    win32gui: Any, win32con: Any, win32process: Any, exe_name: str, skip_pid: int
) -> int | None:
    """The browser's frontmost top-level window, which is the one it puts a link in: a
    window of ``exe_name`` that is not Upshot's (``skip_pid``, the window's process)."""
    if not exe_name:
        return None
    found: list[int] = []

    def visit(hwnd: int, _: object) -> bool:
        if found or not win32gui.IsWindowVisible(hwnd) or not win32gui.GetWindowText(hwnd):
            return True
        if win32gui.GetWindow(hwnd, win32con.GW_OWNER) or _cloaked(hwnd):
            return True
        if win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE) & win32con.WS_EX_TOOLWINDOW:
            return True
        pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        if pid != skip_pid and _process_image(pid).lower() == exe_name:
            found.append(hwnd)
        return True

    win32gui.EnumWindows(visit, None)
    return found[0] if found else None


def _cloaked(hwnd: int) -> bool:  # pragma: no cover - Windows only
    """On another virtual desktop, or otherwise hidden by the compositor."""
    import ctypes

    value = ctypes.c_int(0)
    ctypes.windll.dwmapi.DwmGetWindowAttribute(  # type: ignore[attr-defined,unused-ignore]
        hwnd, 14, ctypes.byref(value), ctypes.sizeof(value)
    )
    return value.value != 0


def _process_image(pid: int) -> str:  # pragma: no cover - Windows only
    """The file name of a process's executable ("chrome.exe"), or "" when unknown."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined,unused-ignore]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buffer))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return PureWindowsPath(buffer.value).name
    finally:
        kernel32.CloseHandle(handle)


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
    nobody, so the call the user was in stops taking keystrokes (the test machine, D72). This
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


def find_window() -> int | None:
    """Upshot's open window, if there is one: for a feedback screenshot (D87)."""
    if sys.platform != "win32":
        return None
    try:  # pragma: no cover - Windows only
        import win32gui

        found: list[int] = []

        def visit(hwnd: int, _: object) -> bool:
            if _is_ours(win32gui, hwnd) and hwnd not in _closing:
                found.append(hwnd)
            return True

        win32gui.EnumWindows(visit, None)
        return found[0] if found else None
    except Exception:
        return None


def count_open() -> int:
    """How many Upshot windows are open. An update reopens the window if one was (D87)."""
    if sys.platform != "win32":
        return 0
    try:  # pragma: no cover - Windows only
        import win32gui

        found: list[int] = []

        def visit(hwnd: int, _: object) -> bool:
            if _is_ours(win32gui, hwnd) and hwnd not in _closing:
                found.append(hwnd)
            return True

        win32gui.EnumWindows(visit, None)
        return len(found)
    except Exception:
        return 0


def close_all(*, wait_s: float = CLOSE_WAIT_S) -> int:
    """Close every Upshot window, and wait (up to ``wait_s``) until they are gone. How
    many were asked to close.

    The window is a browser window, so it outlives the app: quitting (the tray, or the
    installer stopping the app for an upgrade) left a dead page behind, and the next start
    found that window and focused it instead of opening one, which showed nothing on
    the test machine (D74). Posting WM_CLOSE alone was not enough: the close is asynchronous, so
    the start that followed still found the closing window, failed to bring it forward
    ("Invalid window handle") and counted that as shown — the app sat in the tray with
    no window and no taskbar button (the test machine, 2026-09-30).
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


def bring_to_front() -> bool:
    """Put Upshot's open window in front, from the background. False if there is none.

    After Google's sign-in, which runs in the user's browser, Upshot should be where the
    user comes back to. Windows gives the foreground only to the program the user is in
    (the browser), so a plain SetForegroundWindow from here just flashes the taskbar
    button. A synthetic Alt press around the call is the usual way past that lock: Windows
    treats the next foreground change as the user's own.
    """
    if sys.platform != "win32":
        return False
    import win32api
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
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        try:
            win32gui.SetForegroundWindow(hwnd)
        finally:
            win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    except Exception as exc:
        log.info("could not bring the Upshot window to the front: %s", exc)
        return False
    return True


def open_window(url: str, *, maximized: bool = False) -> str:
    """Show Upshot's window: focus the open one, or start one at ``url``, filling the
    screen when ``maximized``.

    Returns what it did, for the log and the tests: "focused", "app" or "tab".
    """
    if focus_existing():
        return "focused"
    browser = app_browser()
    if browser:
        try:
            subprocess.Popen(app_command(browser, url, maximized=maximized), close_fds=True)
            return "app"
        except OSError as exc:
            log.warning("could not open the Upshot window with %s: %s", browser, exc)
    webbrowser.open(url)
    return "tab"
