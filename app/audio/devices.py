"""WASAPI endpoint enumeration (TECHNICAL-DESIGN.md §4.1).

Every PyAudioWPatch import is inside a function: this module must stay importable on a
machine with no audio stack at all, which is what ``test_imports_all_modules`` asserts.
"""

from __future__ import annotations

import threading
import wave
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.log import get

log = get(__name__)


# PortAudio's Pa_Initialize/Pa_Terminate are reference-counted but NOT thread-safe, and
# a stream handle must not be touched while another thread is closing it. Both happen
# here: the recorder tears its streams down while the settings meter opens one, and the
# writer thread polls a stream the stop path is freeing. Either race crashes the process
# inside _portaudiowpatch with 0xc0000005 — a native access violation, so no Python
# `except` can catch it and nothing reaches the log. Every open/close goes through this.
PORTAUDIO_LOCK = threading.RLock()


class NoDeviceError(RuntimeError):
    """No usable endpoint. Recording cannot start; the UI must say why."""


@dataclass(frozen=True)
class DeviceInfo:
    index: int
    name: str
    rate: int
    channels: int
    is_loopback: bool
    is_input: bool
    max_output_channels: int = 0

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> DeviceInfo:
        return cls(
            index=int(raw["index"]),
            name=str(raw["name"]),
            rate=int(raw["defaultSampleRate"]),
            channels=int(raw["maxInputChannels"]) or int(raw["maxOutputChannels"]),
            is_loopback=bool(raw.get("isLoopbackDevice", False)),
            is_input=int(raw["maxInputChannels"]) > 0,
            max_output_channels=int(raw["maxOutputChannels"]),
        )


def _pyaudio_module() -> Any:
    try:
        import pyaudiowpatch as pyaudio
    except ImportError as exc:  # pragma: no cover - Windows-only wheel
        raise NoDeviceError(
            "Recording only works on the Windows build. This copy is running on "
            "another system, where Upshot cannot open the microphone or capture "
            "what the computer is playing."
        ) from exc
    return pyaudio


@contextmanager
def audio_host() -> Iterator[Any]:
    """A PyAudio instance that is always terminated."""
    pyaudio = _pyaudio_module()
    with PORTAUDIO_LOCK:
        host = pyaudio.PyAudio()
    try:
        yield host
    finally:
        with PORTAUDIO_LOCK:
            host.terminate()


def _wasapi_info(host: Any) -> dict[str, Any]:
    pyaudio = _pyaudio_module()
    try:
        info = host.get_host_api_info_by_type(pyaudio.paWASAPI)
    except OSError as exc:
        raise NoDeviceError("WASAPI host API is unavailable") from exc
    return dict(info)


def list_devices() -> list[DeviceInfo]:
    with audio_host() as host:
        return [
            DeviceInfo.from_raw(dict(host.get_device_info_by_index(index)))
            for index in range(host.get_device_count())
        ]


def list_inputs(host: Any | None = None) -> list[DeviceInfo]:
    """Real microphones: input endpoints that are not loopback companions.

    Deduplicated by name — WASAPI commonly exposes the same physical microphone several
    times, and a dropdown with four identical entries is worse than useless.
    """
    if host is None:
        with audio_host() as own:
            return list_inputs(own)
    seen: dict[str, DeviceInfo] = {}
    for index in range(host.get_device_count()):
        device = DeviceInfo.from_raw(dict(host.get_device_info_by_index(index)))
        if not device.is_input or device.is_loopback:
            continue
        seen.setdefault(device.name, device)
    return list(seen.values())


def input_by_index(index: int, host: Any | None = None) -> DeviceInfo:
    if host is None:
        with audio_host() as own:
            return input_by_index(index, own)
    try:
        device = DeviceInfo.from_raw(dict(host.get_device_info_by_index(index)))
    except OSError as exc:
        raise NoDeviceError(f"no audio endpoint with index {index}") from exc
    if not device.is_input:
        raise NoDeviceError(f"endpoint {index} ({device.name!r}) is not an input")
    return device


