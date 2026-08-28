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


def speechish(seconds: float, seed: int = 5, rate: int = RATE) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.normal(0, 0.15, int(seconds * rate)) * 32767).astype(np.int16)


def silence(seconds: float, rate: int = RATE) -> np.ndarray:
    return np.zeros(int(seconds * rate), dtype=np.int16)


def write_chunks(
    folder: Path,
    *,
    seconds: float = 120.0,
    tracks: tuple[str, ...] = ("me", "them"),
    quiet_tracks: tuple[str, ...] = (),
) -> list:  # type: ignore[type-arg]
    writer = ChunkWriter(folder, tracks=tracks, rate=RATE)
    for track in tracks:
        payload = silence(seconds) if track in quiet_tracks else speechish(seconds)
        writer.write_pcm(track, payload)
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
