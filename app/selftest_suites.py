"""Suites registered by phases 1 and up.

Everything heavy is imported inside the suite function, so importing this module stays
free and safe in every environment.
"""

from __future__ import annotations

import argparse
import os
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
                speech.synth("This is Upshot loopback self test. " * 6, fixture)
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


@suite("asr")
def _asr(args: argparse.Namespace) -> list[Check]:
    """Which backend is wired, and whether a local model and CUDA are actually present."""
    from app.asr.local import cuda_library_dirs, supported_compute_type
    from app.asr.models import resolve
    from app.config import Config

    cfg = Config.load()
    kind = str(cfg.get("asr.backend", "local"))
    cuda_dirs = cuda_library_dirs(configured=cfg.get("asr.cuda_dir"))
    choice = resolve(cfg, device="cuda" if cuda_dirs else "cpu")
    checks = [
        Check("asr_backend", True, f"asr.backend = {kind}", {"backend": kind}),
        Check(
            "asr_cuda",
            True,
            f"{len(cuda_dirs)} CUDA library dir(s)"
            + (f"; compute {supported_compute_type()}" if cuda_dirs else "; running on CPU"),
            {"cuda_dirs": len(cuda_dirs), "device": "cuda" if cuda_dirs else "cpu"},
        ),
        Check(
            "asr_model",
            True,
            ("local: " if choice.local else "will download: ") + choice.reference,
            {"model": choice.reference, "local": choice.local},
        ),
    ]
    return checks


@suite("live-llm", in_all=False)
def _live_llm(args: argparse.Namespace) -> list[Check]:
    """One real API call, schema-validated. Skipped when no key is configured."""
    from app.config import Config
    from app.llm.client import AnthropicClient, system_blocks
    from app.llm.schema import validate
    from app.llm.tokens import CachingCounter, tokens_per_word

    cfg = Config.load()
    if not cfg.secret("anthropic", env="ANTHROPIC_API_KEY"):
        return [skipped("live_llm", "no Anthropic key in keyring or ANTHROPIC_API_KEY")]
    transcript = (
        "**[00:00] THEM:** בוקר טוב, נתחיל עם הסטטוס של ה-deployment.\n"
        "**[00:14] ME:** העברתי את השירות ל-Kubernetes, יש בעיה עם ה-migration.\n"
        "**[00:31] THEM:** אז ההחלטה היא לדחות את הרילי‏ס לשבוע הבא.\n"
    )
    client = AnthropicClient(cfg)
    counter = CachingCounter(client.count_tokens)
    ratio = tokens_per_word(transcript, counter)
    result = client.complete_json(
        system_blocks=system_blocks("You write meeting notes as JSON.", None),
        user=transcript,
        max_tokens=4000,
    )
    validate(result.data)
    return [
        Check(
            "live_llm_call",
            result.stop_reason == "end_turn" and bool(result.data.get("title")),
            f"stop_reason={result.stop_reason}, title={result.data.get('title')!r}",
            {"usage": result.usage, "model": result.model},
        ),
        Check(
            "hebrew_tokens_per_word",
            ratio < 5.0,
            f"{ratio:.3f} tokens per Hebrew word",
            {"tokens_per_word": round(ratio, 3)},
        ),
    ]


def _synthesize_meeting(folder: Path, seconds: float, rate: int = 16000) -> None:
    """Two tracks of deterministic audio on disk, exactly as the recorder leaves them."""
    import numpy as np

    from app.audio.writer import ChunkWriter

    writer = ChunkWriter(folder, tracks=("me", "them"), rate=rate)
    rng = np.random.default_rng(7)
    for track in ("me", "them"):
        payload = (rng.normal(0, 0.15, int(seconds * rate)) * 32767).astype(np.int16)
        writer.write_pcm(track, payload)
    writer.close()


def _import_wav(folder: Path, wav: Path, rate: int = 16000) -> None:
    """A user-supplied recording becomes the `them` track — the same path /api/import uses."""
    from app.audio.ingest import ingest

    ingest(wav, folder)


