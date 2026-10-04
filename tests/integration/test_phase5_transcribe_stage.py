from __future__ import annotations

from pathlib import Path

import pytest

from app import meta
from app.asr.fake import FakeAsr, FakeClassifier
from app.audio.writer import track_path
from app.pipeline.stages import transcribe
from tests.fixtures.meetings import harness, write_chunks


class Services:
    def __init__(self, asr: FakeAsr, classifier: FakeClassifier | None = None) -> None:
        self.asr = asr
        self.classifier = classifier


def test_the_meeting_language_comes_from_the_classifier(tmp_path: Path) -> None:
    """The classifier decides what the meeting was in, before transcription, and the
    decision is stored under ``asr.language_detection``, never shown.

    Audio is one file per track, so this is one pass per track rather than one per
    committed segment — the model sees the entire track as context.
    """
    h = harness(tmp_path, asr__backend="fake", audio__vad="energy")
    meeting = h.meeting()
    records = write_chunks(meeting.path, seconds=200)
    assert len({r.seq for r in records}) > 1, "more than one committed segment per track"
    backend = FakeAsr()
    classifier = FakeClassifier("es", p=0.93)
    ctx = h.context(meeting, services=Services(backend, classifier))

    transcribe.run(ctx)

    assert len(classifier.calls) == 1, "classified once per meeting"
    assert set(classifier.calls[0]) == {"me", "them"}
    assert len(backend.transcribe_calls) == 2, "one pass per track, not per segment"
    assert {call["wav"].stem for call in backend.transcribe_calls} == {"me", "them"}
    stored = h.dao.require_meeting(meeting.id)
    assert stored.language == "es"
    assert stored.language_conf == pytest.approx(0.93)
    _segments, payload = transcribe.load_segments(meeting.path)
    assert payload["language"] == "es"
    assert payload["language_source"] == "classifier"
    detection = payload["asr"]["language_detection"]
    assert (detection["language"], detection["route"], detection["forced"]) == ("es", "other", True)
    stored_meta = meta.read(meeting.path)
    assert stored_meta["asr"]["language_detection"] == detection
    assert stored_meta["language"] == "es"
    assert "language_detection_s" in ctx.metrics
    assert backend.unloaded == 1, "the model is unloaded before the LLM stage"


def test_the_fake_asr_brings_a_matching_classifier(tmp_path: Path) -> None:
    """With the fake backend and no classifier given, no real model is loaded."""
    h = harness(tmp_path, asr__backend="fake", audio__vad="energy")
    meeting = h.meeting()
    write_chunks(meeting.path, seconds=60)
    transcribe.run(h.context(meeting, services=Services(FakeAsr(language="en"))))
    assert h.dao.require_meeting(meeting.id).language == "en"


# ------------------------------------------------------- routing (story C, R2/R3)


def route(tmp_path: Path, classifier: FakeClassifier) -> tuple[FakeAsr, object, object]:
    h = harness(tmp_path, asr__backend="fake", audio__vad="energy")
    meeting = h.meeting()
    write_chunks(meeting.path, seconds=60)
    backend = FakeAsr()
    transcribe.run(h.context(meeting, services=Services(backend, classifier)))
    return backend, h.dao.require_meeting(meeting.id), meeting


def test_a_hebrew_meeting_goes_to_the_hebrew_model_told_hebrew(tmp_path: Path) -> None:
    backend, stored, _m = route(tmp_path, FakeClassifier("he"))
    assert backend.roles == ["hebrew"]
    assert [(c["role"], c["language"], c["multilingual"]) for c in backend.transcribe_calls] == [
        ("hebrew", "he", False), ("hebrew", "he", False),
    ]  # fmt: skip
    assert stored.language == "he"


def test_a_spanish_meeting_goes_to_stock_whisper_forced_to_spanish(tmp_path: Path) -> None:
    backend, stored, meeting = route(tmp_path, FakeClassifier("es", p=0.97))
    assert backend.roles == ["other"]
    assert {(c["role"], c["language"], c["multilingual"]) for c in backend.transcribe_calls} == {
        ("other", "es", False)
    }
    assert stored.language == "es"
    segments, _payload = transcribe.load_segments(meeting.path)
    assert any("proyecto" in s.text for s in segments), "the transcript is in Spanish"


