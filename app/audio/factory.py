"""Which capture implementation is wired — chosen by config, never by patching."""

from __future__ import annotations

from pathlib import Path

from app.audio.capture import AudioCapture, AudioFormat
from app.config import Config
from app.log import get

log = get(__name__)


def make_capture(
    config: Config,
    track: str,
    *,
    fixture: Path | None = None,
    device_index: int | None = None,
) -> AudioCapture:
    kind = str(config.get("audio.capture", "wasapi"))
    queue_seconds = float(config.get("audio.queue_seconds", 10))
    if kind == "synthetic":
        from app.audio.fake import SyntheticCapture

        pattern = str(config.get("audio.synthetic_pattern", "silence"))
        return SyntheticCapture(
            track=track,
            pattern=pattern,
            queue_seconds=queue_seconds,
            wav=fixture,
            realtime=bool(config.get("audio.synthetic_realtime", True)),
        )
    if kind == "wasapi":
        from app.audio.devices import NoDeviceError, input_by_index, resolve_track
        from app.audio.wasapi import WasapiCapture

        device = None
        key = "audio.input_device" if track == "me" else "audio.output_device"
        configured = config.get(key) if device_index is None else device_index
        if configured is not None:
            try:
                # A microphone is opened directly; a playback endpoint is opened through
                # its loopback companion, which is a different device entirely.
                device = (
                    input_by_index(int(configured))
                    if track == "me"
                    else resolve_track("them", output_index=int(configured))
                )
            except (NoDeviceError, ValueError) as exc:
                # A device can vanish between settings and a meeting. Falling back to the
                # system default records something; refusing to start records nothing.
                log.warning("configured %s device unusable (%s); using the default", track, exc)
        capture: AudioCapture = WasapiCapture(
            track=track, queue_seconds=queue_seconds, device=device
        )
        return capture
    raise ValueError(f"unknown audio.capture {kind!r}")


def device_format(config: Config) -> AudioFormat:
    return AudioFormat(
        rate=int(config.get("audio.device_rate", 48000)),
        channels=int(config.get("audio.device_channels", 2)),
    )
