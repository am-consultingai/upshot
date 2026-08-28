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


@suite("asr")
def _asr(args: argparse.Namespace) -> list[Check]:
    """Which backend is wired, and whether a local model and CUDA are actually present."""
    from app.asr.local import cuda_library_dirs, supported_compute_type
    from app.asr.models import resolve
    from app.config import Config

    cfg = Config.load()
    kind = str(cfg.get("asr.backend", "local"))
    cuda_dirs = cuda_library_dirs()
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
    """A user-supplied recording becomes the `them` track (single-track import)."""
    import wave

    import numpy as np
    import soxr

    from app.audio.writer import ChunkWriter

    with wave.open(str(wav), "rb") as handle:
        source_rate = handle.getframerate()
        channels = handle.getnchannels()
        raw = handle.readframes(handle.getnframes())
    mono = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
    if channels > 1:
        mono = mono[: len(mono) // channels * channels].reshape(-1, channels).mean(axis=1)
    if source_rate != rate:
        mono = np.asarray(soxr.resample(mono / 32768.0, source_rate, rate)) * 32768.0
    audio = mono
    writer = ChunkWriter(folder, tracks=("them",), rate=rate)
    writer.write_pcm("them", audio.astype(np.int16))
    writer.close()


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