@suite("pipeline")
def _pipeline(args: argparse.Namespace) -> list[Check]:
    """M0 — a recording on disk becomes summary.html, with fakes and no network."""
    import random
    import tempfile
    import time
    from pathlib import Path

    from app.clock import SystemClock
    from app.config import Config
    from app.db.dao import Dao, connect
    from app.llm.schema import validate
    from app.meetings import MeetingService
    from app.pipeline.queue import JobQueue
    from app.pipeline.stages import registry
    from app.pipeline.stages.summarize import load_notes
    from app.pipeline.states import MeetingState
    from app.pipeline.worker import Worker

    started = time.monotonic()
    budget_s = float(getattr(args, "budget_s", 0) or 120)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = Config.load()
        cfg.set("data_root", str(root / "meetings"))
        cfg.set("asr.backend", "fake")
        cfg.set("llm.provider", "fake")
        cfg.set("audio.vad", "energy")
        cfg.set("enrichment.source", "null")
        cfg.set("delivery.mode", "draft")
        cfg.set("job_policy", "asap")
        conn = connect(root / "index.db")
        clock = SystemClock()
        dao = Dao(conn, clock)
        dao.seed_ids(11)
        queue = JobQueue(conn, clock, random.Random(5))
        service = MeetingService(cfg, dao, queue, clock=clock)
        meeting = service.create(source="imported")

        wav = getattr(args, "input", None)
        if wav and Path(wav).exists():
            _import_wav(meeting.path, Path(wav))
            source_detail = f"imported {Path(wav).name}"
        else:
            _synthesize_meeting(meeting.path, seconds=600)
            source_detail = "synthesized 10 minutes of two-track audio"
        service.finish(meeting.id, duration_s=600)

        worker = Worker(dao=dao, queue=queue, config=cfg, stages=registry(), clock=clock)
        executed = worker.drain()
        final = dao.require_meeting(meeting.id)
        folder = final.path
        artifacts = {
            name: (folder / name).exists()
            for name in (
                "transcript.json",
                "transcript.md",
                "notes.json",
                "summary.html",
                "summary.email.html",
                "meta.json",
            )
        }
        schema_ok = False
        notes_detail = "notes.json missing"
        if artifacts["notes.json"]:
            try:
                notes = load_notes(folder)
                validate(notes)
                schema_ok = True
                notes_detail = f"notes.json is schema-valid; title={notes.get('title')!r}"
            except Exception as exc:
                notes_detail = f"notes.json failed validation: {exc}"
        log_path = folder / "pipeline.log"
        log_text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        errors = [line for line in log_text.splitlines() if " ERROR " in line]
        elapsed = time.monotonic() - started
        state = final.state
        conn.close()

    return [
        Check("pipeline_source", True, source_detail, {"jobs_executed": executed}),
        Check(
            "pipeline_artifacts",
            all(artifacts.values()),
            ", ".join(f"{name}={'ok' if ok else 'MISSING'}" for name, ok in artifacts.items()),
            {"artifacts": artifacts},
        ),
        Check("pipeline_notes_schema", schema_ok, notes_detail),
        Check(
            "pipeline_state",
            state in (MeetingState.RENDERED, MeetingState.DELIVERED),
            f"meeting reached {state}",
            {"state": state},
        ),
        Check("pipeline_log_clean", not errors, f"{len(errors)} ERROR line(s) in pipeline.log"),
        Check(
            "pipeline_within_budget",
            elapsed <= budget_s,
            f"{elapsed:.1f}s (budget {budget_s:.0f}s)",
            {"seconds": round(elapsed, 2), "budget_s": budget_s},
        ),
    ]


@suite("api")
def _api(args: argparse.Namespace) -> list[Check]:
    """Start the server on an ephemeral port and hit /api/status over a real socket."""
    import tempfile
    from pathlib import Path

    import httpx

    from app.config import Config
    from app.main import create_app
    from app.server import LocalServer
    from app.services import build

    def free_port() -> int:
        import socket

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return int(probe.getsockname()[1])

    with tempfile.TemporaryDirectory() as tmp:
        cfg = Config.load()
        cfg.set("data_root", str(Path(tmp) / "meetings"))
        cfg.set("server.port", free_port())
        cfg.set("delivery.notifier", "fake")
        services = build(cfg, with_worker=False, with_recorder=False)
        app = create_app(services)
        server = LocalServer(app, host=cfg.server_host, port=cfg.server_port).start()
        try:
            base = f"http://127.0.0.1:{cfg.server_port}"
            bound = server.sockets[0].getsockname()[0]
            with httpx.Client(base_url=base, timeout=5.0) as client:
                unauthorized = client.get("/api/status")
                token = services.auth.issue_token()
                client.get("/", params={"k": token})
                status = client.get("/api/status")
                rebind = client.get("/api/status", headers={"Host": "evil.com"})
        finally:
            server.stop()
            services.close()

    return [
        Check("api_bind_address", bound == "127.0.0.1", f"bound to {bound}", {"host": bound}),
        Check(
            "api_requires_cookie",
            unauthorized.status_code == 401,
            f"unauthenticated /api/status → {unauthorized.status_code}",
        ),
        Check(
            "api_status",
            status.status_code == 200,
            f"/api/status → {status.status_code}",
            {
                "queue_depth": status.json().get("queue_depth")
                if status.status_code == 200
                else None
            },
        ),
        Check(
            "api_host_header",
            rebind.status_code == 421,
            f"Host: evil.com → {rebind.status_code}",
        ),
    ]


def _detector_harness(root: Path, *, mode: str) -> Any:
    """A real detector and a real recorder, driven by fake signal sources."""
    import random

    from app.audio.fake import SyntheticCapture
    from app.audio.recorder import Recorder
    from app.clock import FakeClock
    from app.config import Config
    from app.db.dao import Dao, connect
    from app.detect.detector import Detector
    from app.detect.sources import (
        FakeCameraSource,
        FakeMicSource,
        FakeSessionSource,
        FakeTitleSource,
        FakeVadSource,
        Sources,
    )
    from app.meetings import MeetingService
    from app.notify import FakeNotifier
    from app.pipeline.queue import JobQueue

    cfg = Config.load()
    cfg.set("data_root", str(root / "meetings"))
    cfg.set("audio.capture", "synthetic")
    cfg.set("audio.vad", "energy")
    cfg.set("asr.backend", "fake")
    cfg.set("llm.provider", "fake")
    cfg.set("detection.mode", mode)
    cfg.set("audio.min_meeting_s", 5)
    cfg.set("job_policy", "asap")
    conn = connect(root / "index.db")
    clock = FakeClock()
    dao = Dao(conn, clock)
    dao.seed_ids(31)
    queue = JobQueue(conn, clock, random.Random(13))
    captures: dict[str, Any] = {}

    def make(track: str) -> Any:
        capture = SyntheticCapture(track, "tone", queue_seconds=200.0, generate_on_read=False)
        captures[track] = capture
        return capture

    recorder = Recorder(cfg, make, clock=clock)
    sources = Sources(
        mic=FakeMicSource(),
        sessions=FakeSessionSource(),
        titles=FakeTitleSource(),
        camera=FakeCameraSource(),
        vad=FakeVadSource(),
    )
    detector = Detector(
        cfg,
        dao,
        MeetingService(cfg, dao, queue, clock=clock),
        recorder,
        sources,
        clock=clock,
        notifier=FakeNotifier(clock=clock),
    )
    return {
        "cfg": cfg,
        "conn": conn,
        "dao": dao,
        "queue": queue,
        "clock": clock,
        "recorder": recorder,
        "sources": sources,
        "detector": detector,
        "captures": captures,
    }


