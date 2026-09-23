"""Live input level for the settings screen (TECHNICAL-DESIGN.md §4.1).

A preview meter that opens the chosen endpoint, measures it, and writes nothing to disk.
It deliberately reuses ``make_capture`` rather than talking to WASAPI directly: what the
meter shows is then the same signal path the recorder would use, so a device that reads
silent here reads silent in a meeting too.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import numpy as np

from app.audio.capture import AudioCapture, AudioFormat
from app.log import get

log = get(__name__)

_DTYPES = {"float32": np.float32, "int16": np.int16}


def levels_of(payload: bytes, fmt: AudioFormat) -> tuple[float, float]:
    """(rms, peak) in 0..1, downmixed to mono. Both 0.0 for an empty or partial block."""
    dtype = _DTYPES.get(fmt.dtype)
    if dtype is None or not payload:
        return 0.0, 0.0
    data = np.frombuffer(payload, dtype=dtype)
    if data.size == 0:
        return 0.0, 0.0
    audio = data.astype(np.float32)
    if dtype is np.int16:
        audio = audio / 32768.0
    if fmt.channels > 1:
        usable = audio.size - (audio.size % fmt.channels)
        if usable == 0:
            return 0.0, 0.0
        audio = audio[:usable].reshape(-1, fmt.channels).mean(axis=1)
    rms = float(np.sqrt(np.mean(np.square(audio))))
    peak = float(np.abs(audio).max())
    return rms, min(peak, 1.0)


@dataclass
class Reading:
    rms: float = 0.0
    peak: float = 0.0
    clipped: bool = False
    blocks: int = 0
    error: str | None = None

    @property
    def silent(self) -> bool:
        """Nothing at all has arrived — a muted or wrong endpoint, not a quiet room."""
        return self.blocks > 0 and self.peak == 0.0


class LevelMonitor:
    """Drains a capture on its own thread and keeps the most recent reading."""

    def __init__(self, capture: AudioCapture, *, decay: float = 0.6) -> None:
        self.capture = capture
        self.decay = decay
        self._reading = Reading()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self.capture.start()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="level-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2.0)
        try:
            self.capture.stop()
        except Exception as exc:  # pragma: no cover - device teardown is best-effort
            log.warning("level monitor could not close its stream: %s", exc)

    def read(self) -> Reading:
        with self._lock:
            return Reading(**vars(self._reading))

    def _loop(self) -> None:
        blocks = 0
        while not self._stop.is_set():
            try:
                payload = self.capture.read(timeout=0.05)
            except Exception as exc:
                with self._lock:
                    self._reading = Reading(error=str(exc), blocks=blocks)
                return
            if payload is None:
                # No block this tick: decay towards zero so the bar falls instead of
                # freezing at the last peak.
                with self._lock:
                    self._reading = Reading(
                        rms=self._reading.rms * self.decay,
                        peak=self._reading.peak * self.decay,
                        blocks=blocks,
                    )
                continue
            blocks += 1
            rms, peak = levels_of(bytes(payload), self.capture.format)
            with self._lock:
                previous = self._reading
                self._reading = Reading(
                    rms=rms,
                    peak=max(peak, previous.peak * self.decay),
                    clipped=peak >= 0.999,
                    blocks=blocks,
                )
        with self._lock:
            self._reading = Reading(blocks=blocks)


def wait_for_signal(monitor: LevelMonitor, seconds: float = 1.0) -> Reading:
    """Poll until a block has been measured — used by tests and the CLI probe."""
    deadline = time.monotonic() + seconds
    reading = monitor.read()
    while time.monotonic() < deadline:
        reading = monitor.read()
        if reading.blocks > 0 or reading.error:
            return reading
        time.sleep(0.01)
    return reading


# ---------------------------------------------------------------- the preview monitors

_lock = threading.Lock()
# One preview stream per track. WASAPI tolerates a single capture stream per endpoint, so
# the meter and the recorder cannot both hold one — but the microphone and the loopback
# are different endpoints and can be metered at the same time.
_active: dict[str, LevelMonitor] = {}
# Which device each running monitor was opened on, and how many meters are reading it.
# Every open page is a client: two tabs on the setup screen share one stream per track
# instead of each closing the other's to open its own.
_devices: dict[str, int | None] = {}
_users: dict[str, int] = {}
# How many preview streams have been opened this run. A healthy Settings page opens one
# per meter; a UI bug that reopens the device on every render shows up here immediately.
_acquisitions = 0


def acquisitions() -> int:
    return _acquisitions


def acquire(config: object, device_index: int | None = None, track: str = "me") -> LevelMonitor:
    """Join the preview monitor running for ``track`` on this device, or start one,
    replacing a monitor on another device or one that has failed."""
    global _acquisitions
    from app.audio.factory import make_capture
    from app.config import Config

    assert isinstance(config, Config)
    with _lock:
        running = _active.get(track)
        if (
            running is not None
            and _devices.get(track) == device_index
            and running.read().error is None
        ):
            _users[track] = _users.get(track, 0) + 1
            return running
        _stop(track)
        _acquisitions += 1
        monitor = LevelMonitor(make_capture(config, track, device_index=device_index))
        monitor.start()
        _active[track] = monitor
        _devices[track] = device_index
        _users[track] = 1
        return monitor


def release(track: str | None = None, monitor: LevelMonitor | None = None) -> None:
    """Let go of a track's meter; its stream closes when the last meter reading it does.

    ``monitor`` is the one the caller joined: if it has since been closed or replaced,
    there is nothing of the caller's left to let go of. With no track, every stream
    closes at once, whoever is reading it. Called before recording arms — the recorder
    must win.
    """
    with _lock:
        if track is None:
            for name in list(_active):
                _stop(name)
            return
        if monitor is not None and _active.get(track) is not monitor:
            return
        _users[track] = _users.get(track, 0) - 1
        if _users[track] <= 0:
            _stop(track)


def _stop(track: str) -> None:
    _users.pop(track, None)
    _devices.pop(track, None)
    monitor = _active.pop(track, None)
    if monitor is not None:
        monitor.stop()
        # Debug, not info: this happens every time Settings is closed or hidden.
        # The one release worth announcing is logged by the recording route.
        log.debug("released the preview stream for %s", track)


def active(track: str = "me") -> LevelMonitor | None:
    return _active.get(track)
