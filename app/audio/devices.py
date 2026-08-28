"""WASAPI endpoint enumeration (TECHNICAL-DESIGN.md §4.1).

Every PyAudioWPatch import is inside a function: this module must stay importable on a
machine with no audio stack at all, which is what ``test_imports_all_modules`` asserts.
"""

from __future__ import annotations

import wave
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.log import get

log = get(__name__)


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
            "PyAudioWPatch is not installed — audio capture needs the Windows build"
        ) from exc
    return pyaudio


@contextmanager
def audio_host() -> Iterator[Any]:
    """A PyAudio instance that is always terminated."""
    pyaudio = _pyaudio_module()
    host = pyaudio.PyAudio()
    try:
        yield host
    finally:
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


def resolve_track(track: str, host: Any | None = None) -> DeviceInfo:
    """``me`` is the default capture endpoint; ``them`` is the render loopback."""
    if host is None:
        with audio_host() as own:
            return resolve_track(track, own)
    if track == "me":
        return default_capture(host)
    if track == "them":
        return loopback_for(default_render(host), host)
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