def _run_seconds(harness: dict[str, Any], count: int) -> None:
    for _ in range(count):
        for capture in harness["captures"].values():
            for _block in range(10):
                capture.emit()
        harness["recorder"].drain()
        harness["detector"].tick()
        harness["clock"].advance(1)


@suite("detect-e2e", in_all=False)
def _detect_e2e(args: argparse.Namespace) -> list[Check]:
    """M2 — a simulated meeting is detected, recorded and processed to RENDERED."""
    import tempfile
    from pathlib import Path

    from app.pipeline.stages import registry
    from app.pipeline.states import MeetingState
    from app.pipeline.worker import Worker

    checks: list[Check] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        # 1. a real meeting: detected, committed, recorded, processed
        h = _detector_harness(root / "positive", mode="on")
        h["sources"].mic.hold("Zoom.exe")
        h["sources"].titles.window_titles = ["Zoom Meeting"]
        h["sources"].vad.set(me=True, them=True)
        _run_seconds(h, 130)
        h["sources"].mic.release()
        _run_seconds(h, 65)
        meetings = h["dao"].list_meetings()
        detected = meetings[0] if meetings else None
        worker = Worker(
            dao=h["dao"], queue=h["queue"], config=h["cfg"], stages=registry(), clock=h["clock"]
        )
        worker.drain()
        final = h["dao"].require_meeting(detected.id) if detected else None
        artifacts: dict[str, bool] = {}
        ok = False
        codes: list[str] = []
        if final is not None:
            artifacts = {
                name: (final.path / name).exists()
                for name in ("transcript.md", "notes.json", "summary.html")
            }
            codes = [item["code"] for item in final.evidence]
            ok = (
                final.source == "detected"
                and bool(codes)
                and all(artifacts.values())
                and final.state in (MeetingState.RENDERED, MeetingState.DELIVERED)
            )
        checks.append(
            Check(
                "detect_commits_a_meeting",
                ok,
                f"{final.id if final else 'none'} → {final.state if final else '-'}, "
                f"artifacts {artifacts}",
                {"evidence": codes},
            )
        )
        h["conn"].close()

        # 2a. watching a video: loopback audio, nobody holds the microphone. The
        # detector never even wakes — the cheapest possible rejection.
        n = _detector_harness(root / "negative", mode="on")
        n["sources"].vad.set(me=False, them=True)
        n["sources"].titles.window_titles = ["Funny cats - YouTube"]
        _run_seconds(n, 120)
        checks.append(
            Check(
                "video_never_wakes_the_detector",
                not n["dao"].list_meetings() and not n["dao"].detector_events(),
                f"{len(n['dao'].list_meetings())} meetings, "
                f"{len(n['dao'].detector_events())} detector events",
            )
        )

        # 2b. a media or voice-note app that *does* hold the mic while audio plays: it
        # wakes, peaks at 3 (unknown app + loopback), never reaches 5, and is logged as a
        # near miss at the 90 s give-up.
        n["sources"].mic.hold("SomeMediaPlayer.exe")
        _run_seconds(n, 95)
        negative_meetings = n["dao"].list_meetings()
        events = n["dao"].detector_events()
        checks.append(
            Check(
                "media_app_is_a_near_miss",
                not negative_meetings and bool(events) and events[0].outcome == "near_miss",
                f"{len(negative_meetings)} meetings, outcome "
                f"{events[0].outcome if events else 'none'}",
                {"peak_score": events[0].peak_score if events else None},
            )
        )
        n["conn"].close()

        # 3. shadow mode commits nothing
        s = _detector_harness(root / "shadow", mode="shadow")
        s["sources"].mic.hold("Zoom.exe")
        s["sources"].titles.window_titles = ["Zoom Meeting"]
        s["sources"].vad.set(me=True, them=True)
        _run_seconds(s, 30)
        shadow_events = s["dao"].detector_events()
        shadow_files = (
            list((root / "shadow" / "meetings").rglob("*"))
            if (root / "shadow" / "meetings").exists()
            else []
        )
        checks.append(
            Check(
                "shadow_commits_nothing",
                not s["dao"].list_meetings()
                and bool(shadow_events)
                and shadow_events[0].outcome == "shadow"
                and not [f for f in shadow_files if f.is_file()],
                f"{len(shadow_events)} shadow event(s), {len(shadow_files)} paths on disk",
            )
        )
        s["conn"].close()
    return checks


