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

        class Reason(ctypes.Structure):
            # REASON_CONTEXT with POWER_REQUEST_CONTEXT_SIMPLE_STRING: only the string
            # member of the union is read.
            _fields_ = [
                ("Version", wintypes.ULONG),
                ("Flags", wintypes.DWORD),
                ("SimpleReasonString", wintypes.LPWSTR),
            ]

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
        context = self._reason_type(0, 0x1, ctypes.cast(text, ctypes.c_wchar_p))
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
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.reason = reason
        self.request = request if request is not None else power_request()
        self.enabled = enabled
        self.interval_s = interval_s
        self.linger_s = linger_s
        self.monotonic = monotonic
        #: The reason the request is held for, or None when it is not held.
        self.held: str | None = None
        #: A reason the system refused a request for (or NoPowerRequest declined).
        self._refused: str | None = None
        self._last_reason_at = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self) -> None:
        """Hold, change or release the request to match what is happening now."""
        try:
            reason = self.reason() if self.enabled() else None
        except Exception:
            log.exception("keep awake: could not tell whether anything is running")
            reason = None
        now = self.monotonic()
        if reason is not None:
            self._last_reason_at = now
            if reason != self.held and reason != self._refused:
                # The reason is fixed when a request is made: another reason, another one.
                if self.request.hold(reason):
                    log.info("keeping the computer awake: %s", reason)
                    self.held = reason
                    self._refused = None
                else:
                    # Not asked again every tick for the same reason: one warning is enough.
                    self.held = None
                    self._refused = reason
            return
        self._refused = None
        if self.held is not None and (
            not self.enabled() or now - self._last_reason_at >= self.linger_s
        ):
            self.release()

    def release(self) -> None:
        if self.held is None:
            return
        self.request.release()
        log.info("no longer keeping the computer awake")
        self.held = None

    def run_forever(self) -> None:
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(self.interval_s)
        self.release()

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
