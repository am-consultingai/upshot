"""Keep the computer awake while a meeting is recorded or processed.

Nothing told Windows that Upshot was busy, so a laptop slept on its idle timer in the
middle of the work: on machine B (2026-10-13) Modern Standby took twelve minutes out of a
recording, and an hour out of the transcription that followed.

While there is a reason (``reason()`` answers with one), a Windows power request holds
the system awake: ``PowerRequestSystemRequired``, and ``PowerRequestExecutionRequired`` so
a Modern Standby machine keeps running the process. The display may still turn off. The
request is released when the reason has been gone for ``linger_s``: between two stages of
one meeting the worker is briefly idle, and on a machine whose idle timeout has long
passed a release, even for a second, is a sleep. The reason shows in ``powercfg
/requests``. Closing the lid, the power button, Sleep and a critical battery still win.

Level-triggered: a thread asks ``reason()`` every ``interval_s``. A power request
belongs to its handle, not to a thread, so it is set and cleared from that one thread.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

log = logging.getLogger(__name__)

#: Shorter than any idle timeout Windows offers (one minute).
INTERVAL_S = 15.0
#: Longer than the gap between two stages of one meeting.
LINGER_S = 120.0
#: A request the system refused is asked for again after this long.
RETRY_S = 60.0


class PowerRequest(Protocol):
    def hold(self, reason: str) -> bool: ...

    def release(self) -> None: ...


class NoPowerRequest:
    """Not Windows: nothing to ask."""

    def hold(self, reason: str) -> bool:
        return False

    def release(self) -> None:
        return None


class WindowsPowerRequest:  # pragma: no cover - Windows only
    """``PowerCreateRequest`` with a plain reason, set for system and execution."""

    SYSTEM_REQUIRED = 1
    EXECUTION_REQUIRED = 3
    KINDS = (SYSTEM_REQUIRED, EXECUTION_REQUIRED)

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class Detailed(ctypes.Structure):
            _fields_ = [
                ("LocalizedReasonModule", wintypes.HMODULE),
                ("LocalizedReasonId", wintypes.ULONG),
                ("ReasonStringCount", wintypes.ULONG),
                ("ReasonStrings", ctypes.POINTER(wintypes.LPWSTR)),
            ]

        class Union(ctypes.Union):
            _fields_ = [("Detailed", Detailed), ("SimpleReasonString", wintypes.LPWSTR)]

        class Reason(ctypes.Structure):
            # REASON_CONTEXT, its full size; with POWER_REQUEST_CONTEXT_SIMPLE_STRING only
            # the string member of the union is read.
            _fields_ = [("Version", wintypes.ULONG), ("Flags", wintypes.DWORD), ("Reason", Union)]

        self._ctypes = ctypes
        self._reason_type = Reason
        # `WinDLL` exists only on Windows, where the ignore is in turn unused.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined,unused-ignore]
        kernel32.PowerCreateRequest.argtypes = [ctypes.POINTER(Reason)]
        kernel32.PowerCreateRequest.restype = wintypes.HANDLE
        kernel32.PowerSetRequest.argtypes = [wintypes.HANDLE, ctypes.c_int]
        kernel32.PowerSetRequest.restype = wintypes.BOOL
        kernel32.PowerClearRequest.argtypes = [wintypes.HANDLE, ctypes.c_int]
        kernel32.PowerClearRequest.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        self._kernel32 = kernel32
        self._handle: Any = None
        self._set: list[int] = []

    def hold(self, reason: str) -> bool:
        """A new request for ``reason``, set before the one it replaces is cleared, so a
        change of reason leaves no moment unheld."""
        ctypes = self._ctypes
        text = ctypes.create_unicode_buffer(reason)
        context = self._reason_type(Version=0, Flags=0x1)
        context.Reason.SimpleReasonString = ctypes.cast(text, ctypes.c_wchar_p)
        handle = self._kernel32.PowerCreateRequest(ctypes.byref(context))
        if not handle or handle == ctypes.c_void_p(-1).value:
            log.warning("keep awake: PowerCreateRequest failed (%d)", ctypes.get_last_error())
            return False
        kinds: list[int] = []
        for kind in self.KINDS:
            if self._kernel32.PowerSetRequest(handle, kind):
                kinds.append(kind)
            else:
                log.warning(
                    "keep awake: PowerSetRequest(%d) failed (%d)", kind, ctypes.get_last_error()
                )
        if not kinds:
            self._kernel32.CloseHandle(handle)
            return False
        self.release()
        self._handle = handle
        self._set = kinds
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        for kind in self._set:
            self._kernel32.PowerClearRequest(self._handle, kind)
        self._kernel32.CloseHandle(self._handle)
        self._handle = None
        self._set = []


def power_request() -> PowerRequest:
    if sys.platform != "win32":
        return NoPowerRequest()
    try:  # pragma: no cover - Windows only
        return WindowsPowerRequest()
    except Exception as exc:  # pragma: no cover - Windows only
        log.warning("keep awake is unavailable: %s", exc)
        return NoPowerRequest()


class KeepAwake:
    def __init__(
        self,
        reason: Callable[[], str | None],
        *,
        request: PowerRequest | None = None,
        enabled: Callable[[], bool] = lambda: True,
        interval_s: float = INTERVAL_S,
        linger_s: float = LINGER_S,
        retry_s: float = RETRY_S,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.reason = reason
        self.request = request if request is not None else power_request()
        self.enabled = enabled
        self.interval_s = interval_s
        self.linger_s = linger_s
        self.retry_s = retry_s
        self.monotonic = monotonic
        #: The reason the request is held for, or None when it is not held.
        self.held: str | None = None
        #: A reason the system refused a request for (or NoPowerRequest declined).
        self._refused: str | None = None
        self._refused_at = 0.0
        self._last_reason_at = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self) -> None:
        """Hold, change or release the request to match what is happening now."""
        try:
            enabled = bool(self.enabled())
            reason = self.reason() if enabled else None
        except Exception:
            log.exception("keep awake: could not tell whether anything is running")
            enabled, reason = True, None
        now = self.monotonic()
        if reason is not None:
            self._last_reason_at = now
            refused = reason == self._refused and now - self._refused_at < self.retry_s
            if reason != self.held and not refused:
                # The reason is fixed when a request is made: another reason, another one.
                if self.request.hold(reason):
                    log.info("keeping the computer awake: %s", reason)
                    self.held = reason
                    self._refused = None
                else:
                    # Asked again only after ``retry_s``: one warning a minute is enough.
                    # What was held stays held (a new one is set before the old is
                    # cleared), and is released as usual (review of #27).
                    self._refused, self._refused_at = reason, now
            return
        self._refused = None
        if self.held is not None and (not enabled or now - self._last_reason_at >= self.linger_s):
            self.release()

    def release(self) -> None:
        if self.held is None:
            return
        # Released before it is forgotten: one that raises is still held, and tried again.
        self.request.release()
        self.held = None
        log.info("no longer keeping the computer awake")

    def run_forever(self) -> None:
        # Nothing may end the thread: a request held when it ended would be held until
        # the app closes, and none would be held again (review of #27).
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("keep awake: tick failed")
            self._stop.wait(self.interval_s)
        try:
            self.release()
        except Exception:
            log.exception("keep awake: could not release")

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self.run_forever, name="keep-awake", daemon=True)
        self._thread = thread
        thread.start()
        return thread

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None
        self.release()


def busy_reason(services: Any) -> str | None:
    """Why the computer must stay awake now, in the words ``powercfg /requests`` shows."""
    recorder = getattr(services, "recorder", None)
    if recorder is not None and getattr(recorder, "committed", False):
        return "Upshot is recording a meeting"
    worker = getattr(services, "worker", None)
    if worker is not None and getattr(worker, "busy", False):
        return "Upshot is transcribing and summarizing"
    return None
