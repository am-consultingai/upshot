"""WASAPI capture over PyAudioWPatch (TECHNICAL-DESIGN.md §4.1–§4.2).

The callback body is exactly "copy the bytes into a bounded queue and return". No numpy,
no file I/O, no logging: PortAudio's callback thread has a hard deadline and anything
else there produces dropped frames, which is unrecoverable data loss.
"""

from __future__ import annotations

import contextlib
import queue
import threading
import time
from typing import Any

from app.audio.capture import AudioFormat, CaptureStats, StreamError
from app.audio.devices import PORTAUDIO_LOCK, DeviceInfo, resolve_track
from app.log import get

log = get(__name__)

OPEN_ATTEMPTS = 4
OPEN_RETRY_S = 0.25


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
        # Guards this stream's handle. Held across close, so no other thread can be
        # inside a PortAudio call on a stream that is being freed.
        self._lifecycle = threading.RLock()
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
        # Whole open under both locks, in the same order stop() takes them: PortAudio
        # must not be initialising a host on one thread while another terminates one.
        with self._lifecycle, PORTAUDIO_LOCK:
            self._host = pyaudio.PyAudio()
            # An endpoint that was open a moment ago is not instantly reopenable: WASAPI
            # reports -9999 ("Unanticipated host error") while the previous client's
            # stream is still tearing down. Handing the microphone from the settings
            # meter to the recorder hits exactly that window, so retry before giving up.
            last: OSError | None = None
            for attempt in range(OPEN_ATTEMPTS):
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
                    last = None
                    break
                except OSError as exc:
                    last = exc
                    if attempt + 1 < OPEN_ATTEMPTS:
                        log.warning(
                            "%s stream on %r did not open (%s); retrying",
                            self.track,
                            device.name,
                            exc,
                        )
                        time.sleep(OPEN_RETRY_S)
            if last is not None:
                self._host.terminate()
                self._host = None
                raise StreamError(
                    f"cannot open {self.track} stream on {device.name!r}: {last}"
                ) from last
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
        with self._lifecycle, PORTAUDIO_LOCK:
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
        # Under the lifecycle lock: reading `self._stream` and then calling into it is
        # only safe while stop() cannot run in between. Without this the writer thread
        # calls is_active() on a stream the stop path has already closed, which is a
        # native crash rather than a Python exception.
        with self._lifecycle:
            stream = self._stream
            if stream is None:
                return False
            try:
                return bool(stream.is_active())
            except Exception:
                return False