@suite("detect-shadow", in_all=False)
def _detect_shadow(args: argparse.Namespace) -> list[Check]:
    """Run the real signal sources for N seconds and report what they saw."""
    import time

    from app.config import Config
    from app.detect.evidence import mic_evidence, score, title_evidence
    from app.detect.registry import ConsentStoreReader
    from app.detect.sessions import PycawSessions
    from app.detect.windows import EnumWindowTitles

    cfg = Config.load()
    seconds = int(getattr(args, "seconds", 60) or 60)
    reader = ConsentStoreReader()
    sessions = PycawSessions()
    windows = EnumWindowTitles()
    if not reader.current_holders() and not windows.titles():
        available = False
    else:
        available = True
    if not available and __import__("sys").platform != "win32":
        return [
            skipped("detect_shadow", "the microphone ConsentStore is Windows-only"),
        ]
    samples = 0
    peak = 0
    processes: set[str] = set()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        holders = reader.current_holders()
        titles = windows.titles()
        render = sessions.render_processes()
        process = holders[0].process if holders else ""
        if process:
            processes.add(process)
        evidence = mic_evidence(
            process,
            known_apps=list(cfg.get("detection.known_apps", [])),
            ignore=list(cfg.get("detection.ignore", [])),
        ) + title_evidence(titles, list(cfg.get("detection.title_patterns", [])))
        peak = max(peak, score(evidence, cfg.detection_weights))
        samples += 1
        assert isinstance(render, list)
        time.sleep(1.0)
    return [
        Check(
            "detect_shadow",
            True,
            f"{samples} samples in {seconds}s; peak score {peak}; "
            f"mic holders seen: {sorted(processes) or 'none'}",
            {"samples": samples, "peak_score": peak, "processes": sorted(processes)},
        )
    ]


