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
    h = harness(tmp_path, asr__backend="fake", audio__vad="energy")
    meeting = h.meeting()
    records = write_chunks(meeting.path, seconds=200)
    assert len({r.seq for r in records}) > 1, "more than one chunk per track"
    backend = FakeAsr(language="en", confidence=0.92)
    ctx = h.context(meeting, services=Services(backend))

    transcribe.run(ctx)

    assert len(backend.detect_calls) == 1, "detection runs once, then the language is pinned"
    languages = {call["language"] for call in backend.transcribe_calls}
    assert languages == {"en"}
    assert len(backend.transcribe_calls) == len(records)
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
