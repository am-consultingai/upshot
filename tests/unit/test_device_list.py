"""The device lists offer WASAPI endpoints only.

PortAudio lists every device once per host API. On machine B (job 013) the MME copies
came first — names cut to 31 characters, plus "Microsoft Sound Mapper" and "Primary Sound
Capture Driver" — and the name-based deduplication kept them, so the WASAPI default was
never in the list and nothing was marked default.
"""

from __future__ import annotations

from typing import Any

from app.audio.devices import default_capture, default_render, list_inputs, list_outputs

MME, DIRECTSOUND, WASAPI = 0, 1, 2


def raw(
    index: int, name: str, host_api: int, *, inputs: int = 0, outputs: int = 0
) -> dict[str, Any]:
    return {
        "index": index,
        "name": name,
        "defaultSampleRate": 48000.0,
        "maxInputChannels": inputs,
        "maxOutputChannels": outputs,
        "hostApi": host_api,
    }


DEVICES = [
    raw(0, "Microsoft Sound Mapper - Input", MME, inputs=2),
    raw(1, "Microphone Array (Intel® Smart ", MME, inputs=2),
    raw(2, "Primary Sound Capture Driver", DIRECTSOUND, inputs=2),
    raw(3, "Speakers (Realtek(R) Audio)", MME, outputs=2),
    raw(
        4,
        "Microphone Array (Intel® Smart Sound Technology for Digital Microphones)",
        WASAPI,
        inputs=2,
    ),
    raw(5, "Speakers (Realtek(R) Audio)", WASAPI, outputs=2),
    {
        **raw(6, "Speakers (Realtek(R) Audio) [Loopback]", WASAPI, inputs=2),
        "isLoopbackDevice": True,
    },
]


class FakeHost:
    def get_host_api_info_by_type(self, kind: int) -> dict[str, Any]:
        return {"index": WASAPI, "defaultInputDevice": 4, "defaultOutputDevice": 5}

    def get_device_count(self) -> int:
        return len(DEVICES)

    def get_device_info_by_index(self, index: int) -> dict[str, Any]:
        return DEVICES[index]


def test_only_wasapi_microphones_are_offered(monkeypatch: Any) -> None:
    import sys
    import types

    monkeypatch.setitem(sys.modules, "pyaudiowpatch", types.SimpleNamespace(paWASAPI=13))
    host = FakeHost()
    inputs = list_inputs(host)
    assert [device.index for device in inputs] == [4]
    assert default_capture(host).index in {device.index for device in inputs}


def test_only_wasapi_speakers_are_offered(monkeypatch: Any) -> None:
    import sys
    import types

    monkeypatch.setitem(sys.modules, "pyaudiowpatch", types.SimpleNamespace(paWASAPI=13))
    host = FakeHost()
    outputs = list_outputs(host)
    assert [device.index for device in outputs] == [5], "not the MME twin of the same name"
    assert default_render(host).index == outputs[0].index