@suite("capture-e2e", in_all=False)
def _capture_e2e(args: argparse.Namespace) -> list[Check]:
    """M1 — the whole product proving itself with no human in the loop.

    Start a recording through the API, play a speech fixture into the render endpoint for
    N seconds, stop through the API, and run the pipeline to RENDERED.

    On a machine with WASAPI the fixture is played out the real render endpoint and
    captured on the loopback stream. Everywhere else the same fixture is fed through
    ``SyntheticCapture``, which exercises every part of the path except the audio
    hardware — the report says which mode ran, and `mode` is in the metrics.
    """
    import random
    import tempfile
    import threading
    import time
    import wave
    from pathlib import Path

    import numpy as np

    from app.api.security import CSRF_HEADER
    from app.audio.analysis import cross_correlation
    from app.audio.fake import SyntheticCapture
    from app.audio.recorder import Recorder
    from app.audio.writer import read_manifest, track_files
    from app.clock import SystemClock
    from app.config import Config
    from app.db.dao import Dao, connect
    from app.events import EventBus
    from app.mail import Mailer
    from app.main import create_app
    from app.meetings import MeetingService
    from app.notify import FakeNotifier
    from app.pipeline.queue import JobQueue
    from app.pipeline.stages import registry
    from app.pipeline.states import MeetingState
    from app.pipeline.worker import Worker
    from app.services import Services
    from app.tray import TrayApp
    from app.tray_state import RecorderState

    seconds = int(getattr(args, "seconds", 120) or 120)
    checks: list[Check] = []

    hardware = True
    try:
        from app.audio.devices import default_render, loopback_for

        loopback_for(default_render())
    except Exception:
        hardware = False
    mode = "wasapi" if hardware else "synthetic"

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        fixture = root / "speech.wav"
        spoken_words: list[str] = []
        try:
            from tests.fixtures import speech  # source checkouts only

            if speech.available():
                sentence = (
                    "Good morning everyone, can you hear me? "
                    "Let us start the weekly sync and review the release. "
                )
                speech.synth(sentence * 4, fixture)
                spoken_words = [w.strip(".,?") for w in sentence.split() if len(w) > 3]
            else:
                raise RuntimeError("no SAPI")
        except Exception:
            import subprocess
            import sys as _sys

            subprocess.run(
                [
                    _sys.executable,
                    "scripts/make_fixture.py",
                    "--minutes",
                    str(max(0.5, seconds / 60)),
                    "--out",
                    str(fixture),
                ],
                check=True,
                capture_output=True,
            )

        cfg = Config.load()
        cfg.set("data_root", str(root / "meetings"))
        cfg.set("asr.backend", "fake")
        cfg.set("asr.fake_language", "en")
        cfg.set("asr.language_mode", "detect")
        cfg.set("llm.provider", "fake")
        cfg.set("audio.vad", "energy")
        cfg.set("delivery.notifier", "fake")
        cfg.set("job_policy", "asap")
        cfg.set("audio.min_meeting_s", 10)
        cfg.set("server.port", _free_port())
        cfg.set("audio.capture", "wasapi" if hardware else "synthetic")

        conn = connect(root / "index.db")
        clock = SystemClock()
        dao = Dao(conn, clock)
        queue = JobQueue(conn, clock, random.Random(17))
        events = EventBus()
        from app.api.security import AuthState

        services = Services(
            config=cfg,
            conn=conn,
            dao=dao,
            queue=queue,
            meetings=MeetingService(cfg, dao, queue, clock=clock),
            events=events,
            auth=AuthState(),
            clock=clock,
            mailer=Mailer(cfg),
            notifier=FakeNotifier(clock=clock),
        )
        captures: dict[str, Any] = {}

        def make(track: str) -> Any:
            if hardware:
                from app.audio.wasapi import WasapiCapture

                return WasapiCapture(track=track)
            capture = SyntheticCapture(
                track,
                "silence",
                wav=fixture if track == "them" else None,
                loop_wav=True,
                queue_seconds=30.0,
                generate_on_read=False,  # the test is the device: only explicit emits
            )
            captures[track] = capture
            return capture

        services.recorder = Recorder(cfg, make, clock=clock)
        services.worker = Worker(
            dao=dao,
            queue=queue,
            config=cfg,
            stages=registry(),
            clock=clock,
            recorder=services.recorder,
            services=services,
        )
        app = create_app(services)
        tray = TrayApp(services)

        def tray_label() -> str:
            """idle → recording → processing → idle, the way the icon reads."""
            state = tray.observe()
            if state.recorder is not RecorderState.IDLE:
                return str(state.recorder)
            return "processing" if state.processing else "idle"

        tray_states: list[str] = [tray_label()]

        from fastapi.testclient import TestClient

        client = TestClient(app, base_url=f"http://127.0.0.1:{cfg.server_port}")
        token = services.auth.issue_token()
        client.get(f"/?k={token}")
        client.headers[CSRF_HEADER] = services.auth.csrf_secret

        started = client.post("/api/recording/start", json={"title": "M1 capture"}).json()
        meeting_id = started["meeting_id"]
        tray_states.append(tray_label())

        player: threading.Thread | None = None
        if hardware:
            from app.audio.devices import play_wav

            player = threading.Thread(target=play_wav, args=(fixture,), daemon=True)
            player.start()
            time.sleep(seconds)
        else:
            services.recorder.start_thread()
            for _ in range(seconds * 10):  # 0.1 s blocks
                for capture in captures.values():
                    capture.emit()
                time.sleep(0.001)
            time.sleep(0.5)
        stopped = client.post("/api/recording/stop").json()
        if player is not None:
            player.join(timeout=5)
        tray_states.append(tray_label())

        folder = dao.require_meeting(meeting_id).path
        records, torn = read_manifest(folder)
        durations = {
            track: sum(r.dur_ms for r in records if r.track == track) for track in ("me", "them")
        }
        # One file per track, so presence is a file rather than a directory.
        tracks_present = set(track_files(folder))

        services.worker.drain()
        tray_states.append(tray_label())
        final = dao.require_meeting(meeting_id)

        # the loopback (or synthetic) `them` track must carry the fixture
        with wave.open(str(fixture), "rb") as handle:
            source = np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16)
        captured_parts = []
        for record in sorted((r for r in records if r.track == "them"), key=lambda r: r.seq):
            with wave.open(str(folder / "audio" / record.file), "rb") as handle:
                captured_parts.append(
                    np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16)
                )
        captured = np.concatenate(captured_parts) if captured_parts else np.zeros(0, np.int16)
        window = min(len(source), len(captured), 30 * cfg.sample_rate)
        peak, lag = cross_correlation(
            source[:window].astype(np.float64),
            captured[:window].astype(np.float64),
            max_lag=cfg.sample_rate,
        )

        transcript_text = ""
        transcript_md = folder / "transcript.md"
        if transcript_md.exists():
            transcript_text = transcript_md.read_text(encoding="utf-8").lower()
        artifacts = {
            name: (folder / name).exists()
            for name in ("transcript.md", "notes.json", "summary.html", "summary.email.html")
        }
        conn.close()

    checks.append(Check("capture_mode", True, f"{mode} capture", {"mode": mode}))
    checks.append(
        Check(
            "capture_api_roundtrip",
            bool(meeting_id) and stopped.get("meeting_id") == meeting_id,
            f"started and stopped {meeting_id} through the API, {stopped.get('chunks')} chunks",
            {"chunks": stopped.get("chunks")},
        )
    )
    checks.append(
        Check(
            "capture_duration",
            all(abs(value - seconds * 1000) <= 500 for value in durations.values()),
            f"durations {durations} against {seconds * 1000} ms (±500 ms)",
            {"durations_ms": durations, "torn_manifest_lines": torn},
        )
    )
    checks.append(
        Check(
            "capture_both_tracks",
            tracks_present == {"me", "them"},
            f"tracks on disk: {sorted(tracks_present)}",
        )
    )
    checks.append(
        Check(
            "capture_them_correlates",
            peak >= 0.5,
            f"cross-correlation {peak:.3f} at lag {lag}",
            {"correlation": round(peak, 4), "lag_samples": int(lag)},
        )
    )
    checks.append(
        Check(
            "capture_language_pinned",
            final.language == "en" and (final.language_conf or 0) >= 0.6,
            f"language {final.language} (confidence {final.language_conf})",
            {"language": final.language, "confidence": final.language_conf},
        )
    )
    checks.append(
        Check(
            "capture_pipeline_rendered",
            final.state in (MeetingState.RENDERED, MeetingState.DELIVERED)
            and all(artifacts.values()),
            f"{final.state}, artifacts {artifacts}",
            {"state": final.state},
        )
    )
    expected_sequence = ["idle", "recording", "processing", "idle"]
    checks.append(
        Check(
            "capture_tray_sequence",
            tray_states == expected_sequence,
            f"tray states {tray_states}",
            {"states": tray_states},
        )
    )
    if spoken_words and str(Config.load().get("asr.backend", "local")) != "fake":
        hits = sum(1 for word in spoken_words if word.lower() in transcript_text)
        checks.append(
            Check(
                "capture_transcript_words",
                hits / max(1, len(spoken_words)) >= 0.5,
                f"{hits}/{len(spoken_words)} fixture words in the transcript",
            )
        )
    else:
        checks.append(
            skipped(
                "capture_transcript_words",
                "the fake ASR is wired, so the transcript is fixture text, not the spoken words",
            )
        )
    return checks


