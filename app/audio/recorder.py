"""The recorder: two streams, two rings, one writer thread.

Recording is sacred (DESIGN.md §4): audio goes to disk continuously as chunk files, the
callbacks only copy bytes, and a device change costs a 300 ms hole rather than a meeting.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from app.audio.capture import TRACKS, AudioCapture, StreamError
from app.audio.ring import PreRollRing
from app.audio.writer import ChunkRecord, ChunkWriter, Resampler
from app.clock import Clock, SystemClock
from app.config import Config
from app.log import get

log = get(__name__)

CaptureFactory = Callable[[str], AudioCapture]


@dataclass
class TrackRuntime:
    track: str
    capture: AudioCapture
    resampler: Resampler
    ring: PreRollRing
    level: float = 0.0
    reopened: int = 0
    gap_ms: int = 0


@dataclass
class RecordingResult:
    folder: Path | None
    records: list[ChunkRecord] = field(default_factory=list)
    duration_ms: dict[str, int] = field(default_factory=dict)
    dropped: dict[str, int] = field(default_factory=dict)
    xruns: dict[str, int] = field(default_factory=dict)
    gaps_ms: dict[str, int] = field(default_factory=dict)

    @property
    def total_duration_ms(self) -> int:
        return max(self.duration_ms.values()) if self.duration_ms else 0


class Recorder:
    """Armed → (committed | discarded). Nothing reaches disk before commit."""

    def __init__(
        self,
        config: Config,
        capture_factory: CaptureFactory,
        *,
        clock: Clock | None = None,
        tracks: tuple[str, ...] = TRACKS,
        min_gap_ms: int = 100,
    ) -> None:
        self.config = config
        self.capture_factory = capture_factory
        self.clock = clock or SystemClock()
        self.tracks = tracks
        self.min_gap_ms = min_gap_ms
        self.rate = config.sample_rate
        self.runtime: dict[str, TrackRuntime] = {}
        self.writer: ChunkWriter | None = None
        self.folder: Path | None = None
        self.meeting_id: str | None = None
        self.armed = False
        self.committed = False
        self.paused = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()

    # -- lifecycle ---------------------------------------------------------

    def arm(self) -> None:
        """Tier 1: open both streams and start the pre-roll. Zero files on disk."""
        if self.armed:
            return
        preroll_s = float(self.config.preroll_s)
        for track in self.tracks:
            capture = self.capture_factory(track)
            capture.start()
            self.runtime[track] = TrackRuntime(
                track=track,
                capture=capture,
                resampler=Resampler(
                    in_rate=capture.format.rate,
                    out_rate=self.rate,
                    channels=capture.format.channels,
                ),
                ring=PreRollRing(preroll_s, self.rate),
            )
        self.armed = True
        log.info("recorder armed (pre-roll %.0fs, tracks %s)", preroll_s, ",".join(self.tracks))

    def commit(self, folder: Path, meeting_id: str | None = None) -> None:
        """Tier 3: create the folder, flush the pre-roll as chunk 0001, keep going."""
        if not self.armed:
            self.arm()
        if self.committed:
            return
        self.folder = Path(folder)
        self.meeting_id = meeting_id
        self.writer = ChunkWriter(
            self.folder,
            tracks=self.tracks,
            rate=self.rate,
            chunk_s=float(self.config.chunk_s),
            silence_search_s=float(self.config.get("audio.silence_search_s", 10)),
            hard_cut_s=float(self.config.get("audio.hard_cut_s", 70)),
        )
        for track in self.tracks:
            runtime = self.runtime[track]
            if len(runtime.ring):
                runtime.ring.flush_to(self.writer, track)
                self.writer.flush_track(track)  # the pre-roll is exactly chunk 0001
        self.committed = True
        log.info("recorder committed to %s", self.folder)

    def discard(self) -> None:
        """Evidence decayed: drop the rings, close the streams, leave nothing behind."""
        for runtime in self.runtime.values():
            runtime.ring.drop()
            runtime.capture.stop()
        self.runtime.clear()
        self.armed = False
        self.committed = False
        self.folder = None

    def start(self, folder: Path, meeting_id: str | None = None) -> None:
        """Manual Start: arm and commit in one step."""
        self.arm()
        self.commit(folder, meeting_id)

    def stop(self) -> RecordingResult:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        self.drain()
        self._flush_resamplers()
        result = RecordingResult(folder=self.folder)
        if self.writer is not None:
            result.records = self.writer.close()
            result.duration_ms = {t: self.writer.duration_ms(t) for t in self.tracks}
        for track, runtime in self.runtime.items():
            result.dropped[track] = runtime.capture.stats.dropped
            result.xruns[track] = runtime.capture.stats.xruns
            result.gaps_ms[track] = runtime.gap_ms
            runtime.capture.stop()
        self.runtime.clear()
        self.armed = False
        self.committed = False
        self.writer = None
        return result

    def _flush_resamplers(self) -> None:
        """End of stream: the resampler's delay line is real audio, not rounding."""
        for track, runtime in self.runtime.items():
            tail = runtime.resampler.flush()
            if len(tail) == 0:
                continue
            with self._lock:
                if self.committed and self.writer is not None and not self.paused:
                    self.writer.write_pcm(track, tail)
                else:
                    runtime.ring.push(tail)

    # -- the writer thread -------------------------------------------------

    def is_active(self) -> bool:
        return self.committed and not self.paused

    def pause(self) -> None:
        """Pause actually stops writing to disk — it does not trust the meeting app's mute."""
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def levels(self) -> dict[str, float]:
        return {track: runtime.level for track, runtime in self.runtime.items()}

    def pump_once(self, timeout: float = 0.05) -> int:
        """Drain both queues once. Returns the number of samples written."""
        written = 0
        for track in list(self.runtime):
            written += self._pump_track(track, timeout)
        return written

    def _pump_track(self, track: str, timeout: float) -> int:
        runtime = self.runtime.get(track)
        if runtime is None:
            return 0
        try:
            payload = runtime.capture.read(timeout)
        except StreamError as exc:
            self._reopen(track, exc)
            return 0
        if payload is None:
            return 0
        samples = runtime.resampler.process(payload)
        if len(samples) == 0:
            return 0
        runtime.level = float(np.sqrt(np.mean(np.square(samples.astype(np.float32) / 32768.0))))
        with self._lock:
            if self.committed and self.writer is not None:
                if self.paused:
                    return 0
                self.writer.write_pcm(track, samples)
            else:
                runtime.ring.push(samples)
        return len(samples)

    def _reopen(self, track: str, exc: Exception) -> None:
        """A USB event must not lose a meeting; a 300 ms hole is acceptable (§4.5)."""
        runtime = self.runtime[track]
        started = self.clock.monotonic()
        log.warning("stream error on %s: %s — reopening", track, exc)
        with self._lock:
            if self.writer is not None:
                self.writer.flush_track(track)
        try:
            runtime.capture.stop()
        except Exception:  # the device is already gone; nothing to salvage
            log.debug("stop() failed on a dead %s stream", track)
        capture = self.capture_factory(track)
        capture.start()
        elapsed_ms = round((self.clock.monotonic() - started) * 1000)
        gap_ms = max(elapsed_ms, self.min_gap_ms)
        runtime.capture = capture
        runtime.resampler = Resampler(
            in_rate=capture.format.rate, out_rate=self.rate, channels=capture.format.channels
        )
        runtime.reopened += 1
        runtime.gap_ms += gap_ms
        with self._lock:
            if self.writer is not None:
                self.writer.note_gap(track, gap_ms)

    def drain(self, max_iterations: int = 100_000) -> int:
        """Pump until both queues are empty — used at stop and by the tests."""
        total = 0
        for _ in range(max_iterations):
            written = 0
            for track in list(self.runtime):
                runtime = self.runtime[track]
                if runtime.capture.frames.empty():
                    continue
                written += self._pump_track(track, 0.0)
            if written == 0:
                break
            total += written
        return total

    def run_forever(self) -> None:
        while not self._stop.is_set():
            if self.pump_once(0.05) == 0:
                self.clock.sleep(0.01)

    def start_thread(self) -> threading.Thread:
        """Idempotent: a second caller gets the running writer thread, not a second one.

        Two pump threads would drain the same queues into the same resampler, and the
        one that is not joined at stop() keeps feeding it after its final flush.
        """
        existing = self._thread
        if existing is not None and existing.is_alive():
            return existing
        self._stop.clear()
        thread = threading.Thread(target=self.run_forever, name="writer", daemon=True)
        self._thread = thread
        thread.start()
        return thread