def test_an_unclear_meeting_lets_whisper_decide(tmp_path: Path) -> None:
    from app.asr.classify import decide

    unsure = decide({"ru": 0.45, "en": 0.35, "he": 0.2})
    backend, stored, _m = route(tmp_path, FakeClassifier(decision=unsure))
    assert {(c["role"], c["language"], c["multilingual"]) for c in backend.transcribe_calls} == {
        ("other", None, True)
    }
    assert stored.language == "ru", "the meeting's language is still the top one"
    assert stored.language_conf == pytest.approx(0.45)


def test_exactly_one_backend_is_built_per_meeting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R3: one large model per meeting, both tracks through it."""
    from app.asr import factory

    built: list[str] = []
    real = factory.make_backend

    def counting(config: object, role: str = "hebrew") -> object:
        built.append(role)
        return real(config, role)  # type: ignore[arg-type]

    monkeypatch.setattr(factory, "make_backend", counting)
    h = harness(tmp_path, asr__backend="fake", audio__vad="energy", asr__fake_language="ar")
    meeting = h.meeting()
    write_chunks(meeting.path, seconds=60)
    transcribe.run(h.context(meeting, services=None))
    assert built == ["other"]
    assert h.dao.require_meeting(meeting.id).language == "ar"


def test_the_model_used_is_recorded(tmp_path: Path) -> None:
    _backend, _stored, meeting = route(tmp_path, FakeClassifier("es"))
    assert meta.read(meeting.path)["asr"]["role"] == "other"


def test_a_hebrew_meeting_needs_no_review(tmp_path: Path) -> None:
    """There is no detection left to be unsure about, so nothing is flagged for it."""
    h = harness(tmp_path, asr__backend="fake", audio__vad="energy")
    meeting = h.meeting()
    write_chunks(meeting.path, seconds=90)
    transcribe.run(h.context(meeting, services=Services(FakeAsr())))
    assert h.dao.require_meeting(meeting.id).language == "he"
    assert meta.review_reasons(meeting.path) == []


def test_segments_are_on_the_meeting_timeline(tmp_path: Path) -> None:
    h = harness(tmp_path, asr__backend="fake", audio__vad="energy")
    meeting = h.meeting()
    records = write_chunks(meeting.path, seconds=200)
    transcribe.run(h.context(meeting, services=Services(FakeAsr())))
    segments, payload = transcribe.load_segments(meeting.path)
    assert segments
    last_chunk_start = max(r.t0_ms for r in records) / 1000.0
    assert max(s.start for s in segments) >= last_chunk_start
    assert all(s.end > s.start for s in segments)
    assert payload["language"] == "he"
    assert {s.track for s in segments} == {"me", "them"}


def test_transcribe_is_idempotent(tmp_path: Path) -> None:
    h = harness(tmp_path, asr__backend="fake", audio__vad="energy")
    meeting = h.meeting()
    write_chunks(meeting.path, seconds=90)
    backend = FakeAsr()
    services = Services(backend)
    transcribe.run(h.context(meeting, services=services))
    calls = len(backend.transcribe_calls)
    transcribe.run(h.context(meeting, services=services))
    assert len(backend.transcribe_calls) == calls, "the stage found its own output"


def test_participants_reach_initial_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.glossary.ENABLED", True)  # off by default since 2026-09-29
    h = harness(tmp_path, asr__backend="fake", audio__vad="energy")
    meeting = h.meeting()
    h.dao.upsert_term(
        __import__("app.db.dao", fromlist=["GlossaryTerm"]).GlossaryTerm(
            "ArgoCD", kind="tech", aliases="ארגו"
        )
    )
    write_chunks(meeting.path, seconds=90)
    backend = FakeAsr()
    transcribe.run(h.context(meeting, services=Services(backend)))
    prompts = {call["initial_prompt"] for call in backend.transcribe_calls}
    assert any(p and "ArgoCD" in p for p in prompts)


def test_whisper_gets_no_prompt_while_the_glossary_is_off(tmp_path: Path) -> None:
    """Glossary terms and attendee names both stay out of transcription by default."""
    h = harness(tmp_path, asr__backend="fake", audio__vad="energy")
    meeting = h.meeting()
    h.dao.upsert_term(
        __import__("app.db.dao", fromlist=["GlossaryTerm"]).GlossaryTerm(
            "ArgoCD", kind="tech", aliases="ארגו"
        )
    )
    write_chunks(meeting.path, seconds=90)
    backend = FakeAsr()
    transcribe.run(h.context(meeting, services=Services(backend)))
    assert backend.transcribe_calls
    assert all(call["initial_prompt"] is None for call in backend.transcribe_calls)


def test_missing_audio_fails_loudly(tmp_path: Path) -> None:
    h = harness(tmp_path, asr__backend="fake", audio__vad="energy")
    meeting = h.meeting()
    meeting.path.mkdir(parents=True, exist_ok=True)
    with pytest.raises(FileNotFoundError):
        transcribe.run(h.context(meeting, services=Services(FakeAsr())))


# ------------------------------------------------------- the leaking microphone (D37)


def test_a_leaking_microphone_is_cancelled_before_transcription(tmp_path: Path) -> None:
    """The far side must reach the model once, not twice.

    A microphone bus carrying playback records the far side into ``me`` as well, and
    nothing downstream can tell that from two people saying the same thing.
    """
    import wave

    import numpy as np

    h = harness(tmp_path, asr__backend="fake", audio__vad="energy")
    meeting = h.meeting()
    write_chunks(meeting.path, seconds=90, leak=1.0)
    backend = FakeAsr()
    classifier = FakeClassifier()
    ctx = h.context(meeting, services=Services(backend, classifier))

    transcribe.run(ctx)

    model = meta.read(meeting.path)["echo"]
    assert model["delay"] == 1683
    assert model["gain"] == pytest.approx(1.0, abs=0.05)
    assert model["reduction"] > 0.9, model
    assert ctx.metrics["crosstalk"] > 0.85

    handed = {call["wav"].parent.name: call["wav"] for call in backend.transcribe_calls}
    assert handed["clean"].stem == "me", "the near track was cleaned before transcription"
    assert handed["audio"].stem == "them", "the far side is already clean"
    classified = classifier.calls[0]
    assert classified["me"].parent.name == "clean", "classified on the cleaned track too"
    assert classified == {Path(w).stem: w for w in handed.values()}

    with wave.open(str(track_path(meeting.path, "me")), "rb") as raw:
        original = np.frombuffer(raw.readframes(raw.getnframes()), dtype=np.int16)
    assert np.abs(original).max() > 0, "the recording itself is untouched"

    reasons = meta.review_reasons(meeting.path)
    assert any("system audio" in reason for reason in reasons)
    assert not (meeting.path / "audio" / "clean").exists(), "the derived copy is not kept"


def test_echo_cancellation_can_be_turned_off(tmp_path: Path) -> None:
    """The warning survives; only the subtraction goes away."""
    h = harness(tmp_path, asr__backend="fake", audio__vad="energy", audio__echo_cancel="off")
    meeting = h.meeting()
    write_chunks(meeting.path, seconds=90, leak=1.0)
    backend = FakeAsr()

    transcribe.run(h.context(meeting, services=Services(backend)))

    assert "echo" not in meta.read(meeting.path)
    assert {call["wav"].parent.name for call in backend.transcribe_calls} == {"audio"}
    assert any("system audio" in reason for reason in meta.review_reasons(meeting.path))


def test_two_people_talking_is_left_alone(tmp_path: Path) -> None:
    h = harness(tmp_path, asr__backend="fake", audio__vad="energy")
    meeting = h.meeting()
    write_chunks(meeting.path, seconds=90)
    backend = FakeAsr()
    ctx = h.context(meeting, services=Services(backend))

    transcribe.run(ctx)

    assert "echo" not in meta.read(meeting.path)
    assert ctx.metrics["crosstalk"] == 0.0
    assert not meta.review_reasons(meeting.path)
    assert {call["wav"].parent.name for call in backend.transcribe_calls} == {"audio"}


CANCELLED_REASON = "microphone is also capturing system audio (removed on playback)"


def test_a_partial_leak_is_cancelled_only_when_asked(tmp_path: Path) -> None:
    """0.651 was measured on a real recording, where the near voice diluted the copy.

    `auto` leaves it alone — flagging it would flag every meeting held without headphones
    (D36) — and `on` is the escape hatch for a user who knows their bus is leaking.
    """
    for mode, expected in (("auto", False), ("on", True)):
        h = harness(
            tmp_path / mode, asr__backend="fake", audio__vad="energy", audio__echo_cancel=mode
        )
        meeting = h.meeting()
        write_chunks(meeting.path, seconds=90, leak=0.35)
        backend = FakeAsr()
        ctx = h.context(meeting, services=Services(backend))

        transcribe.run(ctx)

        assert 0.3 < ctx.metrics["crosstalk"] < 0.85, ctx.metrics
        assert ("echo" in meta.read(meeting.path)) is expected, mode
        cleaned = "clean" in {call["wav"].parent.name for call in backend.transcribe_calls}
        assert cleaned is expected, mode
        assert meta.review_reasons(meeting.path) == ([CANCELLED_REASON] if expected else [])


# ------------------------------------------------------- installation integrity (story G)


def test_a_missing_model_fails_the_job_by_name_without_a_download(
    tmp_path: Path, app_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R12: nothing is downloaded and nothing stands in; the failure names the model."""
    import shutil

    from app.asr import model_manager
    from app.asr.models import MODELS, ModelNotInstalled
    from app.errors import PermanentError
    from tests.fixtures.models import install_models

    placed = install_models(app_home)
    shutil.rmtree(placed["other"])

    def no_downloads(*args: object, **kwargs: object) -> None:
        raise AssertionError("a download was attempted")

    monkeypatch.setattr(model_manager, "http_fetch", no_downloads)
    monkeypatch.setattr(model_manager.ModelManager, "start", no_downloads)
    h = harness(tmp_path, asr__backend="local", audio__vad="energy")
    meeting = h.meeting()
    write_chunks(meeting.path, seconds=30)
    with pytest.raises(ModelNotInstalled, match=MODELS["other"].repo) as caught:
        transcribe.run(h.context(meeting, services=None))
    assert isinstance(caught.value, PermanentError), "failed once, not retried"
    assert not transcribe.segments_path(meeting.path).exists()