@suite("capture-injected", in_all=False)
def _capture_injected(args: argparse.Namespace) -> list[Check]:
    """T2 — both tracks proved against known audio, on real Windows endpoints.

    ``capture-e2e`` plays a fixture out the *default* render endpoint and records the
    *default* microphone, so it needs a quiet room and a human to judge it, and it falls
    back to synthetic capture everywhere else. This suite instead injects a different
    signal into each track through idle virtual endpoints: nothing is audible, the
    machine's own audio settings are untouched, and both tracks have a known answer.

    What it proves that nothing else does: that audio put into the microphone endpoint
    comes out of ``me.wav``, that the two tracks are actually independent, and that the
    configured device indices are the ones used.
    """
    import tempfile
    import threading

    from app.audio.analysis import cross_correlation
    from app.audio.devices import play_wav
    from app.audio.factory import make_capture
    from app.audio.recorder import Recorder
    from app.audio.writer import track_files
    from app.clock import SystemClock
    from app.config import Config

    seconds = float(getattr(args, "seconds", 0) or 8)
    seconds = min(max(seconds, 4.0), 30.0)

    try:
        channels = _idle_injection_endpoints(seconds)
    except Exception as exc:
        detail = (
            f"no pair of usable virtual endpoints here ({exc}). "
            "Install a virtual audio device, name two endpoints in UP_TEST_ME_RENDER and "
            "UP_TEST_THEM_RENDER, or set UP_TEST_ALLOW_AUDIBLE=1 to borrow real ones."
        )
        if os.environ.get("UP_REQUIRE_HARDWARE"):
            # The Windows harness sets this: there, "nothing to test" is a failure, not a
            # pass. `skipped` reports ok=True, which is how a suite testing nothing at all
            # reads as green.
            return [Check("capture_injected", False, detail)]
        return [skipped("capture_injected", detail)]

    me_channel, them_channel = channels
    checks: list[Check] = [
        Check(
            "capture_endpoints",
            True,
            f"me <- {me_channel.name} [{me_channel.loopback}] "
            f"{me_channel.rate} Hz {me_channel.channels}ch, "
            f"them <- {them_channel.name} [{them_channel.render}] "
            f"{them_channel.rate} Hz {them_channel.channels}ch",
            {
                "me_render": me_channel.render,
                "me_rate": me_channel.rate,
                "them_render": them_channel.render,
                "them_rate": them_channel.rate,
                "candidates": list(REJECTED),
            },
        )
    ]

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Two chirps sweeping opposite ways: broadband, so correlation locks on hard, and
        # unlike each other, so a track carrying the wrong one cannot pass. Each is
        # written at its endpoint's own rate, and compared against the same sweep sampled
        # at the rate the recorder writes.
        rate = (cfg_rate := 16000)
        sweeps = {"me": (200.0, 3000.0), "them": (3500.0, 600.0)}
        near = _chirp(root / "near.wav", me_channel, seconds, *sweeps["me"])
        far = _chirp(root / "far.wav", them_channel, seconds, *sweeps["them"])

        cfg = Config.load()
        cfg.set("data_root", str(root / "meetings"))
        cfg.set("audio.capture", "wasapi")
        cfg.set("audio.vad", "energy")
        cfg.set("audio.min_meeting_s", 1)
        cfg.set("audio.echo_cancel", "off")  # A4 tests the canceller; this one must not
        # The point of the exercise: the recorder must honour these, not the defaults.
        cfg.set("audio.input_device", me_channel.loopback)
        cfg.set("audio.output_device", them_channel.render)

        recorder = Recorder(cfg, lambda track: make_capture(cfg, track), clock=SystemClock())
        folder = root / "meetings" / "injected"
        recorder.start(folder, "injected")
        recorder.start_thread()
        try:
            time.sleep(0.6)  # let both streams settle before the signal starts
            players = [
                threading.Thread(
                    target=play_wav, args=(near,), kwargs={"device_index": me_channel.render}
                ),
                threading.Thread(
                    target=play_wav, args=(far,), kwargs={"device_index": them_channel.render}
                ),
            ]
            for player in players:
                player.start()
            for player in players:
                player.join(timeout=seconds + 20)
            time.sleep(1.0)  # let the tail reach the writer
        finally:
            result = recorder.stop()

        files = track_files(folder)
        for track in ("me", "them"):
            if track not in files:
                checks.append(Check(f"capture_{track}_written", False, "no track file at all"))
        if {"me", "them"} - set(files):
            return checks

        heard = {track: _wav_samples(path) for track, path in files.items()}
        expected = {
            track: _chirp_samples(cfg_rate, seconds, *sweeps[track]) for track in sweeps
        }
        for track in ("me", "them"):
            import numpy as np

            aligned, score, offset_ms = _aligned_correlation(expected[track], heard[track], rate)
            peak = float(np.abs(heard[track]).max())
            checks.append(
                Check(
                    f"capture_{track}_carries_the_fixture",
                    aligned >= 0.8 and offset_ms < 2000,
                    f"correlation {aligned:.3f} windowed ({score:.3f} whole-track), "
                    f"offset {offset_ms:.0f} ms, peak {peak:.3f}, "
                    f"{len(heard[track]) / rate:.1f}s recorded",
                    {
                        "correlation": round(aligned, 4),
                        "offset_ms": round(offset_ms),
                        "mode": "wasapi",
                    },
                )
            )

        # Independence: the same signal on both tracks is the crosstalk fault (D36), and
        # it is indistinguishable from two people saying the same thing.
        bleed, _ = cross_correlation(heard["me"], heard["them"])
        checks.append(
            Check(
                "capture_tracks_are_independent",
                abs(bleed) < 0.3,
                f"cross-correlation between the tracks {bleed:.3f}",
                {"crosstalk": round(bleed, 4)},
            )
        )
        checks.append(
            Check(
                "capture_no_xruns",
                sum(result.xruns.values()) == 0,
                f"xruns {result.xruns}, dropped {result.dropped}, gaps {result.gaps_ms}",
                {"gaps_ms": {k: round(v) for k, v in result.gaps_ms.items()}},
            )
        )
    return checks


