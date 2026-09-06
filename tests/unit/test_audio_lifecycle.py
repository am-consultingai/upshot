"""The stream lifecycle must be race-free.

A real Windows run crashed with 0xc0000005 inside `_portaudiowpatch.pyd`: the writer
thread called `is_active()` on a stream the stop path had already closed and whose
PortAudio host it had terminated. That is a native access violation, so no `except`
catches it and nothing reaches the log — the process simply vanishes mid-meeting.

PortAudio itself cannot be exercised here, so these tests drive the same code paths with
stubs that report use-after-free instead of crashing.
"""

from __future__ import annotations

import threading

from app.audio.wasapi import WasapiCapture


class Stub:
    """A stream/host that records any use after it was closed."""

    def __init__(self) -> None:
        self.closed = False
        self.violations: list[str] = []
        self.calls = 0

    def _use(self, what: str) -> None:
        if self.closed:
            self.violations.append(what)
        self.calls += 1

    # stream
    def is_active(self) -> bool:
        self._use("is_active")
        return True

    def stop_stream(self) -> None:
        self._use("stop_stream")

    def close(self) -> None:
        self._use("close")
        self.closed = True

    # host
    def terminate(self) -> None:
        self._use("terminate")
        self.closed = True


def _race_once() -> tuple[Stub, Stub]:
    capture = WasapiCapture(track="me")
    stream, host = Stub(), Stub()
    capture._stream = stream
    capture._host = host
    capture.running = True

    stop_now = threading.Event()
    polling = threading.Event()

    def poll() -> None:
        polling.set()
        while not stop_now.is_set():
            capture.is_alive()

    reader = threading.Thread(target=poll, daemon=True)
    reader.start()
    assert polling.wait(2)
    capture.stop()
    stop_now.set()
    reader.join(timeout=2)
    return stream, host


def test_is_alive_cannot_touch_a_stream_being_closed() -> None:
    for _ in range(20):  # the window is small; try repeatedly
        stream, host = _race_once()
        assert stream.violations == [], f"used after close: {stream.violations}"
        assert host.violations == [], f"used after terminate: {host.violations}"
        assert stream.calls > 0, "the stub was never exercised"


def test_stop_is_idempotent_under_concurrency() -> None:
    """Two threads stopping at once must not double-terminate the host."""
    capture = WasapiCapture(track="them")
    stream, host = Stub(), Stub()
    capture._stream = stream
    capture._host = host
    capture.running = True

    threads = [threading.Thread(target=capture.stop) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert stream.violations == []
    assert host.violations == []
    assert capture.running is False
