from __future__ import annotations

from pathlib import Path

from app import meta
from app.asr.backend import TranscriptFile
from app.asr.fake import FakeAsr
from app.pipeline.stages import assemble, transcribe
from app.pipeline.states import JobStage
from tests.fixtures.meetings import harness, write_chunks


class Services:
    def __init__(self, asr: FakeAsr) -> None:
        self.asr = asr


def prepared(tmp_path: Path):  # type: ignore[no-untyped-def]
    h = harness(tmp_path, asr__backend="fake", audio__vad="energy")
    meeting = h.meeting()
    write_chunks(meeting.path, seconds=150)
    transcribe.run(h.context(meeting, services=Services(FakeAsr())))
    return h, meeting


def test_assemble_writes_both_artifacts(tmp_path: Path) -> None:
    h, meeting = prepared(tmp_path)
    assemble.run(h.context(meeting, JobStage.ASSEMBLE))
    transcript_json, transcript_md = assemble.transcript_paths(meeting.path)
    assert transcript_json.exists() and transcript_md.exists()
    loaded = TranscriptFile.read(transcript_json)
    assert loaded.segments
    assert loaded.language == "he"
    assert [s.id for s in loaded.segments] == list(range(len(loaded.segments)))
    text = transcript_md.read_text(encoding="utf-8")
    assert text.startswith("**[00:00]") or text.startswith("# ")
    assert "ME:" in text and "THEM:" in text
    stored = h.dao.require_meeting(meeting.id)
    assert stored.duration_s and stored.duration_s > 100
    assert "echo_suppressed" in meta.read(meeting.path)


def test_fts_roundtrip(tmp_path: Path) -> None:
    h, meeting = prepared(tmp_path)
    assemble.run(h.context(meeting, JobStage.ASSEMBLE))
    hits = h.dao.search("הסטטוס")
    assert hits, "a Hebrew word from the transcript is searchable"
    assert hits[0].meeting_id == meeting.id
    assert hits[0].at_ms >= 0
    assert "הסטטוס" in hits[0].snippet
    assert h.dao.list_meetings(q="הסטטוס")[0].id == meeting.id


def test_assemble_idempotent(tmp_path: Path) -> None:
    h, meeting = prepared(tmp_path)
    ctx = h.context(meeting, JobStage.ASSEMBLE)
    assemble.run(ctx)
    transcript_json, transcript_md = assemble.transcript_paths(meeting.path)
    first_json = transcript_json.read_bytes()
    first_md = transcript_md.read_bytes()
    turns = len(h.dao.turns(meeting.id))

    assemble.run(ctx)  # current output: does nothing
    assert transcript_json.read_bytes() == first_json
    assert len(h.dao.turns(meeting.id)) == turns

    transcript_json.unlink()
    transcript_md.unlink()
    assemble.run(ctx)  # forced re-run: identical bytes, no duplicate rows
    assert transcript_json.read_bytes() == first_json
    assert transcript_md.read_bytes() == first_md
    assert len(h.dao.turns(meeting.id)) == turns
    assert len(h.dao.search("הסטטוס")) == len(
        [t for t in h.dao.turns(meeting.id) if "סטטוס" in t.text]
    )
