"""Every PortAudio host lives on one thread.

PortAudio's WASAPI host binds to the thread that initialised it, and Pa_Initialize is
reference-counted: while any PyAudio instance is alive, an input stream opened on another
thread fails with -9999 ("Unanticipated host error"). On machine B (job 012) the setup
screen's two meters and its device listing ran on different server threads, so both
meters failed, and recording opened the same devices fine minutes later. Reproduced on
machine A with the real library. The fake below enforces the same rule.
"""

from __future__ import annotations

import sys
import threading
import types
from typing import Any

import pytest

from app.audio.devices import DeviceInfo, audio_host, list_inputs
from app.audio.wasapi import WasapiCapture

MIC = DeviceInfo(
    index=1, name="Microphone", rate=48000, channels=1, is_loopback=False, is_input=True
)
RAW_MIC = {
    "index": 1,
    "name": "Microphone",
    "defaultSampleRate": 48000.0,
    "maxInputChannels": 1,
    "maxOutputChannels": 0,
    "hostApi": 2,
}


class PortAudio:
    """Reference-counted initialisation, bound to the thread that initialised it."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.refs = 0
        self.owner: int | None = None
        self.failures: list[str] = []
        self.opened = 0


class FakeStream:
    def start_stream(self) -> None:
        pass

    def stop_stream(self) -> None:
        pass

    def close(self) -> None:
        pass

    def is_active(self) -> bool:
        return True


class FakeHost:
    def __init__(self, pa: PortAudio) -> None:
        self.pa = pa
        with pa.lock:
            if pa.refs == 0:
                pa.owner = threading.get_ident()
            pa.refs += 1

    def get_device_count(self) -> int:
        return 1

    def get_device_info_by_index(self, index: int) -> dict[str, Any]:
        return RAW_MIC

    def open(self, **kwargs: Any) -> FakeStream:
        if kwargs.get("input") and threading.get_ident() != self.pa.owner:
            self.pa.failures.append(threading.current_thread().name)
            raise OSError(-9999, "Unanticipated host error")
        self.pa.opened += 1
        return FakeStream()

    def terminate(self) -> None:
        with self.pa.lock:
            self.pa.refs -= 1
            if self.pa.refs == 0:
                self.pa.owner = None


@pytest.fixture
def pa(monkeypatch: pytest.MonkeyPatch) -> PortAudio:
    state = PortAudio()
    module = types.SimpleNamespace(
        PyAudio=lambda: FakeHost(state), paFloat32=1, paContinue=0, paWASAPI=13
    )
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", module)
    monkeypatch.setattr("app.audio.wasapi.OPEN_RETRY_S", 0.0)
    return state


def test_a_stream_opens_while_another_thread_holds_a_host(pa: PortAudio) -> None:
    """A device listing on one request thread, a meter opening on another."""
    listing_holds = threading.Event()
    meter_done = threading.Event()

    def listing() -> None:
        with audio_host():
            listing_holds.set()
            meter_done.wait(5)

    holder = threading.Thread(target=listing, name="listing")
    holder.start()
    assert listing_holds.wait(5)
    capture = WasapiCapture("me", device=MIC)
    try:
        capture.start()
    finally:
        meter_done.set()
        holder.join(5)
    capture.stop()
    assert pa.failures == []
    assert pa.opened == 1


def test_both_meters_open_from_different_threads(pa: PortAudio) -> None:
    """The setup screen: the me and them meters start on two server threads at once,
    while a third lists devices."""
    captures = [WasapiCapture("me", device=MIC), WasapiCapture("them", device=MIC)]
    errors: list[BaseException] = []
    barrier = threading.Barrier(3)

    def meter(capture: WasapiCapture) -> None:
        barrier.wait(5)
        try:
            capture.start()
        except BaseException as exc:  # reported below
            errors.append(exc)

    def lister() -> None:
        barrier.wait(5)
        list_inputs()

    threads = [threading.Thread(target=meter, args=(c,)) for c in captures]
    threads.append(threading.Thread(target=lister))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    for capture in captures:
        capture.stop()
    assert errors == [] and pa.failures == []
    assert pa.refs == 0, "a host was left initialised"
