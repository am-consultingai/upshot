from __future__ import annotations

from pathlib import Path

import pytest

from app import meta
from app.asr.fake import FakeAsr
from app.pipeline.stages import transcribe
from tests.fixtures.meetings import harness, write_chunks


class Services:
    def __init__(self, asr: FakeAsr) -> None:
        self.asr = asr


def test_language_pinned_after_first_chunk(tmp_path: Path) -> None:
    """Detection runs once and the whole meeting is transcribed in that language.

    Audio is one file per track, so this is one pass per track rather than one per
    committed segment — the model sees the entire track as context.
    """
    h = harness(tmp_path, asr__backend="fake", audio__vad="energy")
    meeting = h.meeting()
    records = write_chunks(meeting.path, seconds=200)
    assert len({r.seq for r in records}) > 1, "more than one committed segment per track"
    backend = FakeAsr(language="en", confidence=0.92)
    ctx = h.context(meeting, services=Services(backend))

    transcribe.run(ctx)

    assert len(backend.detect_calls) == 1, "detection runs once, then the language is pinned"
    languages = {call["language"] for call in backend.transcribe_calls}
    assert languages == {"en"}
    assert len(backend.transcribe_calls) == 2, "one pass per track, not per segment"
    assert {call["wav"].stem for call in backend.transcribe_calls} == {"me", "them"}
    stored = h.dao.require_meeting(meeting.id)
    assert stored.language == "en"
    assert stored.language_conf == pytest.approx(0.92)
    assert backend.unloaded == 1, "the model is unloaded before the LLM stage"


def test_low_confidence_records_review_reason(tmp_path: Path) -> None:
    h = harness(tmp_path, asr__backend="fake", audio__vad="energy")
    meeting = h.meeting()
    write_chunks(meeting.path, seconds=90)
    backend = FakeAsr(language="en", confidence=0.4)
    transcribe.run(h.context(meeting, services=Services(backend)))
    stored = h.dao.require_meeting(meeting.id)
    assert stored.language == "he", "low confidence falls back to the configured default"
    reasons = meta.review_reasons(meeting.path)
    assert reasons and "confident" in reasons[0]


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


def test_participants_reach_initial_prompt(tmp_path: Path) -> None:
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
    ctx = h.context(meeting, services=Services(backend))

    transcribe.run(ctx)

    model = meta.read(meeting.path)["echo"]
    assert model["delay"] == 1683
    assert model["gain"] == pytest.approx(1.0, abs=0.05)
    assert model["reduction"] > 0.9, model
    assert ctx.metrics["crosstalk"] > 0.85

    handed = {call["wav"].parent.name: call["wav"] for call in backend.transcribe_calls}
    assert handed["clean"].stem == "me", "the near track was cleaned before transcription"
    assert handed["audio"].stem == "them", "the far side is already clean"

    with wave.open(str(transcribe.track_path(meeting.path, "me")), "rb") as raw:
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
