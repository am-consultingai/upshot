"""``SyntheticCapture`` — deterministic audio with no hardware (EXECUTION-PLAN Phase 3).

Emits the device's native format (48 kHz float32 stereo by default) so everything below
the ``AudioCapture`` protocol is exercised exactly as it is in production.
"""

from __future__ import annotations

import queue
import threading
import time
import wave
from collections.abc import Callable
from pathlib import Path

import numpy as np

from app.audio.capture import AudioFormat, CaptureStats, StreamError

Pattern = str | Callable[[int, int], np.ndarray]


def tone_block(
    start_frame: int, frames: int, *, rate: int = 48000, freq: float = 440.0, amplitude: float = 0.3
) -> np.ndarray:
    index = np.arange(start_frame, start_frame + frames, dtype=np.float64)
    return (amplitude * np.sin(2 * np.pi * freq * index / rate)).astype(np.float32)


def silence_block(start_frame: int, frames: int) -> np.ndarray:
    return np.zeros(frames, dtype=np.float32)


class SyntheticCapture:
    """A capture stream whose content is a pure function of the frame index."""

    def __init__(
        self,
        track: str = "them",
        pattern: Pattern = "silence",
        *,
        fmt: AudioFormat | None = None,
        block_frames: int = 4800,
        queue_seconds: float = 10.0,
        wav: Path | None = None,
        loop_wav: bool = True,
        generate_on_read: bool = True,
        realtime: bool = False,
    ) -> None:
        self.track = track
        self.format = fmt or AudioFormat()
        self.block_frames = block_frames
        self.pattern = pattern
        self.stats = CaptureStats()
        maxsize = max(1, int(queue_seconds * self.format.rate / block_frames))
        self.frames: queue.Queue[bytes] = queue.Queue(maxsize=maxsize)
        self.position = 0
        self._glitch_at: int | None = None
        self._glitched = False
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wav: np.ndarray | None = None
        self.loop_wav = loop_wav
        self.generate_on_read = generate_on_read
        # Tests want audio as fast as the machine allows; a person running the app with
        # `audio.capture = "synthetic"` wants a minute of audio to take a minute.
        self.realtime = realtime
        self._next_block_at: float | None = None
        if wav is not None:
            self._wav = self._load_wav(wav)
        self.running = False

    # -- fixture loading

    def _load_wav(self, path: Path) -> np.ndarray:
        with wave.open(str(path), "rb") as handle:
            rate = handle.getframerate()
            channels = handle.getnchannels()
            raw = handle.readframes(handle.getnframes())
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if channels > 1:
            audio = audio[: len(audio) // channels * channels].reshape(-1, channels).mean(axis=1)
        if rate != self.format.rate:
            import soxr

            audio = np.asarray(soxr.resample(audio, rate, self.format.rate), dtype=np.float32)
        return audio.astype(np.float32)

    # -- control

    def glitch_after(self, blocks: int) -> None:
        """Make the stream fail like a yanked USB headset does."""
        self._glitch_at = blocks
        self._glitched = False

    def start(self) -> None:
        self.running = True
        self.stats.started_mono = 0.0

    def stop(self) -> None:
        self.running = False
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def read(self, timeout: float = 0.1) -> bytes | None:
        """Pull one block.

        When ``generate_on_read`` is set (the default) an empty queue produces the next
        deterministic block inline, so a test drives generation and consumption in one
        thread and a glitch surfaces to the recorder exactly as a device error does.
        """
        try:
            return self.frames.get_nowait()
        except queue.Empty:
            pass
        if self.running and self.generate_on_read:
            if self.realtime:
                self._pace()
            return self.block()
        try:
            return self.frames.get(timeout=timeout)
        except queue.Empty:
            return None

    def _pace(self) -> None:
        """Hold one block's worth of wall time, so synthetic audio runs at 1×."""
        interval = self.block_frames / self.format.rate
        now = time.monotonic()
        if self._next_block_at is None:
            self._next_block_at = now + interval
            return
        delay = self._next_block_at - now
        if delay > 0:
            time.sleep(delay)
        self._next_block_at = max(now, self._next_block_at) + interval

    # -- generation

    def _mono(self, frames: int) -> np.ndarray:
        start = self.position
        if self._wav is not None:
            if self.loop_wav:
                index = (np.arange(start, start + frames) % len(self._wav)).astype(np.int64)
                return self._wav[index]
            end = min(len(self._wav), start + frames)
            block = np.zeros(frames, dtype=np.float32)
            if start < len(self._wav):
                block[: end - start] = self._wav[start:end]
            return block
        if callable(self.pattern):
            return np.asarray(self.pattern(start, frames), dtype=np.float32)
        if self.pattern == "tone":
            return tone_block(start, frames, rate=self.format.rate)
        if self.pattern == "silence":
            return silence_block(start, frames)
        raise ValueError(f"unknown pattern {self.pattern!r}")

    def block(self, frames: int | None = None) -> bytes:
        """Produce one device block. This is what a PortAudio callback would hand over."""
        frames = frames or self.block_frames
        if (
            self._glitch_at is not None
            and not self._glitched
            and self.stats.blocks >= self._glitch_at
        ):
            self._glitched = True
            self.stats.errors.append("synthetic stream error")
            raise StreamError("synthetic device change")
        mono = self._mono(frames)
        if self.format.channels > 1:
            data = np.repeat(mono[:, None], self.format.channels, axis=1).reshape(-1)
        else:
            data = mono
        self.position += frames
        self.stats.frames += frames
        self.stats.blocks += 1
        return data.astype(np.float32).tobytes()

    def emit(self, frames: int | None = None) -> bool:
        """Callback behaviour: copy bytes into the queue and return. Never blocks."""
        payload = self.block(frames)
        try:
            self.frames.put_nowait(payload)
        except queue.Full:
            self.stats.dropped += frames or self.block_frames
            return False
        return True

    def pump(self, seconds: float) -> int:
        """Generate ``seconds`` of audio as fast as the machine allows (soak tests)."""
        blocks = int(seconds * self.format.rate / self.block_frames)
        emitted = 0
        for _ in range(blocks):
            if self.emit():
                emitted += 1
        return emitted

    def run_realtime(self, speed: float = 1.0) -> threading.Thread:
        interval = self.block_frames / self.format.rate / speed

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    self.emit()
                except StreamError:
                    return
                self._stop.wait(interval)

        thread = threading.Thread(target=loop, name=f"synthetic-{self.track}", daemon=True)
        self._thread = thread
        thread.start()
        return thread
