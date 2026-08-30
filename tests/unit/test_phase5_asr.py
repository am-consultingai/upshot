from __future__ import annotations

import os
import socket
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

from app.asr.backend import max_consecutive_repeats, sort_segments
from app.asr.factory import make_backend
from app.asr.fake import FakeAsr
from app.asr.language import resolve_language
from app.asr.local import LocalAsr, cuda_library_dirs, probe_device
from app.asr.models import GPU_REPO, resolve
from app.asr.remote import RemoteAsr
from app.audio.vad import TwoStageVad
from app.config import default_config

RATE = 16000


class NoVerifier:
    """Silero disabled: the test fixtures are noise, not speech, and the question here
    is only "does this track carry sound", which the energy gate answers."""

    def available(self) -> bool:
        return False

    def voiced_frames(self, pcm, rate: int = 16000):  # type: ignore[no-untyped-def]
        return []


def gate_only_vad() -> TwoStageVad:
    return TwoStageVad(silero=NoVerifier())


def make_wav(path: Path, seconds: float, *, speech: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    index = np.arange(int(seconds * RATE), dtype=np.float64)
    if speech:
        rng = np.random.default_rng(3)
        payload = (rng.normal(0, 0.15, len(index)) * 32767).astype("<i2")
    else:
        payload = np.zeros(len(index), dtype="<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(payload.tobytes())
    return path


class RecordingModel:
    """A stand-in WhisperModel that records exactly how it was called."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.calls: list[dict[str, object]] = []

    def transcribe(self, path: str, **kwargs: object):  # type: ignore[no-untyped-def]
        self.calls.append({"path": path, **kwargs})

        class RawWord:
            def __init__(self, word: str, start: float, end: float) -> None:
                self.word, self.start, self.end, self.probability = word, start, end, 0.9

        class RawSegment:
            def __init__(self) -> None:
                self.start, self.end, self.text = 0.0, 1.0, "hello world"
                self.words = [RawWord("hello", 0.0, 0.5), RawWord("world", 0.5, 1.0)]
                self.avg_logprob, self.no_speech_prob = -0.1, 0.01

        class Info:
            language, language_probability = "en", 0.99

        return iter([RawSegment()]), Info()

    def detect_language(self, path: str):  # type: ignore[no-untyped-def]
        return "en", 0.87, []


# ------------------------------------------------------------------ fake backend


def test_fake_backend_deterministic(tmp_path: Path) -> None:
    wav = make_wav(tmp_path / "them" / "0001.wav", 30)
    first = FakeAsr().transcribe(wav)
    second = FakeAsr().transcribe(wav)
    assert [s.as_dict() for s in first] == [s.as_dict() for s in second]
    assert first  # and it produced something


def test_segments_monotonic(tmp_path: Path) -> None:
    wav = make_wav(tmp_path / "them" / "0001.wav", 40)
    segments = FakeAsr().transcribe(wav)
    previous_start = -1.0
    for segment in segments:
        assert segment.start >= previous_start
        assert segment.end > segment.start
        previous_start = segment.start
        for word in segment.words:
            assert segment.start - 1e-6 <= word.s <= word.e <= segment.end + 1e-6


def test_sort_segments_is_deterministic(tmp_path: Path) -> None:
    me = FakeAsr().transcribe(make_wav(tmp_path / "me" / "0001.wav", 20))
    them = FakeAsr().transcribe(make_wav(tmp_path / "them" / "0001.wav", 20))
    once = [s.track for s in sort_segments(me + them)]
    twice = [s.track for s in sort_segments(them + me)]
    assert once == twice


def test_fake_repetition_mode(tmp_path: Path) -> None:
    wav = make_wav(tmp_path / "them" / "0001.wav", 30)
    looped = FakeAsr(repetitions=3).transcribe(wav)
    assert max_consecutive_repeats(looped) == 3, "the injected loop is visible to the metric"


@pytest.mark.slow
def test_no_repetition_loop(tmp_path: Path) -> None:
    """3 minutes of silence and one utterance must not produce a repeated segment."""
    config = default_config(asr__backend="fake")
    backend = make_backend(config)
    silence = make_wav(tmp_path / "them" / "0001.wav", 180, speech=False)
    utterance = make_wav(tmp_path / "them" / "0002.wav", 8)
    segments = backend.transcribe(silence) + backend.transcribe(utterance)
    assert max_consecutive_repeats(segments) <= 2, "repetition loop"


# ------------------------------------------------------------------ device probe


def test_device_probe_no_cuda(tmp_path: Path) -> None:
    env: dict[str, str] = {}
    device, compute, registered = probe_device(
        default_config(),
        app_home=tmp_path / "empty-home",
        search_path=[str(tmp_path / "empty-path")],
        system_dirs=(str(tmp_path / "no-cuda"),),
        environ=env,
    )
    assert (device, compute) == ("cpu", "int8")
    assert registered == []
    assert env["CUDA_VISIBLE_DEVICES"] == ""


def test_cuda_dirs_found_in_wheel_layout(tmp_path: Path) -> None:
    lib = "bin" if sys.platform == "win32" else "lib"
    wheel = tmp_path / "site-packages" / "nvidia" / "cublas" / lib
    wheel.mkdir(parents=True)
    (wheel / ("cublas64_12.dll" if sys.platform == "win32" else "libcublas.so.12")).touch()
    dirs = cuda_library_dirs(
        app_home=tmp_path / "home",
        search_path=[str(tmp_path / "site-packages")],
        system_dirs=(),
    )
    assert dirs == [wheel]


def test_cuda_error_falls_back(tmp_path: Path) -> None:
    """A cuBLAS-shaped failure must never fail a transcription."""
    attempts: list[tuple[str, str]] = []

    def factory(**kwargs: object):  # type: ignore[no-untyped-def]
        attempts.append((str(kwargs["device"]), str(kwargs["compute_type"])))
        if kwargs["device"] == "cuda":
            raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
        return RecordingModel(**kwargs)

    config = default_config()
    config.set("asr.device", "cuda")
    config.set("asr.compute_type", "int8")
    backend = LocalAsr(config, model_factory=factory)
    wav = make_wav(tmp_path / "them" / "0001.wav", 2)
    segments = backend.transcribe(wav, language="en")
    assert [device for device, _ in attempts] == ["cuda", "cpu"]
    assert backend.fell_back is True
    assert backend.device == "cpu" and backend.compute_type == "int8"
    assert segments and segments[0].text == "hello world"


def test_warmup_runs_at_load(tmp_path: Path) -> None:
    models: list[RecordingModel] = []

    def factory(**kwargs: object) -> RecordingModel:
        model = RecordingModel(**kwargs)
        models.append(model)
        return model

    backend = LocalAsr(default_config(), model_factory=factory)
    backend.load()
    assert backend.warmups == 1
    assert models[0].calls, "the warmup inference forces the lazy library load at startup"


def test_transcribe_params_are_the_tuned_set(tmp_path: Path) -> None:
    models: list[RecordingModel] = []

    def factory(**kwargs: object) -> RecordingModel:
        model = RecordingModel(**kwargs)
        models.append(model)
        return model

    backend = LocalAsr(default_config(), model_factory=factory)
    backend.transcribe(make_wav(tmp_path / "them" / "0001.wav", 2), language="he")
    call = models[0].calls[-1]
    assert call["condition_on_previous_text"] is False  # the repetition-loop guard
    assert call["vad_filter"] is True
    assert call["word_timestamps"] is True
    assert call["language"] == "he"
    assert call["beam_size"] == 5


def test_initial_prompt_passed(tmp_path: Path) -> None:
    models: list[RecordingModel] = []

    def factory(**kwargs: object) -> RecordingModel:
        model = RecordingModel(**kwargs)
        models.append(model)
        return model

    backend = LocalAsr(default_config(), model_factory=factory)
    prompt = "Kubernetes, ArgoCD, יוסי כהן"
    backend.transcribe(make_wav(tmp_path / "them" / "0001.wav", 2), initial_prompt=prompt)
    assert models[0].calls[-1]["initial_prompt"] == prompt


def test_unload_drops_the_model() -> None:
    backend = LocalAsr(default_config(), model_factory=lambda **kw: RecordingModel(**kw))
    backend.load()
    assert backend.model is not None
    backend.unload()
    assert backend.model is None


@pytest.mark.windows
def test_windows_dll_dirs_registered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform != "win32":
        pytest.skip("PATH/add_dll_directory behaviour is Windows-specific")
    home = tmp_path / "home"
    cuda_dir = home / "cuda" / "nvidia" / "cublas" / "bin"
    cuda_dir.mkdir(parents=True)
    (cuda_dir / "cublas64_12.dll").touch()
    monkeypatch.setenv("PATH", "C:\\Windows\\System32")
    registered: list[str] = []
    monkeypatch.setattr(os, "add_dll_directory", lambda d: registered.append(d))
    config = default_config()
    config.set("asr.device", "cuda")
    device, _compute, dirs = probe_device(config, app_home=home, search_path=[], system_dirs=())
    assert device == "cuda"
    assert str(cuda_dir) in dirs
    assert str(cuda_dir) in registered, "add_dll_directory alone is not enough"
    assert str(cuda_dir) in os.environ["PATH"], "CTranslate2 loads cuBLAS from PATH, lazily"


def test_model_resolution_prefers_configured_path(tmp_path: Path, app_home: Path) -> None:
    model_dir = tmp_path / "ct2-model"
    model_dir.mkdir()
    (model_dir / "model.bin").write_bytes(b"x")
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    config = default_config()
    config.set("asr.model_path", str(model_dir))
    choice = resolve(config)
    assert choice.local is True and choice.reference == str(model_dir)

    empty = default_config()
    fallback = resolve(empty, device="cuda")
    assert fallback.local is False and fallback.reference == GPU_REPO


# ------------------------------------------------------------------ remote


def test_remote_falls_back_when_down(tmp_path: Path) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    fallback = FakeAsr()
    remote = RemoteAsr(f"http://127.0.0.1:{port}", fallback, health_timeout=0.2)
    segments = remote.transcribe(make_wav(tmp_path / "them" / "0001.wav", 20))
    assert segments, "a dead worker still produces a transcript"
    assert remote.used_fallback == 1
    assert remote.warnings and "health check failed" in remote.warnings[0]


# ------------------------------------------------------------------ language


def test_language_detect_prefers_them_track(tmp_path: Path) -> None:
    backend = FakeAsr(language="en", confidence=0.9)
    chunks = {
        "me": make_wav(tmp_path / "me" / "0001.wav", 10, speech=False),
        "them": make_wav(tmp_path / "them" / "0001.wav", 10, speech=True),
    }
    decision = resolve_language(backend, chunks, default_config(), vad=gate_only_vad())
    assert decision.language == "en" and decision.source == "detected"
    assert [p.parent.name for p in backend.detect_calls] == ["them"]


def test_language_detect_falls_back_to_me(tmp_path: Path) -> None:
    backend = FakeAsr(language="en", confidence=0.9)
    chunks = {
        "me": make_wav(tmp_path / "me" / "0001.wav", 10, speech=True),
        "them": make_wav(tmp_path / "them" / "0001.wav", 10, speech=False),
    }
    decision = resolve_language(backend, chunks, default_config(), vad=gate_only_vad())
    assert decision.language == "en"
    assert [p.parent.name for p in backend.detect_calls] == ["me"]


def test_low_confidence_uses_default(tmp_path: Path) -> None:
    backend = FakeAsr(language="en", confidence=0.4)
    chunks = {"them": make_wav(tmp_path / "them" / "0001.wav", 10)}
    decision = resolve_language(backend, chunks, default_config(), vad=gate_only_vad())
    assert decision.language == "he"  # the configured default
    assert decision.confidence == 0.4
    assert decision.needs_review is True
    assert decision.review_reason


def test_fixed_mode_skips_detection(tmp_path: Path) -> None:
    backend = FakeAsr(language="en", confidence=0.99)
    config = default_config(asr__language_mode="fixed")
    chunks = {"them": make_wav(tmp_path / "them" / "0001.wav", 10)}
    decision = resolve_language(backend, chunks, config, vad=gate_only_vad())
    assert decision.language == "he" and decision.source == "fixed"
    assert backend.detect_calls == []


def test_no_speech_anywhere_uses_default(tmp_path: Path) -> None:
    backend = FakeAsr(language="en")
    chunks = {
        "me": make_wav(tmp_path / "me" / "0001.wav", 10, speech=False),
        "them": make_wav(tmp_path / "them" / "0001.wav", 10, speech=False),
    }
    decision = resolve_language(backend, chunks, default_config(), vad=gate_only_vad())
    assert decision.language == "he" and decision.needs_review is True
    assert backend.detect_calls == []


# ------------------------------------------------------------------ glossary prompt


def test_glossary_prompt_respects_token_cap() -> None:
    from app.glossary import Entry, initial_prompt

    entries = [Entry(f"term-{index:03d}", aliases=("alias",)) for index in range(200)]
    prompt = initial_prompt(entries, max_tokens=20)
    assert prompt is not None
    assert len(prompt) <= 20 * 3.0
    assert "term-000" in prompt
    assert "term-199" not in prompt


def test_glossary_prompt_includes_participants_and_tail() -> None:
    from app.glossary import Entry, initial_prompt

    prompt = initial_prompt(
        [Entry("ArgoCD", aliases=("ארגו",))],
        participants=("יוסי כהן",),
        previous_sentence="ואז דיברנו על הפריסה",
        max_tokens=200,
    )
    assert prompt is not None
    assert "יוסי כהן" in prompt and "ArgoCD" in prompt and "הפריסה" in prompt


def test_glossary_prompt_is_none_when_empty() -> None:
    from app.glossary import initial_prompt

    assert initial_prompt([]) is None


def test_glossary_corrections_apply_aliases() -> None:
    from app.glossary import Entry, apply_corrections

    entries = [Entry("Kubernetes", aliases=("קוברנטיס", "k8s"))]
    assert apply_corrections("we deployed to k8s", entries) == "we deployed to Kubernetes"


@pytest.mark.windows
@pytest.mark.slow
def test_transcribe_speech_fixture(tmp_path: Path) -> None:
    """T1: a known English sentence must survive a real local transcription."""
    from tests.fixtures import speech

    if not speech.available():
        pytest.skip("SAPI is only available on Windows")
    config = default_config()
    from app.asr.models import resolve

    if not resolve(config).local:
        pytest.skip("no local ASR model on this machine; set asr.model_path")
    sentence = "The quick brown fox jumps over the lazy dog near the river bank"
    wav = speech.synth(sentence, tmp_path / "them" / "0001.wav")
    backend = LocalAsr(config)
    segments = backend.transcribe(wav, language="en")
    text = " ".join(segment.text for segment in segments).lower()
    expected = [word for word in sentence.lower().split() if len(word) > 2]
    hits = sum(1 for word in expected if word in text)
    assert hits / len(expected) >= 0.7, f"only {hits}/{len(expected)} words survived: {text!r}"
    backend.unload()


@pytest.mark.windows
@pytest.mark.slow
def test_detect_english_fixture(tmp_path: Path) -> None:
    """T1: an English fixture detects as English, and the confidence is reported."""
    from tests.fixtures import speech

    if not speech.available():
        pytest.skip("SAPI is only available on Windows")
    config = default_config()
    from app.asr.models import resolve

    if not resolve(config).local:
        pytest.skip("no local ASR model on this machine; set asr.model_path")
    wav = speech.synth(
        "Good morning everyone, can you hear me? Let us start the weekly sync.",
        tmp_path / "them" / "0001.wav",
    )
    backend = LocalAsr(config)
    language, confidence = backend.detect_language(wav)
    print(f"language-detection confidence: {confidence:.3f}")
    assert language == "en"
    assert confidence >= 0.6, f"confidence {confidence:.2f} is below the one-chunk bar"
    backend.unload()


# ------------------------------------------------------------------ configured CUDA dir


def test_configured_cuda_dir_is_used(tmp_path: Path) -> None:
    """DESIGN.md §2: the app adopts an existing CUDA directory rather than downloading."""
    cuda = tmp_path / "Scripts"
    cuda.mkdir()
    for name in ("cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll"):
        (cuda / name).touch()

    # without the config key the probe cannot see it
    assert cuda_library_dirs(app_home=tmp_path / "home", search_path=[], system_dirs=()) == []

    # with it, it is found first
    found = cuda_library_dirs(
        configured=str(cuda), app_home=tmp_path / "home", search_path=[], system_dirs=()
    )
    assert found == [cuda]


def test_configured_cuda_dir_selects_the_gpu(tmp_path: Path) -> None:
    cuda = tmp_path / "Scripts"
    cuda.mkdir()
    (cuda / "cublas64_12.dll").touch()
    config = default_config()
    config.set("asr.cuda_dir", str(cuda))
    config.set("asr.compute_type", "int8")  # Pascal: DESIGN.md §20.5
    env: dict[str, str] = {}
    device, compute, registered = probe_device(
        config, app_home=tmp_path / "home", search_path=[], system_dirs=(), environ=env
    )
    assert device == "cuda"
    assert compute == "int8"
    assert str(cuda) in registered
    assert "CUDA_VISIBLE_DEVICES" not in env, "the GPU must not be disabled"


def test_configured_cuda_dir_without_cublas_is_ignored(tmp_path: Path) -> None:
    """A wrong path degrades to CPU with a warning, never a crash mid-meeting."""
    empty = tmp_path / "not-cuda"
    empty.mkdir()
    config = default_config()
    config.set("asr.cuda_dir", str(empty))
    device, compute, registered = probe_device(
        config, app_home=tmp_path / "home", search_path=[], system_dirs=(), environ={}
    )
    assert (device, compute, registered) == ("cpu", "int8", [])


def test_configured_cuda_dir_accepts_a_list(tmp_path: Path) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    for folder in (first, second):
        folder.mkdir()
        (folder / "cublas64_12.dll").touch()
    found = cuda_library_dirs(
        configured=[str(first), str(second)],
        app_home=tmp_path / "home",
        search_path=[],
        system_dirs=(),
    )
    assert found == [first, second]
