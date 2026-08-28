"""WASAPI capture over PyAudioWPatch (TECHNICAL-DESIGN.md §4.1–§4.2).

The callback body is exactly "copy the bytes into a bounded queue and return". No numpy,
no file I/O, no logging: PortAudio's callback thread has a hard deadline and anything
else there produces dropped frames, which is unrecoverable data loss.
"""

from __future__ import annotations

import contextlib
import queue
import time
from typing import Any

from app.audio.capture import AudioFormat, CaptureStats, StreamError
from app.audio.devices import DeviceInfo, resolve_track
from app.log import get

log = get(__name__)


class WasapiCapture:
    """One WASAPI stream — the microphone, or a render endpoint's loopback."""

    def __init__(
        self,
        track: str = "them",
        *,
        queue_seconds: float = 10.0,
        device: DeviceInfo | None = None,
        block_frames: int = 4800,
    ) -> None:
        self.track = track
        self.device = device
        self.block_frames = block_frames
        self.queue_seconds = queue_seconds
        self.format = AudioFormat()
        self.stats = CaptureStats()
        self.frames: queue.Queue[bytes] = queue.Queue(maxsize=1)
        self._host: Any = None
        self._stream: Any = None
        self.running = False

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        import pyaudiowpatch as pyaudio

        device = self.device or resolve_track(self.track)
        self.device = device
        # Open at the endpoint's native mix format: a format WASAPI has to convert
        # invites silent failures.
        self.format = AudioFormat(rate=device.rate, channels=device.channels, dtype="float32")
        maxsize = max(2, int(self.queue_seconds * device.rate / self.block_frames))
        self.frames = queue.Queue(maxsize=maxsize)
        self._host = pyaudio.PyAudio()
        try:
            self._stream = self._host.open(
                format=pyaudio.paFloat32,
                channels=device.channels,
                rate=device.rate,
                frames_per_buffer=self.block_frames,
                input=True,
                input_device_index=device.index,
                stream_callback=self._callback,
            )
        except OSError as exc:
            self._host.terminate()
            self._host = None
            raise StreamError(f"cannot open {self.track} stream on {device.name!r}: {exc}") from exc
        self.running = True
        self._stream.start_stream()
        log.info(
            "opened %s stream: %s @ %d Hz, %d ch",
            self.track,
            device.name,
            device.rate,
            device.channels,
        )

    def stop(self) -> None:
        self.running = False
        stream, self._stream = self._stream, None
        host, self._host = self._host, None
        if stream is not None:
            with contextlib.suppress(Exception):
                stream.stop_stream()
            with contextlib.suppress(Exception):
                stream.close()
        if host is not None:
            host.terminate()

    # -- the callback ------------------------------------------------------

    def _callback(
        self, in_data: bytes, frame_count: int, time_info: dict[str, Any], status: int
    ) -> tuple[bytes | None, int]:
        import pyaudiowpatch as pyaudio

        if status:
            self.stats.xruns += 1
        if self.stats.started_mono is None:
            self.stats.started_mono = time.perf_counter_ns() / 1e9
        try:
            self.frames.put_nowait(in_data)
        except queue.Full:
            self.stats.dropped += frame_count
        self.stats.frames += frame_count
        self.stats.blocks += 1
        return (None, pyaudio.paContinue)

    # -- reading -----------------------------------------------------------

    def read(self, timeout: float = 0.1) -> bytes | None:
        try:
            return self.frames.get(timeout=timeout)
        except queue.Empty:
            if self.running and not self.is_alive():
                raise StreamError(f"{self.track} stream stopped unexpectedly") from None
            return None

    def is_alive(self) -> bool:
        stream = self._stream
        if stream is None:
            return False
        try:
            return bool(stream.is_active())
        except Exception:
            return False