@dataclass(frozen=True)
class _Channel:
    """A render endpoint and the loopback that hears it."""

    render: int
    loopback: int
    name: str
    rate: int
    channels: int


#: The vocabulary virtual-audio drivers use in their endpoint names. A hint for *choosing
#: where to inject a test signal* — never product behaviour, and never a gate: an endpoint
#: is accepted because it round-trips a chirp, not because of its name.
#:
#: A physical endpoint carries the fixture just as well, but playing it there makes the
#: machine emit noise at whoever is sitting in front of it, so one is only borrowed when
#: the caller says that is acceptable.
VIRTUAL_MARKERS = ("cable", "voicemeeter", "vb-audio", "virtual", "vaio", "loopback")


def _idle_injection_endpoints(seconds: float) -> tuple[_Channel, _Channel]:
    """Two endpoints that are idle **and carry the signal for as long as the test runs**.

    Idle matters twice over: audio already flowing would drown the fixture, and a busy
    endpoint is usually someone's real output, which a test has no business borrowing.

    Idle is not enough, though, and neither is a quick check. A 1.2 s probe passed a
    Voicemeeter strip whose loopback clock drifts against its render clock; over six
    seconds the chirp slid out of phase and scored anywhere between 0.33 and 0.93, which
    read as a flaky recorder rather than an unsuitable endpoint. So each candidate must
    carry a chirp of the full test length before it is trusted — the harness proves its
    own plumbing before blaming the recorder.
    """

    from app.audio.devices import DeviceInfo, _wasapi_info, audio_host, loopback_for

    found: list[_Channel] = []
    # Explicit beats discovered: a provisioned machine names its injection endpoints and
    # the suite stops guessing. Discovery is the fallback, not the contract.
    forced = [os.environ.get("UP_TEST_ME_RENDER"), os.environ.get("UP_TEST_THEM_RENDER")]
    with audio_host() as host:
        wasapi = int(_wasapi_info(host)["index"])
        renders = []
        for index in range(host.get_device_count()):
            raw = dict(host.get_device_info_by_index(index))
            if not raw.get("maxOutputChannels") or int(raw.get("hostApi", -1)) != wasapi:
                continue
            try:
                companion = loopback_for(DeviceInfo.from_raw(raw), host)
            except Exception:
                continue
            renders.append((index, str(raw["name"]), companion, raw))

    def is_virtual(name: str) -> bool:
        return any(marker in name.lower() for marker in VIRTUAL_MARKERS)

    renders.sort(key=lambda item: not is_virtual(item[1]))
    if all(forced):
        # Named outright: a provisioned machine says which endpoints are the test's, and
        # discovery stops guessing entirely.
        renders = [item for item in renders if str(item[0]) in forced]
    elif not os.environ.get("UP_TEST_ALLOW_AUDIBLE"):
        # On a machine with no virtual audio device this leaves nothing, and the suite
        # says so rather than playing a chirp through someone's speakers.
        renders = [item for item in renders if is_virtual(item[1])]

    for index, name, companion, raw in renders:
        candidate = _Channel(
            render=index,
            loopback=companion.index,
            name=name,
            # Shared-mode WASAPI only accepts the endpoint's own mix format: a 16 kHz
            # fixture is refused outright with "Invalid sample rate".
            rate=int(raw["defaultSampleRate"]),
            channels=min(2, int(raw["maxOutputChannels"])),
        )
        score, note = _channel_score(candidate, companion, seconds)
        REJECTED.append(f"{name.split(' (')[0]}={score:.2f} [{note}]")
        if score >= 0.9:
            found.append(candidate)
        if len(found) == 2:
            return found[0], found[1]
    raise RuntimeError(f"found {len(found)} usable endpoint(s), need 2; tried {REJECTED}")


def _drain(capture: Any, seconds: float) -> Any:
    """What the capture has buffered, read for a bounded stretch.

    Bounded by the clock on purpose. A virtual endpoint keeps delivering frames whether
    anything is playing or not, so "read until it goes quiet" never returns — and reading
    far past the fixture is not free either: trailing silence dilutes the alignment
    search, which is how every Voicemeeter endpoint came to score 0.02 while carrying the
    signal perfectly well.
    """
    import numpy as np

    chunks = []
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        payload = capture.read(0.15)
        if payload:
            chunks.append(payload)
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    # float32 interleaved, not int16 — reading it as int16 yields convincing noise.
    raw = np.frombuffer(b"".join(chunks), dtype=np.float32)
    channels = capture.format.channels
    return raw.reshape(-1, channels)[:, 0] if channels > 1 else raw


