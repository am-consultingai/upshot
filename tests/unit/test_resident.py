"""GPU Whisper models kept for the next job (D108, z8tj1hwjvw)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.asr import classify as classify_module
from app.asr import local as local_module
from app.asr.classify import WhisperClassifier
from app.asr.local import MIN_VRAM_MB, LocalAsr
from app.asr.models import CLASSIFIER, HEBREW, OTHER
from app.asr.resident import RESIDENT, Key, Resident, keep_loaded_s
from app.config import default_config
from app.errors import PermanentError
from app.llm.client import OllamaClient, system_blocks
from tests.unit.test_classify import StubModel, speech
from tests.unit.test_phase5_asr import RecordingModel, make_wav


class FakeTimer:
    def __init__(self, seconds: float, action: Callable[[], None]) -> None:
        self.seconds, self.action = seconds, action
        self.started = self.cancelled = False

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True


def resident() -> tuple[Resident, list[FakeTimer]]:
    timers: list[FakeTimer] = []

    def make(seconds: float, action: Callable[[], None]) -> FakeTimer:
        timers.append(FakeTimer(seconds, action))
        return timers[-1]

    return Resident(timer=make), timers


A = Key(HEBREW, "ivrit", "int8")
B = Key(CLASSIFIER, "small", "int8")


def test_a_parked_model_is_taken_once() -> None:
    held, _ = resident()
    model = object()
    held.park(A, model, idle_s=60)
    assert held.take(A) is model
    assert held.take(A) is None
    assert held.reuses == 1


def test_another_key_is_not_answered() -> None:
    held, _ = resident()
    held.park(A, object(), idle_s=60)
    assert held.take(Key(HEBREW, "ivrit", "float16")) is None
    assert held.held() == [A]


def test_no_keeping_when_the_time_is_zero() -> None:
    held, timers = resident()
    held.park(A, object(), idle_s=0)
    assert held.held() == [] and timers == []


def test_idle_time_frees_everything() -> None:
    held, timers = resident()
    held.park(A, object(), idle_s=300)
    held.park(B, object(), idle_s=300)
    assert timers[-1].seconds == 300 and timers[-1].started
    assert timers[0].cancelled, "each park restarts the wait"
    timers[-1].action()
    assert held.held() == []


def test_a_timer_from_before_the_last_park_does_nothing() -> None:
    held, timers = resident()
    held.park(A, object(), idle_s=300)
    first = timers[-1]
    model = held.take(A)
    held.park(A, model, idle_s=300)
    first.action()  # fired just as it was cancelled
    assert held.held() == [A]


def test_a_model_in_use_is_not_freed_by_the_timer() -> None:
    held, timers = resident()
    held.park(A, object(), idle_s=300)
    in_use = held.take(A)
    timers[-1].action()
    assert in_use is not None and held.held() == []
    held.park(A, in_use, idle_s=300)
    assert held.held() == [A]


def test_drop_all_counts_and_stops_the_timer() -> None:
    held, timers = resident()
    held.park(A, object(), idle_s=300)
    held.park(B, object(), idle_s=300)
    assert held.drop_all() == 2
    assert timers[-1].cancelled and held.held() == []
    assert held.drop_all() == 0


def test_room_kept_when_both_fit_the_card() -> None:
    held, _ = resident()
    held.park(B, object(), idle_s=300)
    held.make_room(HEBREW, lambda: 8192, MIN_VRAM_MB)
    assert held.held() == [B]


def test_room_made_on_a_small_card() -> None:
    held, _ = resident()
    held.park(B, object(), idle_s=300)
    held.make_room(HEBREW, lambda: 2048, MIN_VRAM_MB)  # 1024 + 2048 > 2048
    assert held.held() == []


def test_room_made_when_the_card_cannot_be_read() -> None:
    held, _ = resident()
    held.park(B, object(), idle_s=300)
    held.make_room(OTHER, lambda: None, MIN_VRAM_MB)
    assert held.held() == []


def test_nothing_held_never_asks_the_card() -> None:
    held, _ = resident()

    def vram() -> int:
        raise AssertionError("nvidia-smi must not run when nothing is held")

    held.make_room(HEBREW, vram, MIN_VRAM_MB)


def test_keep_loaded_from_config() -> None:
    assert keep_loaded_s(default_config()) == 300
    assert keep_loaded_s(default_config(asr__keep_loaded_s=0)) == 0


# -- the transcriber -------------------------------------------------------


@pytest.fixture
def big_card(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_module, "cached_gpu_memory_mb", lambda: 8192)


def gpu_asr(built: list[RecordingModel], **config: Any) -> LocalAsr:
    def factory(**kwargs: object) -> RecordingModel:
        built.append(RecordingModel(**kwargs))
        return built[-1]

    return LocalAsr(default_config(asr__device="cuda", asr__compute_type="int8", **config),
                    model_factory=factory)  # fmt: skip


@pytest.mark.usefixtures("installed_models", "big_card")
def test_the_next_file_reuses_the_gpu_model(tmp_path: Path) -> None:
    built: list[RecordingModel] = []
    wav = make_wav(tmp_path / "them" / "0001.wav", 2)
    first = gpu_asr(built)
    first.transcribe(wav, language="he")
    first.park()
    assert first.model is None and len(RESIDENT.held()) == 1
    second = gpu_asr(built)
    second.transcribe(wav, language="he")
    assert len(built) == 1, "one model for both files: a new one each time leaked memory"
    assert second.reused == 1 and second.warmups == 0, "a kept model is already warm"
    assert RESIDENT.held() == []


@pytest.mark.usefixtures("installed_models", "big_card")
def test_unload_frees_the_kept_models_too(tmp_path: Path) -> None:
    built: list[RecordingModel] = []
    first = gpu_asr(built)
    first.load()
    first.park()
    other = gpu_asr(built)
    other.unload()  # the meeting stage, before the LLM (DESIGN.md §20.4)
    assert RESIDENT.held() == []
    gpu_asr(built).load()
    assert len(built) == 2


@pytest.mark.usefixtures("installed_models")
def test_the_cpu_model_is_never_kept(tmp_path: Path) -> None:
    built: list[RecordingModel] = []

    def factory(**kwargs: object) -> RecordingModel:
        built.append(RecordingModel(**kwargs))
        return built[-1]

    backend = LocalAsr(default_config(asr__device="cpu"), model_factory=factory)
    backend.load()
    backend.park()
    assert backend.model is None and RESIDENT.held() == []


@pytest.mark.usefixtures("installed_models", "big_card")
def test_a_cpu_fallback_is_never_kept(tmp_path: Path) -> None:
    def factory(**kwargs: object) -> RecordingModel:
        if kwargs["device"] == "cuda":
            raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
        return RecordingModel(**kwargs)

    backend = LocalAsr(default_config(asr__device="cuda"), model_factory=factory)
    backend.load()
    assert backend.fell_back
    backend.park()
    assert RESIDENT.held() == []


@pytest.mark.usefixtures("installed_models")
def test_a_small_card_frees_the_classifier_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_module, "cached_gpu_memory_mb", lambda: 2048)
    RESIDENT.park(Key(CLASSIFIER, "small", "int8"), object(), idle_s=300)
    gpu_asr([]).load()
    assert RESIDENT.held() == []


@pytest.mark.usefixtures("installed_models", "big_card")
def test_keep_loaded_zero_keeps_nothing() -> None:
    backend = gpu_asr([], asr__keep_loaded_s=0)
    backend.load()
    backend.park()
    assert RESIDENT.held() == []


# -- the classifier --------------------------------------------------------


@pytest.mark.usefixtures("installed_models", "big_card")
def test_the_classifier_reuses_its_gpu_model() -> None:
    built: list[dict[str, Any]] = []
    model = StubModel([{"he": 0.9, "en": 0.1}])

    def factory(**kwargs: Any) -> StubModel:
        built.append(kwargs)
        return model

    def classifier() -> WhisperClassifier:
        return WhisperClassifier(
            default_config(asr__device="cuda"),
            model_factory=factory,
            read_speech=lambda track, wav: speech(track, 120),
        )

    inputs = {"them": Path("them.wav")}
    classifier().classify(inputs)
    assert [key.role for key in RESIDENT.held()] == [CLASSIFIER]
    again = classifier()
    again.classify(inputs)
    assert len(built) == 1 and again.reused == 1
    assert [key.role for key in RESIDENT.held()] == [CLASSIFIER]


@pytest.mark.usefixtures("installed_models")
def test_the_cpu_classifier_is_never_kept() -> None:
    model = StubModel([{"he": 0.9, "en": 0.1}])
    WhisperClassifier(
        default_config(asr__device="cpu"),
        model_factory=lambda **kwargs: model,
        read_speech=lambda track, wav: speech(track, 120),
    ).classify({"them": Path("them.wav")})
    assert RESIDENT.held() == []


def test_classifier_module_uses_the_shared_set() -> None:
    assert classify_module.RESIDENT is RESIDENT


# -- the local LLM ---------------------------------------------------------


def test_a_local_llm_request_frees_the_kept_models() -> None:
    RESIDENT.park(A, object(), idle_s=300)
    seen: list[list[Key]] = []

    def transport(payload: dict[str, Any]) -> str:
        seen.append(RESIDENT.held())
        return json.dumps({"title": "t"})

    client = OllamaClient(default_config(), transport=transport)
    with pytest.raises(PermanentError):  # the reply's shape is not the point here
        client.complete_json(system_blocks=system_blocks("s", None), user="transcript")
    assert seen and seen[0] == [], "Whisper is gone before Ollama loads its model"