def list_outputs(host: Any | None = None) -> list[DeviceInfo]:
    """Playback endpoints, deduplicated by name. The loopback of the chosen one is what
    the ``them`` track records."""
    if host is None:
        with audio_host() as own:
            return list_outputs(own)
    seen: dict[str, DeviceInfo] = {}
    for index in range(host.get_device_count()):
        device = DeviceInfo.from_raw(dict(host.get_device_info_by_index(index)))
        if device.max_output_channels <= 0 or device.is_loopback:
            continue
        seen.setdefault(device.name, device)
    return list(seen.values())


def output_by_index(index: int, host: Any | None = None) -> DeviceInfo:
    if host is None:
        with audio_host() as own:
            return output_by_index(index, own)
    try:
        device = DeviceInfo.from_raw(dict(host.get_device_info_by_index(index)))
    except OSError as exc:
        raise NoDeviceError(f"no audio endpoint with index {index}") from exc
    if device.max_output_channels <= 0:
        raise NoDeviceError(f"endpoint {index} ({device.name!r}) is not a playback device")
    return device


def default_render(host: Any | None = None) -> DeviceInfo:
    if host is None:
        with audio_host() as own:
            return default_render(own)
    info = _wasapi_info(host)
    index = int(info["defaultOutputDevice"])
    if index < 0:
        raise NoDeviceError("no default render endpoint")
    return DeviceInfo.from_raw(dict(host.get_device_info_by_index(index)))


def default_capture(host: Any | None = None) -> DeviceInfo:
    if host is None:
        with audio_host() as own:
            return default_capture(own)
    info = _wasapi_info(host)
    index = int(info["defaultInputDevice"])
    if index < 0:
        raise NoDeviceError("no default capture endpoint (no microphone)")
    return DeviceInfo.from_raw(dict(host.get_device_info_by_index(index)))


def loopback_for(render: DeviceInfo, host: Any | None = None) -> DeviceInfo:
    """The loopback companion of a render endpoint — how we hear everyone else."""
    if host is None:
        with audio_host() as own:
            return loopback_for(render, own)
    if render.is_loopback:
        return render
    for raw in host.get_loopback_device_info_generator():
        candidate = DeviceInfo.from_raw(dict(raw))
        if render.name in candidate.name:
            return candidate
    for raw in host.get_loopback_device_info_generator():  # any loopback is better than none
        return DeviceInfo.from_raw(dict(raw))
    raise NoDeviceError(f"no loopback companion for {render.name!r}")


def render_for(loopback: DeviceInfo, host: Any) -> DeviceInfo:
    """The WASAPI render endpoint a loopback companion listens to: loopback_for, reversed."""
    wasapi = int(_wasapi_info(host)["index"])
    for index in range(host.get_device_count()):
        raw = dict(host.get_device_info_by_index(index))
        candidate = DeviceInfo.from_raw(raw)
        if candidate.is_loopback or candidate.max_output_channels <= 0:
            continue
        if int(raw.get("hostApi", -1)) == wasapi and candidate.name in loopback.name:
            return candidate
    raise NoDeviceError(f"no render endpoint behind {loopback.name!r}")


def resolve_track(
    track: str, host: Any | None = None, *, output_index: int | None = None
) -> DeviceInfo:
    """``me`` is the default capture endpoint; ``them`` is a render endpoint's loopback.

    ``output_index`` selects which playback device to listen to; without it, whatever
    Windows currently calls the default.
    """
    if host is None:
        with audio_host() as own:
            return resolve_track(track, own, output_index=output_index)
    if track == "me":
        return default_capture(host)
    if track == "them":
        render = (
            default_render(host) if output_index is None else output_by_index(output_index, host)
        )
        return loopback_for(render, host)
    raise ValueError(f"unknown track {track!r}")


def play_wav(path: Path, *, device_index: int | None = None, blocking: bool = True) -> float:
    """Play a WAV out the default render endpoint — the source half of the T2 echo test.

    Returns the duration played, in seconds.
    """
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        frames = handle.getnframes()
        payload = handle.readframes(frames)
    with audio_host() as host:
        stream = host.open(
            format=host.get_format_from_width(width),
            channels=channels,
            rate=rate,
            output=True,
            output_device_index=device_index,
        )
        try:
            block = 4096 * channels * width
            for offset in range(0, len(payload), block):
                stream.write(payload[offset : offset + block])
            if blocking:
                stream.stop_stream()
        finally:
            stream.close()
    return frames / float(rate)
