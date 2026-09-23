"""The them track must keep flowing while nothing plays.

WASAPI loopback delivers no packets while its endpoint is silent: machine B's
``test_dual_stream_concurrent`` counted 0 loopback frames in 60 s. The capture therefore
plays silence to the endpoint it listens to. PortAudio cannot run here, so a fake
``pyaudiowpatch`` records what was opened.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from app.audio.devices import DeviceInfo
from app.audio.wasapi import WasapiCapture

WASAPI = 2
SPEAKERS = {
    "index": 7,
    "name": "Speakers (Realtek(R) Audio)",
    "defaultSampleRate": 48000.0,
    "maxInputChannels": 0,
    "maxOutputChannels": 2,
    "hostApi": WASAPI,
}
MME_SPEAKERS = {**SPEAKERS, "index": 3, "hostApi": 0}
LOOPBACK = DeviceInfo(
    index=12,
    name="Speakers (Realtek(R) Audio) [Loopback]",
    rate=48000,
    channels=2,
    is_loopback=True,
    is_input=True,
)
MIC = DeviceInfo(
    index=1, name="Microphone", rate=48000, channels=1, is_loopback=False, is_input=True
)


class FakeStream:
    def __init__(self, kwargs: dict[str, Any]) -> None:
        self.kwargs = kwargs
        self.started = False
        self.closed = False

    def start_stream(self) -> None:
        self.started = True

    def stop_stream(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    def is_active(self) -> bool:
        return not self.closed


class FakeHost:
    fail_output = False

    def __init__(self) -> None:
        self.opened: list[FakeStream] = []

    def get_host_api_info_by_type(self, kind: int) -> dict[str, Any]:
        return {"index": WASAPI}

    def get_device_count(self) -> int:
        return 2

    def get_device_info_by_index(self, index: int) -> dict[str, Any]:
        return [MME_SPEAKERS, SPEAKERS][index]

    def open(self, **kwargs: Any) -> FakeStream:
        if kwargs.get("output") and self.fail_output:
            raise OSError(-9996, "Invalid output device")
        stream = FakeStream(kwargs)
        self.opened.append(stream)
        return stream

    def terminate(self) -> None:
        pass


@pytest.fixture
def hosts(monkeypatch: pytest.MonkeyPatch) -> list[FakeHost]:
    created: list[FakeHost] = []

    def make() -> FakeHost:
        host = FakeHost()
        created.append(host)
        return host

    module = types.SimpleNamespace(PyAudio=make, paFloat32=1, paContinue=0, paWASAPI=13)
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", module)
    FakeHost.fail_output = False
    return created


def test_loopback_plays_silence_to_its_own_endpoint(hosts: list[FakeHost]) -> None:
    capture = WasapiCapture("them", device=LOOPBACK)
    capture.start()
    opened = hosts[0].opened
    assert [s.kwargs.get("output", False) for s in opened] == [False, True]
    keepalive = opened[1]
    assert keepalive.started
    assert keepalive.kwargs["output_device_index"] == SPEAKERS["index"], "not the MME twin"
    assert keepalive.kwargs["channels"] == 2 and keepalive.kwargs["rate"] == 48000

    payload, flag = keepalive.kwargs["stream_callback"](None, 480, {}, 0)
    assert payload == bytes(480 * 2 * 4) and flag == 0
    payload, _ = keepalive.kwargs["stream_callback"](None, 9600, {}, 0)
    assert len(payload) == 9600 * 2 * 4, "a block larger than asked for is still whole"

    capture.stop()
    assert all(s.closed for s in opened)


def test_the_microphone_needs_no_keepalive(hosts: list[FakeHost]) -> None:
    capture = WasapiCapture("me", device=MIC)
    capture.start()
    assert [s.kwargs.get("output", False) for s in hosts[0].opened] == [False]
    capture.stop()


def test_a_keepalive_that_cannot_open_does_not_stop_the_recording(
    hosts: list[FakeHost],
) -> None:
    FakeHost.fail_output = True
    capture = WasapiCapture("them", device=LOOPBACK)
    capture.start()
    assert capture.running and capture._keepalive is None
    capture.stop()