#: What each candidate endpoint scored, in the order tried. Reported by the suite so a
#: rejection is visible rather than inferred.
REJECTED: list[str] = []


def _channel_score(channel: _Channel, companion: Any, seconds: float) -> tuple[float, str]:
    """Idle, and demonstrably able to carry a signal — decided in one capture session.

    Both questions are answered without closing the stream in between: reopening an
    endpoint immediately after closing it lands in WASAPI's -9999 teardown window, which
    rejected every candidate and left the suite skipping itself into a green result.
    """
    import tempfile
    import threading

    import numpy as np

    from app.audio.devices import play_wav
    from app.audio.wasapi import WasapiCapture

    try:
        capture = WasapiCapture(track="me", queue_seconds=seconds + 20.0, device=companion)
        capture.start()
    except Exception as exc:
        return -1.0, f"will not open ({type(exc).__name__})"
    try:
        time.sleep(0.3)
        idle = _drain(capture, 0.4)
        if float(np.abs(idle).max(initial=0.0)) > 1e-4:
            return -2.0, "busy"
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _chirp(Path(tmp) / "probe.wav", channel, seconds, 300.0, 2500.0)
            player = threading.Thread(
                target=play_wav, args=(fixture,), kwargs={"device_index": channel.render}
            )
            player.start()
            player.join(timeout=seconds + 20)
            heard = _drain(capture, 1.5)
        expected = _chirp_samples(capture.format.rate, seconds, 300.0, 2500.0)
        aligned, whole, offset = _aligned_correlation(expected, heard, capture.format.rate)
        note = (
            f"{len(heard) / max(1, capture.format.rate):.1f}s heard, "
            f"peak {float(np.abs(heard).max(initial=0.0)):.2f}, "
            f"whole {whole:.2f}, offset {offset:.0f}ms"
        )
        return float(aligned), note
    except Exception as exc:
        return -3.0, f"failed ({type(exc).__name__}: {exc})"
    finally:
        capture.stop()


def _aligned_correlation(
    expected: Any, heard: Any, rate: int, window_s: float = 0.5
) -> tuple[float, float, float]:
    """How well the recording carries the fixture, measured window by window.

    Two corrections to the obvious approach, both learned the hard way on real hardware.

    Recording starts before the signal does, so a whole-array correlation is scaled down
    by however much silence sits on either side of it — a fine channel reads as a weak
    one. So: find the lag first, then score from there.

    And a virtual audio device is not a wire. A Voicemeeter strip converts sample rates
    asynchronously, so a six-second chirp slides out of phase by a random amount each
    session: the same endpoint scored 0.33 to 0.93 across runs while the audio was
    perfectly audible and gap-free, and one *physical* endpoint scored 0.971 at 1 ms
    offset. Comparing whole waveforms therefore measures the mixer's clock, not the
    recorder. Half-second windows, each aligned locally, ask the question that matters —
    did this half second of the meeting arrive — and the median is the verdict.
    """
    import numpy as np

    from app.audio.analysis import cross_correlation

    raw_score, lag = cross_correlation(expected, heard, max_lag=int(rate * 3))
    offset_ms = abs(lag) / rate * 1000
    span = int(rate * window_s)
    slack = int(rate * 0.05)  # drift within half a second is tens of samples, not more

    def median_from(start: int) -> float:
        window = heard[start : start + len(expected)]
        if len(window) < span * 2:
            return 0.0
        scores = [
            cross_correlation(expected[at : at + span], window[at : at + span], max_lag=slack)[0]
            for at in range(0, len(window) - span, span)
        ]
        return float(np.median(scores)) if scores else 0.0

    # Both signs. The recording is normally the delayed one, so the lag comes back
    # negative and slicing at `max(0, lag)` silently compares the fixture against the
    # silence before it starts — which scored 0.02 on channels whose whole-track
    # correlation was 0.99. Taking the better of the two readings is sign-agnostic.
    best = max(median_from(max(0, -int(lag))), median_from(max(0, int(lag))))
    if best == 0.0:
        aligned, _ = cross_correlation(expected[: len(heard)], heard)
        return aligned, raw_score, offset_ms
    return best, raw_score, offset_ms


def _chirp_samples(rate: int, seconds: float, start_hz: float, end_hz: float) -> Any:
    """A linear sweep. Analytic, so the same signal can be sampled at two rates and still
    be the same signal — which is what lets the fixture play at 48 kHz and be compared
    against what the recorder wrote at 16 kHz."""
    import numpy as np

    t = np.linspace(0, seconds, int(rate * seconds), endpoint=False)
    return np.sin(2 * np.pi * (start_hz * t + (end_hz - start_hz) * t * t / (2 * seconds)))


def _chirp(path: Path, channel: _Channel, seconds: float, start_hz: float, end_hz: float) -> Path:
    import numpy as np

    mono = (_chirp_samples(channel.rate, seconds, start_hz, end_hz) * 0.45 * 32767).astype(np.int16)
    frames = np.repeat(mono[:, None], channel.channels, axis=1) if channel.channels > 1 else mono
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channel.channels)
        handle.setsampwidth(2)
        handle.setframerate(channel.rate)
        handle.writeframes(frames.tobytes())
    return path


def _wav_samples(path: Path) -> Any:
    import numpy as np

    with wave.open(str(path), "rb") as handle:
        raw = handle.readframes(handle.getnframes())
        channels = handle.getnchannels()
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768.0
    return samples.reshape(-1, channels)[:, 0] if channels > 1 else samples


def _free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])
