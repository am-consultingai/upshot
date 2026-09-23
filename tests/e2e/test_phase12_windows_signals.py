"""T3 and the other real-signal detector tests. Windows only; collected everywhere."""

from __future__ import annotations

import contextlib
import os
import time

import pytest

from app.detect.registry import (
    MICROPHONE_KEY,
    ConsentStoreReader,
    ConsentStoreWatcher,
    decode_exe,
)
from app.detect.sessions import PycawSessions
from app.detect.windows import EnumWindowTitles


def test_decode_exe_path() -> None:
    """Pure, so it runs everywhere: NonPackaged subkeys are the exe path with \\ → #."""
    assert decode_exe("C:#Program Files#Zoom#bin#Zoom.exe") == r"C:\Program Files\Zoom\bin\Zoom.exe"
    assert decode_exe("Microsoft.Teams_8wekyb3d8bbwe") == "Microsoft.Teams_8wekyb3d8bbwe"


def test_reader_is_quiet_off_windows() -> None:
    """No registry here → an empty holder list, never an exception."""
    if os.name == "nt":  # pragma: no cover - Windows only
        pytest.skip("this asserts the non-Windows fallback")
    assert ConsentStoreReader().current_holders() == []
    assert PycawSessions().render_processes() == []
    assert EnumWindowTitles().titles() == []
    assert EnumWindowTitles().foreground_title() == ""


@pytest.mark.windows
@pytest.mark.audio_hw
def test_registry_sees_self() -> None:
    """T3: open a real capture stream and watch this process appear in the ConsentStore."""
    if os.name != "nt":
        pytest.skip("the microphone ConsentStore is Windows-only")
    from app.audio.wasapi import WasapiCapture

    reader = ConsentStoreReader()
    executable = os.path.basename(os.sys.executable)  # type: ignore[attr-defined]
    capture = WasapiCapture("me")
    capture.start()
    try:
        deadline = time.monotonic() + 5
        holders: list[str] = []
        while time.monotonic() < deadline:
            holders = [h.process for h in reader.current_holders()]
            if any(executable.lower() in holder.lower() for holder in holders):
                break
            time.sleep(0.25)
        assert any(executable.lower() in holder.lower() for holder in holders), holders
    finally:
        capture.stop()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        holders = [h.process for h in reader.current_holders()]
        if not any(executable.lower() in holder.lower() for holder in holders):
            break
        time.sleep(0.25)
    assert not any(executable.lower() in holder.lower() for holder in holders)


@pytest.mark.windows
def test_registry_rearm() -> None:
    """Three consecutive ConsentStore changes must produce three wakes."""
    if os.name != "nt":
        pytest.skip("RegNotifyChangeKeyValue is Windows-only")
    import winreg

    wakes: list[float] = []
    watcher = ConsentStoreWatcher(lambda: wakes.append(time.monotonic()), key=MICROPHONE_KEY)
    thread = watcher.start()
    try:
        # Armed first: on a cold machine loading pywin32 in the thread took longer than a
        # fixed half second, and writes made before the first arm are never seen (B).
        deadline = time.monotonic() + 15
        while watcher.rearms == 0 and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert watcher.rearms > 0, f"the watcher never armed (thread alive: {thread.is_alive()})"
        for index in range(3):
            with winreg.CreateKey(
                winreg.HKEY_CURRENT_USER, rf"{MICROPHONE_KEY}\NonPackaged\ma#selftest"
            ) as key:
                winreg.SetValueEx(key, "LastUsedTimeStop", 0, winreg.REG_QWORD, index + 1)
            deadline = time.monotonic() + 3
            while len(wakes) <= index and time.monotonic() < deadline:
                time.sleep(0.05)
        assert len(wakes) >= 3, f"the one-shot notification was not re-armed: {wakes}"
        assert watcher.rearms >= 3
    finally:
        watcher.stop()
        # Only when a write happened: a missing key here would hide the real failure.
        with (
            contextlib.suppress(FileNotFoundError),
            winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, rf"{MICROPHONE_KEY}\NonPackaged", 0, winreg.KEY_ALL_ACCESS
            ) as parent,
        ):
            winreg.DeleteKey(parent, "ma#selftest")


@pytest.mark.windows
def test_window_titles_enumerated() -> None:
    """Create a window with a known title and read it back through EnumWindows."""
    if os.name != "nt":
        pytest.skip("EnumWindows is Windows-only")
    import tkinter

    title = "upshot-title-probe"
    root = tkinter.Tk()
    root.title(title)
    root.geometry("200x100")
    root.update()
    try:
        titles = EnumWindowTitles().titles()
        assert any(title in item for item in titles), titles
    finally:
        root.destroy()


@pytest.mark.windows
def test_render_sessions_enumerated() -> None:
    if os.name != "nt":
        pytest.skip("pycaw is Windows-only")
    processes = PycawSessions().render_processes()
    assert isinstance(processes, list)
