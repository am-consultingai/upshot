"""Suites registered by phases 1 and up.

Everything heavy is imported inside the suite function, so importing this module stays
free and safe in every environment.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from app.selftest import Check, skipped, suite


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


def _tone_fixture(path: Path, seconds: float = 20.0, rate: int = 16000) -> Path:
    """A speech-like fallback signal for machines with no SAPI (chirp + amplitude
    envelope, so cross-correlation has something unambiguous to lock onto)."""
    import wave

    import numpy as np

    index = np.arange(int(seconds * rate), dtype=np.float64)
    sweep = np.sin(2 * np.pi * (200 + 600 * (index / len(index))) * index / rate)
    envelope = 0.5 + 0.5 * np.sin(2 * np.pi * 3.0 * index / rate)
    signal = np.round(0.4 * sweep * envelope * 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(signal.tobytes())
    return path


@suite("audio")
def _audio(args: argparse.Namespace) -> list[Check]:
    """T2 — play a known signal out the render endpoint and capture it on loopback."""
    import tempfile
    import threading
    import time
    import wave
    from pathlib import Path

    import numpy as np

    from app.audio.analysis import cross_correlation
    from app.audio.devices import (
        NoDeviceError,
        default_capture,
        default_render,
        list_devices,
        loopback_for,
        play_wav,
    )
    from app.audio.recorder import Recorder
    from app.audio.writer import read_manifest
    from app.clock import SystemClock
    from app.config import Config

    try:
        devices = list_devices()
        render = default_render()
        loopback = loopback_for(render)
    except (NoDeviceError, Exception) as exc:
        return [
            skipped("audio_devices", f"no WASAPI audio available here ({exc})"),
            skipped("loopback_echo", "requires a Windows render endpoint"),
        ]

    checks = [
        Check(
            "audio_devices",
            len(devices) > 0,
            f"{len(devices)} endpoints; render={render.name!r}, loopback={loopback.name!r}",
            {"devices": len(devices), "render_rate": render.rate},
        )
    ]
    try:
        capture_device = default_capture()
        checks.append(
            Check("default_capture", True, capture_device.name, {"rate": capture_device.rate})
        )
    except Exception as exc:
        checks.append(skipped("default_capture", f"no microphone ({exc})"))

    cfg = Config.load()
    cfg.set("audio.capture", "wasapi")
    seconds = 20.0
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        fixture = root / "source.wav"
        try:
            from tests.fixtures import speech  # source checkouts only

            if speech.available():
                speech.synth("This is the meeting agent loopback self test. " * 6, fixture)
            else:
                _tone_fixture(fixture, seconds)
        except Exception:
            _tone_fixture(fixture, seconds)

        from app.audio.factory import make_capture

        recorder = Recorder(cfg, lambda track: make_capture(cfg, track), clock=SystemClock())
        recorder.start(root / "meeting", "selftest-audio")
        thread = recorder.start_thread()
        time.sleep(0.5)
        player = threading.Thread(target=play_wav, args=(fixture,), daemon=True)
        player.start()
        player.join(timeout=seconds + 30)
        time.sleep(0.5)
        result = recorder.stop()
        assert thread is not None

        with wave.open(str(fixture), "rb") as handle:
            source_rate = handle.getframerate()
            source = np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16)
        if source_rate != cfg.sample_rate:
            import soxr

            source = np.asarray(
                soxr.resample(source.astype(np.float32), source_rate, cfg.sample_rate)
            )
        captured_parts = []
        for record in sorted((r for r in result.records if r.track == "them"), key=lambda r: r.seq):
            with wave.open(str(Path(result.folder or root) / "audio" / record.file), "rb") as h:
                captured_parts.append(np.frombuffer(h.readframes(h.getnframes()), dtype=np.int16))
        captured = np.concatenate(captured_parts) if captured_parts else np.zeros(0, dtype=np.int16)
        peak, lag = cross_correlation(
            source.astype(np.float64), captured.astype(np.float64), max_lag=cfg.sample_rate
        )
        offset_ms = abs(lag) * 1000 / cfg.sample_rate
        ratio = len(captured) / max(1, len(source))
        records, torn = read_manifest(Path(result.folder or root))
        xruns = result.xruns.get("them", 0)

    checks.extend(
        [
            Check(
                "loopback_echo",
                peak >= 0.8 and offset_ms < 500,
                f"correlation {peak:.3f}, offset {offset_ms:.0f} ms",
                {"correlation": round(peak, 4), "offset_ms": round(offset_ms, 1)},
            ),
            Check(
                "loopback_sample_count",
                abs(ratio - 1.0) <= 0.01,
                f"captured/source = {ratio:.4f}",
                {"ratio": round(ratio, 5), "captured": len(captured)},
            ),
            Check("loopback_xruns", xruns == 0, f"{xruns} xruns", {"xruns": xruns}),
            Check("loopback_manifest", torn == 0, f"{len(records)} chunks, {torn} torn lines"),
        ]
    )
    return checks
