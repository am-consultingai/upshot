from __future__ import annotations

from pathlib import Path

import pytest

from app import meta
from app.asr.fake import FakeAsr
from app.llm.client import FakeLlm
from app.llm.schema import validate
from app.pipeline.stages import assemble, summarize, transcribe
from app.pipeline.states import JobStage, MeetingState
from tests.fixtures.meetings import harness, write_chunks


class Services:
    def __init__(self, asr: FakeAsr | None = None, llm: FakeLlm | None = None) -> None:
        self.asr = asr or FakeAsr()
        self.llm = llm or FakeLlm()


def prepared(tmp_path: Path, *, seconds: float = 200.0, duration_s: int | None = None, **cfg):  # type: ignore[no-untyped-def]
    h = harness(tmp_path, asr__backend="fake", llm__provider="fake", audio__vad="energy", **cfg)
    meeting = h.meeting()
    write_chunks(meeting.path, seconds=seconds)
    services = Services()
    transcribe.run(h.context(meeting, services=services))
    assemble.run(h.context(meeting, JobStage.ASSEMBLE, services=services))
    if duration_s is not None:
        h.dao.update_meeting(meeting.id, duration_s=duration_s)
    return h, h.dao.require_meeting(meeting.id)


def test_summarize_writes_schema_valid_notes(tmp_path: Path) -> None:
    h, meeting = prepared(tmp_path)
    llm = FakeLlm()
    summarize.run(h.context(meeting, JobStage.SUMMARIZE, services=Services(llm=llm)))
    notes = summarize.load_notes(meeting.path)
    validate(notes)
    assert notes["title"]
    assert len(llm.calls) >= 2, "at least one map call and the reduce call"
    payload = meta.read(meeting.path)
    assert payload["prompt_versions"] == {"system": "1", "map": "1", "reduce": "1"}
    assert payload["summary_language"] == "en"
    stored = h.dao.require_meeting(meeting.id)
    assert stored.summary_language == "en"
    assert stored.title == notes["title"] and stored.title_source == "llm"


def test_map_reduce_merges(tmp_path: Path) -> None:
    h, meeting = prepared(tmp_path, seconds=400)
    llm = FakeLlm()
    ctx = h.context(meeting, JobStage.SUMMARIZE, services=Services(llm=llm))
    h.config.set("llm.window_tokens", 60)
    h.config.set("llm.window_overlap_tokens", 10)
    summarize.run(ctx)
    map_calls = [call for call in llm.calls if "title" not in call["schema"]["properties"]]
    reduce_calls = [call for call in llm.calls if "title" in call["schema"]["properties"]]
    assert len(map_calls) > 1, "a long transcript is windowed"
    assert len(reduce_calls) == 1
    reduce_input = reduce_calls[0]["user"]
    assert '"windows"' in reduce_input
    notes = summarize.load_notes(meeting.path)
    assert len(notes["decisions"]) >= 1
    assert ctx.metrics["windows"] == len(map_calls)


def test_sanity_gate_flags_review(tmp_path: Path) -> None:
    h, meeting = prepared(tmp_path, duration_s=40 * 60)
    llm = FakeLlm(action_items=0)
    ctx = h.context(meeting, JobStage.SUMMARIZE, services=Services(llm=llm))
    summarize.run(ctx)
    assert summarize.notes_path(meeting.path).exists(), "artifacts are still written"
    assert h.dao.require_meeting(meeting.id).state == MeetingState.NEEDS_REVIEW
    reasons = meta.review_reasons(meeting.path)
    assert any("no action items" in reason for reason in reasons)


def test_sanity_gate_unknown_owner(tmp_path: Path) -> None:
    h, meeting = prepared(tmp_path, duration_s=30 * 60)
    ctx = h.context(meeting, JobStage.SUMMARIZE, services=Services(llm=FakeLlm(unknown_owner=True)))
    summarize.run(ctx)
    assert any("not a known participant" in r for r in meta.review_reasons(meeting.path))


def test_healthy_summary_does_not_flag_review(tmp_path: Path) -> None:
    h, meeting = prepared(tmp_path, duration_s=30 * 60)
    summarize.run(h.context(meeting, JobStage.SUMMARIZE, services=Services()))
    assert meta.review_reasons(meeting.path) == []
    assert h.dao.require_meeting(meeting.id).state != MeetingState.NEEDS_REVIEW


def test_summary_language_resolution(tmp_path: Path) -> None:
    for configured, expected in (("en", "en"), ("he", "he"), ("auto", "he")):
        h, meeting = prepared(tmp_path / configured, summary__language=configured)
        ctx = h.context(meeting, JobStage.SUMMARIZE, services=Services())
        assert summarize.resolve_summary_language(ctx) == expected
        summarize.run(ctx)
        assert h.dao.require_meeting(meeting.id).summary_language == expected


def test_output_language_reaches_the_request(tmp_path: Path) -> None:
    h, meeting = prepared(tmp_path, summary__language="he")
    llm = FakeLlm()
    summarize.run(h.context(meeting, JobStage.SUMMARIZE, services=Services(llm=llm)))
    for call in llm.calls:
        system_text = "\n".join(block["text"] for block in call["system"])
        assert "Hebrew (he)" in system_text


def test_summarize_is_idempotent(tmp_path: Path) -> None:
    h, meeting = prepared(tmp_path)
    llm = FakeLlm()
    services = Services(llm=llm)
    summarize.run(h.context(meeting, JobStage.SUMMARIZE, services=services))
    calls = len(llm.calls)
    first = summarize.notes_path(meeting.path).read_bytes()
    summarize.run(h.context(meeting, JobStage.SUMMARIZE, services=services))
    assert len(llm.calls) == calls
    assert summarize.notes_path(meeting.path).read_bytes() == first


def test_missing_transcript_fails_loudly(tmp_path: Path) -> None:
    h = harness(tmp_path, llm__provider="fake")
    meeting = h.meeting()
    meeting.path.mkdir(parents=True, exist_ok=True)
    with pytest.raises(FileNotFoundError):
        summarize.run(h.context(meeting, JobStage.SUMMARIZE, services=Services()))
