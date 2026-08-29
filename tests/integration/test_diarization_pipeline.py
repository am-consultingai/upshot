"""Diarization through the real transcribe → assemble path."""

from __future__ import annotations

from pathlib import Path

from app import meta
from app.asr.fake import FakeAsr
from app.pipeline.stages import assemble, transcribe
from app.pipeline.states import JobStage
from tests.fixtures.meetings import harness, write_chunks


class Services:
    def __init__(self, asr: FakeAsr | None = None) -> None:
        self.asr = asr or FakeAsr()


def prepared(tmp_path: Path, **overrides: object):  # type: ignore[no-untyped-def]
    defaults: dict[str, object] = {
        "asr__backend": "fake",
        "audio__vad": "energy",
        "llm__provider": "fake",
    }
    defaults.update(overrides)
    h = harness(tmp_path, **defaults)  # type: ignore[arg-type]
    meeting = h.meeting()
    write_chunks(meeting.path, seconds=150)
    return h, meeting


def test_off_by_default_leaves_them_alone(tmp_path: Path) -> None:
    h, meeting = prepared(tmp_path)
    ctx = h.context(meeting, services=Services())
    transcribe.run(ctx)
    segments, _ = transcribe.load_segments(meeting.path)
    speakers = {s.speaker for s in segments}
    assert speakers == {"ME", "THEM"}
    assert "diarization" not in ctx.metrics
    assert "diarization" not in meta.read(meeting.path)


def test_diarization_splits_the_them_track(tmp_path: Path) -> None:
    h, meeting = prepared(tmp_path, asr__diarization="fake", asr__diarization_fake_speakers=3)
    ctx = h.context(meeting, services=Services())
    transcribe.run(ctx)

    segments, _ = transcribe.load_segments(meeting.path)
    them = {s.speaker for s in segments if s.track == "them"}
    mine = {s.speaker for s in segments if s.track == "me"}
    assert them <= {"THEM_1", "THEM_2", "THEM_3"}
    assert len(them) > 1, "the loopback track was actually split"
    assert mine == {"ME"}, "the microphone track is a hardware fact and is never relabelled"

    assert ctx.metrics["diarization"]["backend"] == "fake"
    assert ctx.metrics["diarization"]["speakers"] == 3
    assert meta.read(meeting.path)["diarization"]["speakers"] == 3


def test_diarized_speakers_reach_the_transcript(tmp_path: Path) -> None:
    h, meeting = prepared(tmp_path, asr__diarization="fake")
    services = Services()
    transcribe.run(h.context(meeting, services=services))
    assemble.run(h.context(meeting, JobStage.ASSEMBLE, services=services))
    text = (meeting.path / "transcript.md").read_text(encoding="utf-8")
    assert "THEM_1:" in text
    assert "THEM_2:" in text
    assert "ME:" in text
    assert "**[" in text


def test_coalescing_does_not_merge_two_speakers(tmp_path: Path) -> None:
    """Turns from different people must not be glued together by the 2 s rule."""
    from app.asr.backend import Segment
    from app.asr.diarize import SpeakerTurn, assign_speakers
    from app.pipeline.stages.assemble import coalesce

    segments = [
        Segment(0, "them", "THEM", 0.0, 3.0, "first speaker"),
        Segment(1, "them", "THEM", 3.5, 6.0, "second speaker"),
        Segment(2, "them", "THEM", 6.2, 8.0, "second speaker again"),
    ]
    turns = [SpeakerTurn(0.0, 3.2, 0), SpeakerTurn(3.2, 9.0, 1)]
    labelled = assign_speakers(segments, turns)
    coalesced = coalesce(labelled)
    assert [turn.speaker for turn in coalesced] == ["THEM_1", "THEM_2"]
    assert coalesced[1].text == "second speaker second speaker again"

    # without diarization the same three segments would have collapsed into one turn
    assert len(coalesce(segments)) == 1


def test_echo_suppression_still_works_with_labels(tmp_path: Path) -> None:
    """Echo suppression keys on the track, so speaker labels do not disturb it."""
    from app.asr.backend import Segment, sort_segments
    from app.asr.diarize import SpeakerTurn, assign_speakers
    from app.pipeline.stages.assemble import suppress_echo

    sentence = "אני חושב שנצטרך לדחות את הרילי‏ס"
    segments = sort_segments(
        [
            Segment(0, "me", "ME", 10.0, 13.0, sentence),
            Segment(1, "them", "THEM", 10.2, 13.2, sentence),
            Segment(2, "them", "THEM", 20.0, 22.0, "בסדר גמור"),
        ]
    )
    labelled = assign_speakers(segments, [SpeakerTurn(0.0, 30.0, 0)])
    kept, dropped = suppress_echo(labelled)
    assert dropped == 1
    assert [s.speaker for s in kept] == ["ME", "THEM_1"]


def test_diarization_is_idempotent(tmp_path: Path) -> None:
    h, meeting = prepared(tmp_path, asr__diarization="fake")
    ctx = h.context(meeting, services=Services())
    transcribe.run(ctx)
    first = transcribe.segments_path(meeting.path).read_bytes()
    transcribe.run(h.context(meeting, services=Services()))
    assert transcribe.segments_path(meeting.path).read_bytes() == first