# ------------------------------------------------------- transcribe again as… (story E)


def test_a_chosen_language_skips_the_classifier_and_picks_its_model(tmp_path: Path) -> None:
    h = harness(tmp_path, asr__backend="fake", audio__vad="energy")
    meeting = h.meeting()
    write_chunks(meeting.path, seconds=60)
    classifier = FakeClassifier("he")
    backend = FakeAsr()
    services = Services(backend, classifier)
    transcribe.run(h.context(meeting, services=services))
    assert h.dao.require_meeting(meeting.id).language == "he"

    # Transcribe again as English: the classifier is not asked, stock Whisper is told en.
    meta.update(meeting.path, asr_language_override="en")
    backend.transcribe_calls.clear()
    transcribe.run(h.context(meeting, force=True, services=services))
    assert len(classifier.calls) == 1, "not asked again"
    assert {(c["role"], c["language"]) for c in backend.transcribe_calls} == {("other", "en")}
    stored = h.dao.require_meeting(meeting.id)
    assert stored.language == "en"
    _segments, payload = transcribe.load_segments(meeting.path)
    assert payload["asr"]["language_detection"]["rule"] == "override"
    assert payload["language_source"] == "override"
    assert any("project" in s.text for s in _segments), "replaced by the English transcript"

    # And back as Hebrew.
    meta.update(meeting.path, asr_language_override="he")
    backend.transcribe_calls.clear()
    transcribe.run(h.context(meeting, force=True, services=services))
    assert {(c["role"], c["language"]) for c in backend.transcribe_calls} == {("hebrew", "he")}
    assert h.dao.require_meeting(meeting.id).language == "he"


def test_a_forced_rerun_transcribes_again(tmp_path: Path) -> None:
    """force used to be ignored here: the stage found its own output and skipped."""
    h = harness(tmp_path, asr__backend="fake", audio__vad="energy")
    meeting = h.meeting()
    write_chunks(meeting.path, seconds=30)
    backend = FakeAsr()
    transcribe.run(h.context(meeting, services=Services(backend)))
    calls = len(backend.transcribe_calls)
    transcribe.run(h.context(meeting, force=True, services=Services(backend)))
    assert len(backend.transcribe_calls) == 2 * calls
