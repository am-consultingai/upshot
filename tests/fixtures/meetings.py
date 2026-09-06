"""Building a meeting on disk the way the recorder would have left it."""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.audio.writer import ChunkWriter
from app.clock import FakeClock
from app.config import Config, default_config
from app.db.dao import Dao, connect
from app.pipeline.context import StageContext
from app.pipeline.queue import Job, JobQueue
from app.pipeline.states import JobStage, MeetingState

RATE = 16000


def speechish(
    seconds: float, seed: int = 5, rate: int = RATE, amplitude: float = 0.15
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.normal(0, amplitude, int(seconds * rate)) * 32767).astype(np.int16)


def silence(seconds: float, rate: int = RATE) -> np.ndarray:
    return np.zeros(int(seconds * rate), dtype=np.int16)


def write_chunks(
    folder: Path,
    *,
    seconds: float = 120.0,
    tracks: tuple[str, ...] = ("me", "them"),
    quiet_tracks: tuple[str, ...] = (),
    leak: float = 0.0,
    leak_delay: int = 1683,
) -> list:  # type: ignore[type-arg]
    """``leak`` reproduces a microphone bus that also carries playback (D36/D37)."""
    writer = ChunkWriter(folder, tracks=tracks, rate=RATE)
    # A distinct seed per track. Sharing one made both tracks byte-identical, which is
    # precisely the fault the crosstalk check looks for — a fixture should not look like
    # a broken machine. When the bus leaks, the near voice is the quiet part of its own
    # track: measured on a real recording it is a quarter of what the copy contributes.
    near = 0.04 if leak else 0.15
    payloads = {
        track: silence(seconds)
        if track in quiet_tracks
        else speechish(seconds, seed=5 + index, amplitude=near if track == "me" else 0.15)
        for index, track in enumerate(tracks)
    }
    if leak and {"me", "them"} <= set(payloads):
        from app.audio.echo import aligned

        copy = leak * aligned(payloads["them"], leak_delay, len(payloads["me"]))
        payloads["me"] = np.clip(payloads["me"] + copy, -32768, 32767).astype(np.int16)
    for track in tracks:
        writer.write_pcm(track, payloads[track])
    return writer.close()


@dataclass
class Harness:
    config: Config
    dao: Dao
    queue: JobQueue
    clock: FakeClock
    root: Path

    def meeting(self, name: str = "m1", state: MeetingState = MeetingState.RECORDED):  # type: ignore[no-untyped-def]
        folder = self.root / name
        meeting = self.dao.insert_meeting(folder=folder, source="manual", profile="cpu-deferred")
        for step in (MeetingState.RECORDED,):
            if state != MeetingState.RECORDING:
                self.dao.set_state(meeting.id, step)
        return self.dao.require_meeting(meeting.id)

    def context(
        self, meeting, stage: JobStage = JobStage.TRANSCRIBE, services=None
    ) -> StageContext:  # type: ignore[no-untyped-def]
        job = self.queue.enqueue(meeting.id, stage)
        return StageContext(
            meeting=meeting,
            dao=self.dao,
            queue=self.queue,
            config=self.config,
            clock=self.clock,
            job=job,
            services=services,
        )


def harness(tmp_path: Path, **overrides: object) -> Harness:
    config = default_config(**overrides)  # type: ignore[arg-type]
    conn = connect(tmp_path / "index.db")
    clock = FakeClock()
    dao = Dao(conn, clock)
    dao.seed_ids(42)
    return Harness(config, dao, JobQueue(conn, clock, random.Random(2)), clock, tmp_path / "data")


__all__ = ["RATE", "Harness", "Job", "harness", "silence", "speechish", "write_chunks"]
