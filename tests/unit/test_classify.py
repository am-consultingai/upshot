"""The meeting-language classifier: the rule, the windows, the edge cases (story B).

A stub model answers ``detect_language`` with scripted probabilities; no real model and
no real VAD here. The real-model check is at the bottom, marked ``gpu``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from app.asr import classify as classify_module
from app.asr.classify import (
    FORCE_THRESHOLD,
    HEBREW_THRESHOLD,
    SAMPLE_RATE,
    LanguageDecision,
    TrackSpeech,
    WhisperClassifier,
    allocate,
    decide,
    mean_probabilities,
    sample_windows,
)
from app.asr.model_manager import VERIFIED, target_for
from app.asr.models import CLASSIFIER, HEBREW, MODELS, OTHER, ModelNotInstalled
from app.config import default_config

# ------------------------------------------------------------------ the rule


def test_the_thresholds_are_the_decided_ones() -> None:
    assert (HEBREW_THRESHOLD, FORCE_THRESHOLD) == (0.50, 0.60)


@pytest.mark.parametrize(
    ("p_he", "route", "language"),
    [(0.50, HEBREW, "he"), (0.49, OTHER, "en"), (0.98, HEBREW, "he"), (0.0, OTHER, "en")],
)
def test_hebrew_at_half_and_above(p_he: float, route: str, language: str) -> None:
    decision = decide({"he": p_he, "en": 1 - p_he})
    assert (decision.route, decision.language) == (route, language)
    assert decision.p_he == pytest.approx(p_he)


def test_a_hebrew_decision_tells_the_model_hebrew() -> None:
    decision = decide({"he": 0.8, "en": 0.2})
    assert decision.transcribe_language == "he" and not decision.multilingual
    assert decision.p_top == pytest.approx(0.8)


@pytest.mark.parametrize(("p_top", "forced"), [(0.60, True), (0.59, False), (0.99, True)])
def test_the_top_language_is_forced_from_0_60(p_top: float, forced: bool) -> None:
    rest = 1 - p_top
    decision = decide({"es": p_top, "he": rest / 2, "pt": rest / 2})
    assert decision.route == OTHER and decision.language == "es"
    assert decision.forced is forced
    assert decision.p_top == pytest.approx(p_top)
    if forced:
        assert decision.transcribe_language == "es" and not decision.multilingual
    else:
        # Whisper decides per 30 s; the meeting's language is still the top one.
        assert decision.transcribe_language is None and decision.multilingual


def test_the_top_language_is_the_top_non_hebrew_one() -> None:
    """Hebrew can be the single most likely language and still lose at under half."""
    decision = decide({"he": 0.45, "en": 0.30, "ar": 0.25})
    assert decision.language == "en" and not decision.forced
    assert decision.top3 == (("he", 0.45), ("en", 0.30), ("ar", 0.25))


def test_probabilities_are_averaged_over_windows() -> None:
    mean = mean_probabilities([[("he", 0.9), ("en", 0.1)], [("he", 0.2), ("en", 0.8)]])
    assert mean == pytest.approx({"he": 0.55, "en": 0.45})
    assert decide(mean).route == HEBREW


# ------------------------------------------------------------------ windows


def test_windows_follow_the_share_of_speech() -> None:
    assert allocate({"me": 900, "them": 100}) == {"me": 4, "them": 1}
    assert allocate({"me": 300, "them": 300}) in ({"me": 3, "them": 2}, {"me": 2, "them": 3})
    assert sum(allocate({"me": 123, "them": 456}).values()) == 5


def test_a_silent_track_gets_no_window() -> None:
    assert allocate({"me": 0, "them": 600}) == {"them": 5}


def test_a_track_with_any_speech_gets_one_once_there_is_a_minute() -> None:
    assert allocate({"me": 5, "them": 600}) == {"me": 1, "them": 4}
    # Under a minute in all, the share alone decides.
    assert allocate({"me": 2, "them": 55}) == {"them": 5}


def test_no_speech_no_windows() -> None:
    assert allocate({"me": 0, "them": 0}) == {}


def speech(track: str, seconds: float, *, gap_s: float = 0.0) -> TrackSpeech:
    """``seconds`` of speech, in one piece that starts ``gap_s`` into the track."""
    n = int(seconds * SAMPLE_RATE)
    start = int(gap_s * SAMPLE_RATE)
    audio = np.arange(n, dtype=np.float32) / SAMPLE_RATE  # each sample is its own time
    return TrackSpeech(track, audio, [(start, start + n)] if n else [])


def test_windows_are_evenly_spaced_over_the_speech() -> None:
    windows = sample_windows([speech("them", 300)])
    assert len(windows) == 5
    starts = [window.start_s for window, _audio in windows]
    assert starts == pytest.approx([0, 67.5, 135, 202.5, 270])
    assert all(len(audio) == 30 * SAMPLE_RATE for _window, audio in windows)


def test_window_starts_are_on_the_original_track() -> None:
    windows = sample_windows([speech("me", 100, gap_s=40)], total=1)
    assert windows[0][0].start_s == pytest.approx(40)


def test_under_thirty_seconds_is_one_window_of_everything() -> None:
    windows = sample_windows([speech("me", 8), speech("them", 12)])
    assert len(windows) == 1
    window, audio = windows[0]
    assert window.track == "me+them"
    assert len(audio) == 20 * SAMPLE_RATE


# ------------------------------------------------------------------ the classifier


class StubModel:
    """``detect_language`` with scripted probabilities, one answer per call in turn."""

    def __init__(self, answers: list[dict[str, float]]) -> None:
        self.answers = answers
        self.calls = 0

    def detect_language(self, audio: Any = None, **kwargs: Any) -> tuple[str, float, list[Any]]:
        answer = self.answers[self.calls % len(self.answers)]
        self.calls += 1
        ranked = sorted(answer.items(), key=lambda item: -item[1])
        return ranked[0][0], ranked[0][1], ranked


def place_classifier(home: Path) -> Path:
    model = MODELS[CLASSIFIER]
    target = target_for(model.repo, home)
    target.mkdir(parents=True)
    (target / "model.bin").write_bytes(b"m")
    (target / "config.json").write_text("{}", encoding="utf-8")
    (target / VERIFIED).write_text(model.marker, encoding="utf-8")
    return target


def make(
    answers: list[dict[str, float]], seconds: dict[str, float], built: list[dict[str, Any]]
) -> WhisperClassifier:
    model = StubModel(answers)

    def factory(**kwargs: Any) -> StubModel:
        built.append(kwargs)
        return model

    config = default_config(asr__device="cpu")
    return WhisperClassifier(
        config, model_factory=factory, read_speech=lambda track, wav: speech(track, seconds[track])
    )


INPUTS = {"me": Path("me.wav"), "them": Path("them.wav")}


def test_a_hebrew_meeting(app_home: Path) -> None:
    place_classifier(app_home)
    built: list[dict[str, Any]] = []
    classifier = make([{"he": 0.9, "en": 0.1}], {"me": 200, "them": 400}, built)
    decision = classifier.classify(INPUTS)
    assert (decision.language, decision.route, decision.forced) == ("he", HEBREW, True)
    assert len(decision.windows) == 5
    assert {w.track for w in decision.windows} == {"me", "them"}
    assert decision.device == "cpu"
    assert built[0]["compute_type"] == "int8"
    assert built[0]["model_size_or_path"] == str(target_for(MODELS[CLASSIFIER].repo, app_home))
    assert built[0]["cpu_threads"] == max(1, (os.cpu_count() or 4) - 2)


def test_a_spanish_meeting_with_one_hebrew_window(app_home: Path) -> None:
    place_classifier(app_home)
    answers = [{"he": 0.95, "es": 0.05}] + [{"es": 0.97, "he": 0.03}] * 4
    decision = make(answers, {"me": 0, "them": 600}, []).classify(INPUTS)
    assert (decision.language, decision.route, decision.forced) == ("es", OTHER, True)
    assert decision.p_he == pytest.approx((0.95 + 4 * 0.03) / 5)


def test_one_silent_track_is_decided_by_the_other(app_home: Path) -> None:
    place_classifier(app_home)
    decision = make([{"fr": 0.99}], {"me": 0, "them": 300}, []).classify(INPUTS)
    assert decision.language == "fr"
    assert {w.track for w in decision.windows} == {"them"}


def test_no_speech_is_hebrew_without_loading_a_model(app_home: Path) -> None:
    built: list[dict[str, Any]] = []
    classifier = make([{"en": 1.0}], {"me": 0, "them": 0}, built)
    decision = classifier.classify(INPUTS)
    assert (decision.language, decision.route, decision.rule) == ("he", HEBREW, "no speech")
    assert built == [] and classifier.loads == 0


def test_the_model_is_unloaded_before_returning(app_home: Path) -> None:
    place_classifier(app_home)
    classifier = make([{"he": 0.9}], {"me": 100, "them": 100}, [])
    classifier.classify(INPUTS)
    assert (classifier.loads, classifier.unloads) == (1, 1)


def test_a_gpu_that_fails_falls_back_to_the_cpu(
    app_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.asr.local as local

    place_classifier(app_home)
    monkeypatch.setattr(local, "probe_device", lambda config: ("cuda", "int8", []))
    built: list[str] = []
    model = StubModel([{"en": 0.99}])

    def factory(**kwargs: Any) -> StubModel:
        built.append(str(kwargs["device"]))
        if kwargs["device"] == "cuda":
            raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
        return model

    classifier = WhisperClassifier(
        default_config(), model_factory=factory, read_speech=lambda t, w: speech(t, 100)
    )
    decision = classifier.classify(INPUTS)
    assert built == ["cuda", "cpu"] and decision.device == "cpu"


def test_a_missing_classifier_is_a_broken_installation_not_a_download(app_home: Path) -> None:
    classifier = make([{"he": 0.9}], {"me": 100, "them": 100}, [])
    with pytest.raises(ModelNotInstalled, match="classifier"):
        classifier.classify(INPUTS)


def test_the_decision_round_trips_through_json() -> None:
    decision = LanguageDecision(
        "es", OTHER, False, 0.02, 0.55, (("es", 0.55), ("pt", 0.3), ("he", 0.02)),
        (classify_module.Window("them", 12.5, (("es", 0.6),)),), 1.25, "rule", "cpu",
    )  # fmt: skip
    payload = json.loads(json.dumps(decision.as_dict()))
    assert payload["windows"] == 1 and payload["per_window"][0]["track"] == "them"
    assert LanguageDecision.from_dict(payload) == decision


# ------------------------------------------------------------------ real model

EVIDENCE = Path(__file__).resolve().parents[2] / ".research/2026-09-29-multilingual-asr-evidence"


@pytest.mark.gpu
@pytest.mark.slow
@pytest.mark.parametrize("language", ["he", "en", "es", "fr", "ru"])
def test_the_real_classifier_on_fleurs(language: str, tmp_path: Path) -> None:
    """FLEURS clips, levelled as the benchmark does; the classifier model on disk."""
    from app.asr.models import resolve

    config = default_config()
    if not resolve(config, CLASSIFIER).local:
        pytest.skip("whisper-small is not installed; run upshot --prepare")
    source = EVIDENCE / "fleurs" / f"{language}.npz"
    if not source.exists():
        pytest.skip("FLEURS clips not fetched (scripts/asr_bench/fleurs_fetch.py)")
    import wave

    clips = np.load(source)
    audio = np.concatenate([clip / (np.sqrt(np.mean(clip**2)) + 1e-9) * 0.1 for clip in
                            (clips[k] for k in clips.files)])  # fmt: skip
    wav = tmp_path / "them.wav"
    with wave.open(str(wav), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    decision = WhisperClassifier(config).classify({"them": wav})
    assert decision.language == language, decision.as_dict()
    assert decision.p_top >= 0.89, decision.as_dict()
