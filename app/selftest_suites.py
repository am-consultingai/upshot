"""Suites registered by phases 1 and up.

Everything heavy is imported inside the suite function, so importing this module stays
free and safe in every environment.
"""

from __future__ import annotations

import argparse

from app.selftest import Check, suite


@suite("config")
def _config(args: argparse.Namespace) -> list[Check]:
    from app.config import SECRET_KEYS, Config

    cfg = Config.load()
    dump = cfg.redacted_dump()
    text = repr(dump)
    leaked = [k for k in SECRET_KEYS if k.split(".")[-1] in text and "***" not in text]
    warnings = cfg.warnings()
    return [
        Check("config_loads", True, f"{cfg.source_file}", {"profile": cfg.profile}),
        Check("config_redacts_secrets", not leaked, ", ".join(leaked) or "no secrets in dump"),
        Check(
            "config_warnings",
            True,
            "; ".join(warnings) or "none",
            {"warnings": len(warnings)},
        ),
    ]


@suite("db")
def _db(args: argparse.Namespace) -> list[Check]:
    from app.config import Config
    from app.db import migrate as migrations
    from app.db.dao import Dao, capabilities, connect

    cfg = Config.load()
    conn = connect(fts=cfg.get("db.fts", "auto") != "off")
    caps = capabilities(conn)
    version = migrations.current_version(conn)
    dao = Dao(conn)
    count = len(dao.list_meetings(limit=1))
    checks = [
        Check(
            "db_migrated",
            version == max(m.version for m in migrations.discover()),
            f"schema_version={version}",
            {"schema_version": version},
        ),
        Check(
            "fts5",
            True,
            "FTS5 available" if caps.fts else "FTS5 missing — search falls back to LIKE",
            {"fts5": caps.fts},
        ),
        Check("db_readable", True, f"{count} meeting(s) visible"),
    ]
    conn.close()
    return checks


@suite("queue")
def _queue(args: argparse.Namespace) -> list[Check]:
    """Prove the queue's guarantees against a temporary database on every run."""
    import random
    import tempfile
    from pathlib import Path

    from app.clock import FakeClock
    from app.config import default_config
    from app.db.dao import Dao, connect
    from app.pipeline.fake_stages import FakeStages
    from app.pipeline.queue import JobQueue
    from app.pipeline.states import MeetingState
    from app.pipeline.worker import Worker

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        conn = connect(root / "index.db")
        clock = FakeClock()
        dao = Dao(conn, clock)
        dao.seed_ids(1)
        queue = JobQueue(conn, clock, random.Random(1))
        stages = FakeStages(fail_times=2)
        worker = Worker(
            dao=dao,
            queue=queue,
            config=default_config(job_policy="asap"),
            stages=stages.registry(),  # type: ignore[arg-type]
            clock=clock,
        )
        meeting = dao.insert_meeting(folder=root / "m", source="manual")
        dao.set_state(meeting.id, MeetingState.RECORDED)
        queue.enqueue(meeting.id, "flaky")
        for _ in range(4):
            worker.run_once()
            clock.advance(60)
        job = queue.get_by_stage(meeting.id, "flaky")
        ok = job is not None and job.state == "done" and job.attempts == 2
        detail = f"flaky recovered after {job.attempts if job else '?'} failed attempts"
        conn.close()
    return [Check("queue_retry_ladder", ok, detail, {"attempts": job.attempts if job else -1})]


@suite("audio-synthetic")
def _audio_synthetic(args: argparse.Namespace) -> list[Check]:
    """Every byte-level guarantee in §4, with no audio hardware."""
    import tempfile
    from pathlib import Path

    from app.audio.fake import SyntheticCapture
    from app.audio.recorder import Recorder
    from app.audio.writer import read_manifest
    from app.clock import FakeClock
    from app.config import Config

    cfg = Config.load()
    cfg.set("audio.capture", "synthetic")
    seconds = 180

    def factory(track: str) -> SyntheticCapture:
        return SyntheticCapture(track, "tone" if track == "them" else "silence", block_frames=48000)

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "meeting"
        recorder = Recorder(cfg, factory, clock=FakeClock())
        recorder.start(folder, "selftest")
        for _ in range(seconds):
            recorder.pump_once(0.0)
        recorder.stop()
        records, torn = read_manifest(folder)
        durations = {
            track: sum(r.dur_ms for r in records if r.track == track) for track in ("me", "them")
        }
        drift = {track: abs(value - seconds * 1000) for track, value in durations.items()}
        ok = torn == 0 and all(value <= 50 for value in drift.values())
    return [
        Check(
            "synthetic_capture_roundtrip",
            ok,
            f"{seconds}s synthetic → durations {durations}, torn lines {torn}",
            {"durations_ms": durations, "drift_ms": drift, "chunks": len(records)},
        )
    ]
