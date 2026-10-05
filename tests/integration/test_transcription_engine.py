"""The file engine (D86): one file in, one transcript out, no meeting anywhere."""

from __future__ import annotations

import shutil
import subprocess
import threading
import wave
from pathlib import Path

import numpy as np
import pytest

from app.asr.fake import FakeAsr, FakeClassifier
from app.audio import ingest
from app.audio.ingest import UnsupportedAudio
from app.config import Config, default_config
from app.errors import Cancelled, Preempted
from app.transcription import engine
from app.transcription.types import PHASES, FileTranscript, Options, overall_progress
from tests.fixtures.meetings import speechish

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


class Services:
    def __init__(self, asr: FakeAsr | None = None, classifier: FakeClassifier | None = None):
        self.asr = asr or FakeAsr()
        self.classifier = classifier


def config(**overrides: object) -> Config:
    defaults: dict[str, object] = {"asr__backend": "fake", "audio__vad": "energy"}
    defaults.update(overrides)
    return default_config(**defaults)  # type: ignore[arg-type]


def write_wav(path: Path, seconds: float, rate: int = 16000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(speechish(seconds, rate=rate).tobytes())
    return path


def make_video(path: Path, seconds: int, *, audio: bool = True) -> Path:
    """A tiny mp4: a test pattern, with or without a tone."""
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-f", "lavfi", "-i", f"testsrc=size=64x48:rate=5:duration={seconds}"]  # fmt: skip
    if audio:
        command += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    command += ["-c:v", "mpeg4", *(["-c:a", "aac"] if audio else []), "-shortest", str(path)]
    subprocess.run(command, check=True)
    return path


class Recorder:
    def __init__(self) -> None:
        self.progress: list[tuple[str, float]] = []
        self.checkpoints: list[str] = []

    def on_progress(self, phase: str, fraction: float) -> None:
        self.progress.append((phase, fraction))

    def checkpoint(self) -> None:
        self.checkpoints.append(self.progress[-1][0])

    @staticmethod
    def stop_check() -> None:
        return None


def run(source: Path, tmp_path: Path, *, options: Options | None = None, services=None,
        cfg: Config | None = None, recorder: Recorder | None = None) -> FileTranscript:  # fmt: skip
    rec = recorder or Recorder()
    return engine.transcribe_file(
        source,
        tmp_path / "work",
        config=cfg or config(),
        options=options or Options(),
        services=services if services is not None else Services(),
        stop_check=rec.stop_check,
        on_progress=rec.on_progress,
        checkpoint=rec.checkpoint,
    )


# ----------------------------------------------------------------------- the happy path


@needs_ffmpeg
def test_a_file_is_transcribed_with_no_meeting(tmp_path: Path) -> None:
    source = write_wav(tmp_path / "in" / "clip.wav", 40)
    before = source.read_bytes()
    services = Services(FakeAsr(), FakeClassifier("he"))
    rec = Recorder()

    result = run(source, tmp_path, services=services, recorder=rec)

    assert result.segments, "the fake ASR produced text"
    assert result.duration_s == pytest.approx(40, abs=0.1)
    assert (result.language, result.language_source) == ("he", "classifier")
    assert {s.speaker for s in result.segments} == {"S1"}, "a file has no 'me'"
    assert [s.id for s in result.segments] == list(range(len(result.segments)))
    assert source.read_bytes() == before, "the source is never touched"
    assert not (tmp_path / "work" / engine.WAV_NAME).exists(), "the WAV goes when the job ends"
    assert services.classifier is not None
    assert services.classifier.calls == [{"them": tmp_path / "work" / engine.WAV_NAME}]
    assert services.asr.unloaded == 1


@needs_ffmpeg
def test_phases_report_in_order_and_progress_only_grows(tmp_path: Path) -> None:
    rec = Recorder()
    run(write_wav(tmp_path / "clip.wav", 30), tmp_path, cfg=config(asr__diarization="fake"),
        recorder=rec)  # fmt: skip
    phases = list(dict.fromkeys(phase for phase, _ in rec.progress))
    assert phases == list(PHASES)
    overall = [overall_progress(phase, fraction) for phase, fraction in rec.progress]
    assert overall == sorted(overall)
    assert overall[-1] == pytest.approx(1.0)
    transcribing = [f for phase, f in rec.progress if phase == "transcribe"]
    assert len(transcribing) > 3, "progress during ASR, segment by segment"


@needs_ffmpeg
def test_a_recording_can_stop_it_only_between_phases(tmp_path: Path) -> None:
    rec = Recorder()
    run(write_wav(tmp_path / "clip.wav", 20), tmp_path, cfg=config(asr__diarization="fake"),
        recorder=rec)  # fmt: skip
    assert rec.checkpoints == ["decode", "check", "language", "transcribe"]


@needs_ffmpeg
def test_a_preemption_at_a_boundary_stops_it_and_cleans_up(tmp_path: Path) -> None:
    class Preempting(Recorder):
        def checkpoint(self) -> None:
            if self.progress[-1][0] == "language":
                raise Preempted("a meeting started")

    services = Services()
    with pytest.raises(Preempted):
        run(write_wav(tmp_path / "clip.wav", 20), tmp_path, services=services,
            recorder=Preempting())  # fmt: skip
    assert services.asr.transcribe_calls == [], "stopped before ASR began"
    assert not (tmp_path / "work" / engine.WAV_NAME).exists()


@needs_ffmpeg
def test_a_video_is_transcribed_from_its_audio(tmp_path: Path) -> None:
    services = Services()
    result = run(make_video(tmp_path / "clip.mp4", 12), tmp_path, services=services)
    assert result.duration_s == pytest.approx(12, abs=0.5)
    assert result.segments


@needs_ffmpeg
def test_a_video_with_no_audio_is_refused(tmp_path: Path) -> None:
    with pytest.raises(UnsupportedAudio, match="has no audio"):
        run(make_video(tmp_path / "silent.mp4", 3, audio=False), tmp_path)


def test_ffmpeg_reads_local_files_only_and_never_decodes_video(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []

    class Popen:
        def __init__(self, command: list[str], **kwargs: object) -> None:
            seen.append(command)
            write_wav(Path(command[-1]), 2)

        def wait(self, timeout: float | None = None) -> int:
            return 0

    monkeypatch.setattr(ingest, "ffmpeg_path", lambda config=None: "ffmpeg")
    monkeypatch.setattr(ingest.subprocess, "Popen", Popen)
    run(tmp_path / "clip.m4a", tmp_path)
    command = seen[0]
    assert command[command.index("-protocol_whitelist") + 1] == "file"
    assert command.index("-protocol_whitelist") < command.index("-i") < command.index("-vn")


# ----------------------------------------------------------------------- languages, prompt


@needs_ffmpeg
def test_a_named_language_skips_the_classifier(tmp_path: Path) -> None:
    services = Services(FakeAsr(), FakeClassifier("he"))
    result = run(write_wav(tmp_path / "clip.wav", 20), tmp_path,
                 options=Options(language="es"), services=services)  # fmt: skip
    assert services.classifier is not None and services.classifier.calls == []
    assert (result.language, result.language_source) == ("es", "override")
    assert services.asr.roles == ["other"]
    assert services.asr.transcribe_calls[0]["language"] == "es"


@needs_ffmpeg
def test_the_prompt_reaches_whisper_and_an_empty_one_is_none(tmp_path: Path) -> None:
    services = Services()
    run(write_wav(tmp_path / "a.wav", 10), tmp_path, options=Options(prompt="Dibra"),
        services=services)  # fmt: skip
    run(write_wav(tmp_path / "b.wav", 10), tmp_path, services=services)
    prompts = [call["initial_prompt"] for call in services.asr.transcribe_calls]
    assert prompts == ["Dibra", None]


# ----------------------------------------------------------------------- speakers


@needs_ffmpeg
def test_speakers_are_numbered_in_order_of_first_appearance(tmp_path: Path) -> None:
    cfg = config(asr__diarization="fake", asr__diarization_fake_speakers=3)
    result = run(write_wav(tmp_path / "clip.wav", 60), tmp_path, cfg=cfg)
    assert result.diarized
    order = list(dict.fromkeys(s.speaker for s in result.segments))
    assert order == [f"S{n}" for n in range(1, len(order) + 1)]
    assert len(order) > 1


@needs_ffmpeg
def test_without_diarization_everyone_is_s1(tmp_path: Path) -> None:
    cfg = config(asr__diarization="fake", asr__diarization_fake_speakers=3)
    result = run(write_wav(tmp_path / "clip.wav", 60), tmp_path, cfg=cfg,
                 options=Options(diarize=False))  # fmt: skip
    assert not result.diarized
    assert {s.speaker for s in result.segments} == {"S1"}


@needs_ffmpeg
def test_a_long_file_is_diarized_like_a_meeting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No skip past LONG_TRACK_MINUTES (R7): the meeting stage only warns."""
    from app.asr import diarize

    monkeypatch.setattr(diarize, "LONG_TRACK_MINUTES", 0)
    cfg = config(asr__diarization="fake", asr__diarization_fake_speakers=2)
    assert run(write_wav(tmp_path / "clip.wav", 30), tmp_path, cfg=cfg).diarized


@needs_ffmpeg
def test_a_diarizer_failure_leaves_one_speaker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.asr import diarize

    def broken(self: object, samples: np.ndarray, rate: int) -> list[object]:
        raise RuntimeError("onnx fell over")

    monkeypatch.setattr(diarize.FakeDiarizer, "diarize", broken)
    result = run(
        write_wav(tmp_path / "clip.wav", 30), tmp_path, cfg=config(asr__diarization="fake")
    )
    assert not result.diarized
    assert {s.speaker for s in result.segments} == {"S1"}


# ----------------------------------------------------------------------- edges


@needs_ffmpeg
def test_no_speech_is_an_empty_transcript_not_an_error(tmp_path: Path) -> None:
    result = run(write_wav(tmp_path / "blip.wav", 0.5), tmp_path)
    assert result.segments == ()
    assert (result.language, result.language_conf, result.language_source) == (None, None, "none")
    payload = result.as_result(id="tr_x", source_name="blip.wav")
    assert payload["segments"] == [] and payload["speakers"] == []


@needs_ffmpeg
def test_a_missing_model_fails_by_name_without_a_download(
    tmp_path: Path, app_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.asr import model_manager
    from app.asr.models import MODELS, ModelNotInstalled
    from tests.fixtures.models import install_models

    placed = install_models(app_home)
    shutil.rmtree(placed["other"])

    def no_downloads(*args: object, **kwargs: object) -> None:
        raise AssertionError("a download was attempted")

    monkeypatch.setattr(model_manager, "http_fetch", no_downloads)
    with pytest.raises(ModelNotInstalled, match=MODELS["other"].repo):
        engine.transcribe_file(
            write_wav(tmp_path / "clip.wav", 5),
            tmp_path / "work",
            config=config(asr__backend="local"),
            options=Options(),
            services=None,
            stop_check=Recorder.stop_check,
            on_progress=Recorder().on_progress,
        )
    assert not (tmp_path / "work" / engine.WAV_NAME).exists()


@needs_ffmpeg
def test_cancel_during_asr_stops_at_the_next_segment(tmp_path: Path) -> None:
    services = Services()

    class Cancelling(Recorder):
        def stop_check(self) -> None:  # type: ignore[override]
            if any(phase == "transcribe" and f > 0 for phase, f in self.progress):
                raise Cancelled("cancelled by the user")

    with pytest.raises(Cancelled):
        run(write_wav(tmp_path / "clip.wav", 120), tmp_path, services=services,
            recorder=Cancelling())  # fmt: skip
    assert services.asr.on_segment is None and services.asr.stop_check is None, "hooks reset"
    assert services.asr.unloaded == 1
    assert not (tmp_path / "work" / engine.WAV_NAME).exists()


@needs_ffmpeg
def test_hooks_are_reset_after_a_finished_job(tmp_path: Path) -> None:
    """A shared backend must not report a finished file's progress during a meeting."""
    services = Services()
    run(write_wav(tmp_path / "clip.wav", 10), tmp_path, services=services)
    assert services.asr.on_segment is None and services.asr.stop_check is None


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")
def test_cancel_kills_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A conversion that would take minutes stops within a poll of the cancel."""
    slow = tmp_path / "slow-ffmpeg"
    slow.write_text("#!/bin/sh\nexec sleep 60\n", encoding="utf-8")
    slow.chmod(0o755)
    monkeypatch.setattr(ingest, "ffmpeg_path", lambda config=None: str(slow))
    monkeypatch.setattr(ingest, "POLL_S", 0.05)
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()

    class Cancelling(Recorder):
        def stop_check(self) -> None:  # type: ignore[override]
            if cancel.is_set():
                raise Cancelled("cancelled by the user")

    import time

    started = time.monotonic()
    with pytest.raises(Cancelled):
        run(tmp_path / "clip.mp3", tmp_path, recorder=Cancelling())
    assert time.monotonic() - started < 5
    assert not (tmp_path / "work" / engine.WAV_NAME).exists()


# ----------------------------------------------------------------------- the contract


@needs_ffmpeg
def test_the_result_round_trips_through_json(tmp_path: Path) -> None:
    import json

    cfg = config(asr__diarization="fake", asr__diarization_fake_speakers=2)
    result = run(write_wav(tmp_path / "clip.wav", 30), tmp_path, cfg=cfg)
    payload = json.loads(json.dumps(result.as_result(id="tr_abc", source_name="שיחה.wav")))
    assert payload["version"] == 1
    assert set(payload["segments"][0]) == {"id", "start", "end", "speaker", "text", "words"}
    assert set(payload["segments"][0]["words"][0]) == {"w", "s", "e", "p"}
    back = FileTranscript.from_result(payload)
    assert back.as_result(id="tr_abc", source_name="שיחה.wav") == payload
