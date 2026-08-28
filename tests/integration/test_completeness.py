"""The design's declared-but-easily-forgotten behaviours, each asserted once."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from app.asr.fake import FakeAsr
from app.audio.ingest import UnsupportedAudio, ingest, read_mono
from app.clock import FakeClock
from app.db.dao import GlossaryTerm
from app.pipeline.activity import FakeActivity, FakeRecorderState
from app.pipeline.stages import assemble, transcribe
from app.pipeline.states import JobStage, MeetingState
from app.pipeline.worker import Worker
from tests.fixtures.meetings import harness, write_chunks


class Services:
    def __init__(self, asr: FakeAsr) -> None:
        self.asr = asr


def make_wav(path: Path, seconds: float, rate: int = 44100, channels: int = 2) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    index = np.arange(int(seconds * rate), dtype=np.float64)
    mono = np.sin(2 * np.pi * 220 * index / rate) * 0.3
    payload = np.round(mono * 32767).astype("<i2")
    if channels > 1:
        payload = np.repeat(payload[:, None], channels, axis=1).reshape(-1)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(payload.tobytes())
    return path


# ------------------------------------------------------------------ import


def test_import_becomes_chunks_and_runs_the_pipeline(tmp_path: Path, app_home: Path) -> None:
    from tests.fixtures.api import build_harness

    api = build_harness(tmp_path)
    wav = make_wav(tmp_path / "old-meeting.wav", 150, rate=44100, channels=2)
    with wav.open("rb") as handle:
        response = api.client().post("/api/import", files={"file": ("old-meeting.wav", handle)})
    body = response.json()
    assert response.status_code == 200, body
    assert body["chunks"] >= 2 and body["duration_s"] == 150
    assert body["state"] == MeetingState.RECORDED

    meeting = api.services.dao.require_meeting(body["meeting_id"])
    assert meeting.source == "imported"
    assert (meeting.path / "audio" / "them" / "0001.wav").exists()
    assert api.services.queue.get_by_stage(meeting.id, "transcribe") is not None

    assert api.services.worker is not None
    api.services.worker.drain()
    assert (meeting.path / "summary.html").exists()
    assert api.services.dao.require_meeting(meeting.id).state in (
        MeetingState.RENDERED,
        MeetingState.DELIVERED,
        MeetingState.NEEDS_REVIEW,
    )


def test_import_resamples_to_the_storage_format(tmp_path: Path) -> None:
    wav = make_wav(tmp_path / "in.wav", 3.0, rate=48000, channels=2)
    imported = ingest(wav, tmp_path / "meeting")
    assert imported.duration_s == 3
    assert imported.converted is False
    with wave.open(str(tmp_path / "meeting" / "audio" / "them" / "0001.wav"), "rb") as handle:
        assert handle.getframerate() == 16000
        assert handle.getnchannels() == 1
        assert handle.getnframes() == 3 * 16000


def test_import_rejects_what_it_cannot_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "")  # no ffmpeg anywhere
    bogus = tmp_path / "voice.m4a"
    bogus.write_bytes(b"not audio")
    with pytest.raises(UnsupportedAudio, match="ffmpeg"):
        ingest(bogus, tmp_path / "meeting")


def test_read_mono_averages_channels(tmp_path: Path) -> None:
    wav = make_wav(tmp_path / "stereo.wav", 1.0, rate=16000, channels=2)
    assert len(read_mono(wav)) == 16000


# ------------------------------------------------------------------ glossary


def test_glossary_corrects_the_transcript(tmp_path: Path) -> None:
    """The glossary's second use: aliases become canonical terms before summarization."""
    h = harness(tmp_path, asr__backend="fake", audio__vad="energy")
    meeting = h.meeting()
    h.dao.upsert_term(GlossaryTerm("Kubernetes", kind="tech", aliases="קוברנטיס,k8s"))
    write_chunks(meeting.path, seconds=90)
    services = Services(FakeAsr())
    transcribe.run(h.context(meeting, services=services))

    # rewrite one segment so it contains an alias, then assemble
    import json

    path = transcribe.segments_path(meeting.path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["segments"][0]["text"] = "we deployed it to k8s yesterday"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    ctx = h.context(meeting, JobStage.ASSEMBLE, services=services)
    assemble.run(ctx)
    text = (meeting.path / "transcript.md").read_text(encoding="utf-8")
    assert "Kubernetes" in text and "k8s" not in text
    assert ctx.metrics["glossary_corrections"] == 1


# ------------------------------------------------------------------ policies


def test_scheduled_policy_waits_for_its_window(tmp_path: Path) -> None:
    """`scheduled` must not behave like `asap` — that would be a silent lie."""
    h = harness(tmp_path, job_policy="scheduled")
    clock = FakeClock()
    worker = Worker(
        dao=h.dao,
        queue=h.queue,
        config=h.config,
        stages={"noop": lambda ctx: None},
        clock=clock,
        recorder=FakeRecorderState(False),
        activity=FakeActivity(False),
    )
    meeting = h.meeting()
    h.queue.enqueue(meeting.id, "noop")

    clock.set(clock.now().replace(hour=14))
    assert worker.in_schedule() is False
    assert worker.run_once() is False, "14:00 is outside the nightly window"

    clock.set(clock.now().replace(hour=3))
    assert worker.in_schedule() is True
    assert worker.run_once() is True


def test_after_meeting_policy_yields_to_the_recorder(tmp_path: Path) -> None:
    h = harness(tmp_path, job_policy="after_meeting")
    recorder = FakeRecorderState(True)
    worker = Worker(
        dao=h.dao,
        queue=h.queue,
        config=h.config,
        stages={"noop": lambda ctx: None},
        clock=h.clock,
        recorder=recorder,
    )
    meeting = h.meeting()
    h.queue.enqueue(meeting.id, "noop")
    assert worker.run_once() is False
    recorder.active = False
    assert worker.run_once() is True


def test_asap_policy_runs_during_a_meeting(tmp_path: Path) -> None:
    h = harness(tmp_path, job_policy="asap")
    worker = Worker(
        dao=h.dao,
        queue=h.queue,
        config=h.config,
        stages={"noop": lambda ctx: None},
        clock=h.clock,
        recorder=FakeRecorderState(True),
    )
    meeting = h.meeting()
    h.queue.enqueue(meeting.id, "noop")
    assert worker.run_once() is True, "the GPU profile transcribes while recording"


def test_auto_policy_follows_the_profile(tmp_path: Path) -> None:
    gpu = harness(tmp_path / "gpu", job_policy="auto", profile="gpu-live")
    cpu = harness(tmp_path / "cpu", job_policy="auto", profile="cpu-deferred")
    build = lambda h: Worker(  # noqa: E731
        dao=h.dao, queue=h.queue, config=h.config, stages={}, clock=h.clock
    )
    assert build(gpu).policy == "asap"
    assert build(cpu).policy == "after_meeting"
