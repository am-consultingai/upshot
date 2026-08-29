"""Which capture implementation is wired — chosen by config, never by patching."""

from __future__ import annotations

from pathlib import Path

from app.audio.capture import AudioCapture, AudioFormat
from app.config import Config


def make_capture(config: Config, track: str, *, fixture: Path | None = None) -> AudioCapture:
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
        from app.audio.wasapi import WasapiCapture

        capture: AudioCapture = WasapiCapture(track=track, queue_seconds=queue_seconds)
        return capture
    raise ValueError(f"unknown audio.capture {kind!r}")


def device_format(config: Config) -> AudioFormat:
    return AudioFormat(
        rate=int(config.get("audio.device_rate", 48000)),
        channels=int(config.get("audio.device_channels", 2)),
    )
