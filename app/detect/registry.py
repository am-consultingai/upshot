"""The microphone ConsentStore watch (DETECTION.md §2 signal 1, TECHNICAL-DESIGN §5).

An undocumented artifact — it backs the Windows privacy indicator, not a public
contract — so it is a strong heuristic with pycaw as the backstop, never gospel.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.detect.sources import MicHolder
from app.log import get

log = get(__name__)

CONSENT_ROOT = r"SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore"
MICROPHONE_KEY = rf"{CONSENT_ROOT}\microphone"
WEBCAM_KEY = rf"{CONSENT_ROOT}\webcam"
NON_PACKAGED = "NonPackaged"
IN_USE = 0  # LastUsedTimeStop == 0 means "in use right now"


def decode_exe(subkey: str) -> str:
    """``C:#Program Files#Zoom#Zoom.exe`` → ``C:\\Program Files\\Zoom\\Zoom.exe``."""
    return subkey.replace("#", "\\")


@dataclass
class ConsentStoreReader:
    """Reads who currently holds a capability. Windows-only; import stays inside."""

    key: str = MICROPHONE_KEY

    def _winreg(self) -> Any:
        import winreg

        return winreg

    def current_holders(self) -> list[MicHolder]:
        """Never raises: this signal is a heuristic, and pycaw is the backstop."""
        try:
            return list(self._read())
        except Exception as exc:
            log.debug("consent store unavailable: %s", exc)
            return []

    def _read(self) -> list[MicHolder]:  # pragma: no cover - Windows only
        winreg = self._winreg()
        holders: list[MicHolder] = []
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.key) as root:
            index = 0
            while True:
                try:
                    name = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                if name == NON_PACKAGED:
                    with winreg.OpenKey(root, name) as packaged_root:
                        holders.extend(self._holders_under(packaged_root, packaged=False))
                    continue
                with winreg.OpenKey(root, name) as key:
                    holders.extend(self._holders_under_key(key, name, packaged=True))
        return holders

    def _holders_under(self, root: Any, *, packaged: bool) -> list[MicHolder]:  # pragma: no cover
        winreg = self._winreg()
        found: list[MicHolder] = []
        index = 0
        while True:
            try:
                name = winreg.EnumKey(root, index)
            except OSError:
                break
            index += 1
            with winreg.OpenKey(root, name) as key:
                found.extend(self._holders_under_key(key, name, packaged=packaged))
        return found

    def _holders_under_key(
        self, key: Any, name: str, *, packaged: bool
    ) -> list[MicHolder]:  # pragma: no cover - Windows only
        winreg = self._winreg()
        try:
            stop, _type = winreg.QueryValueEx(key, "LastUsedTimeStop")
        except OSError:
            return []
        if int(stop) != IN_USE:
            return []
        start = 0
        with contextlib.suppress(OSError):
            start, _type = winreg.QueryValueEx(key, "LastUsedTimeStart")
        return [
            MicHolder(
                process=name if packaged else decode_exe(name),
                since_ms=int(start) // 10_000,
                packaged=packaged,
            )
        ]


class ConsentStoreWatcher:
    """``RegNotifyChangeKeyValue`` on the subtree, **re-armed after every fire**.

    The notification is one-shot: without the re-arm the detector wakes exactly once and
    then never again, which is the classic bug in this API.
    """

    def __init__(self, on_change: Callable[[], None], *, key: str = MICROPHONE_KEY) -> None:
        self.on_change = on_change
        self.key = key
        self.wakes = 0
        self.rearms = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self.run, name="detector-registry", daemon=True)
        self._thread = thread
        thread.start()
        return thread

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def run(self, *, timeout_ms: int = 1000) -> None:  # pragma: no cover - Windows only
        import win32api
        import win32con
        import win32event

        handle = win32api.RegOpenKeyEx(win32con.HKEY_CURRENT_USER, self.key, 0, win32con.KEY_NOTIFY)
        event = win32event.CreateEvent(None, False, False, None)
        try:
            while not self._stop.is_set():
                win32api.RegNotifyChangeKeyValue(
                    handle,
                    True,  # bWatchSubtree
                    win32con.REG_NOTIFY_CHANGE_LAST_SET,
                    event,
                    True,  # fAsynchronous
                )
                self.rearms += 1
                # Wait with a timeout so the thread observes the stop flag.
                result = win32event.WaitForSingleObject(event, timeout_ms)
                if result == win32event.WAIT_OBJECT_0:
                    self.wakes += 1
                    self.on_change()
        finally:
            win32api.RegCloseKey(handle)
