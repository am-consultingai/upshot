from __future__ import annotations

import numpy as np
import pytest

from app.asr.backend import Segment
from app.asr.diarize import (
    FakeDiarizer,
    SpeakerTurn,
    assign_speakers,
    concat_track,
    label_for,
    make_diarizer,
    normalize_turns,
    speaker_count,
)
from app.config import default_config


def seg(seg_id: int, track: str, start: float, end: float, text: str = "x") -> Segment:
    return Segment(
        id=seg_id,
        track=track,
        speaker="ME" if track == "me" else "THEM",
        start=start,
        end=end,
        text=text,
    )


# ------------------------------------------------------------------ normalization


def test_normalize_relabels_by_first_appearance() -> None:
    """The clusterer's ids are arbitrary and sparse: speaker_0 and speaker_3 for two people."""
    turns = [SpeakerTurn(9.0, 11.0, 3), SpeakerTurn(1.0, 3.0, 0), SpeakerTurn(12.0, 14.0, 3)]
    normalized = normalize_turns(turns)
    assert [t.speaker for t in normalized] == [0, 1, 1]
    assert [t.start for t in normalized] == [1.0, 9.0, 12.0]
    assert speaker_count(normalized) == 2


def test_normalize_is_idempotent() -> None:
    turns = [SpeakerTurn(0, 1, 5), SpeakerTurn(1, 2, 2)]
    once = normalize_turns(turns)
    assert normalize_turns(once) == once


def test_labels_are_one_based() -> None:
    assert label_for(0) == "THEM_1"
    assert label_for(3) == "THEM_4"
    assert label_for(0, base="SPK") == "SPK_1"


# ------------------------------------------------------------------ assignment


def test_assign_by_majority_overlap() -> None:
    segments = [seg(0, "them", 0.0, 4.0), seg(1, "them", 5.0, 9.0)]
    turns = [SpeakerTurn(0.0, 4.5, 0), SpeakerTurn(4.5, 10.0, 1)]
    labelled = assign_speakers(segments, turns)
    assert [s.speaker for s in labelled] == ["THEM_1", "THEM_2"]


def test_assign_picks_the_dominant_speaker_not_the_first() -> None:
    """A segment that clips the end of one turn belongs to whoever spoke most of it."""
    segments = [seg(0, "them", 3.5, 8.0)]
    turns = [SpeakerTurn(0.0, 4.0, 0), SpeakerTurn(4.0, 9.0, 1)]
    assert assign_speakers(segments, turns)[0].speaker == "THEM_2"


def test_assign_leaves_the_me_track_alone() -> None:
    """Two-track capture already settles ME; a diarizer must not touch it."""
    segments = [seg(0, "me", 0.0, 4.0), seg(1, "them", 0.0, 4.0)]
    labelled = assign_speakers(segments, [SpeakerTurn(0.0, 4.0, 0)])
    assert labelled[0].speaker == "ME" and labelled[0].track == "me"
    assert labelled[1].speaker == "THEM_1"


def test_assign_keeps_them_when_nothing_overlaps() -> None:
    """An unlabelled turn beats a wrong one — the track already proves it is not ME."""
    segments = [seg(0, "them", 20.0, 24.0)]
    assert assign_speakers(segments, [SpeakerTurn(0.0, 4.0, 0)])[0].speaker == "THEM"


def test_assign_with_no_turns_is_a_no_op() -> None:
    segments = [seg(0, "them", 0.0, 4.0)]
    assert assign_speakers(segments, []) == segments


def test_assign_ties_break_deterministically() -> None:
    segments = [seg(0, "them", 0.0, 4.0)]
    turns = [SpeakerTurn(0.0, 2.0, 1), SpeakerTurn(2.0, 4.0, 0)]
    once = assign_speakers(segments, turns)[0].speaker
    twice = assign_speakers(segments, list(reversed(turns)))[0].speaker
    assert once == twice


def test_assign_preserves_everything_else() -> None:
    original = seg(7, "them", 1.0, 3.0, "שלום")
    labelled = assign_speakers([original], [SpeakerTurn(1.0, 3.0, 0)])[0]
    assert (labelled.id, labelled.start, labelled.end, labelled.text) == (
        original.id,
        original.start,
        original.end,
        original.text,
    )


# ------------------------------------------------------------------ track assembly


def test_concat_track_places_chunks_on_the_timeline() -> None:
    rate = 16000
    a = np.full(rate, 1000, dtype=np.int16)
    b = np.full(rate, 2000, dtype=np.int16)
    track = concat_track([(0.0, a), (2.0, b)], rate)  # a one-second gap between them
    assert len(track) == 3 * rate
    assert track[0] == pytest.approx(1000 / 32768, rel=1e-3)
    assert track[rate + 100] == 0.0, "the gap is silence, not a shortened timeline"
    assert track[2 * rate] == pytest.approx(2000 / 32768, rel=1e-3)
    assert track.dtype == np.float32


def test_concat_track_empty() -> None:
    assert len(concat_track([], 16000)) == 0


# ------------------------------------------------------------------ backends


def test_fake_diarizer_is_deterministic() -> None:
    samples = np.zeros(16000 * 20, dtype=np.int16)
    first = FakeDiarizer().diarize(samples, 16000)
    second = FakeDiarizer().diarize(samples, 16000)
    assert first == second
    assert speaker_count(first) == 2
    assert first[0].start == 0.0
    assert first[-1].end == pytest.approx(20.0)
    for turn in first:
        assert turn.end > turn.start


def test_fake_diarizer_speaker_count_is_configurable() -> None:
    samples = np.zeros(16000 * 30, dtype=np.int16)
    assert speaker_count(FakeDiarizer(speakers=3).diarize(samples, 16000)) == 3


def test_diarizer_is_selected_by_config() -> None:
    assert make_diarizer(default_config()) is None, "off by default"
    fake = make_diarizer(default_config(asr__diarization="fake"))
    assert fake is not None and fake.name == "fake"
    from app.config import Config

    with pytest.raises(ValueError):
        make_diarizer(Config({"asr": {"diarization": "nope"}}))


def test_onnx_backend_reports_missing_models(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from app.asr.diarize import OnnxDiarizer

    backend = OnnxDiarizer(str(tmp_path / "missing.onnx"), str(tmp_path / "gone.onnx"))
    pytest.importorskip("sherpa_onnx")
    with pytest.raises(RuntimeError, match="missing or unreadable"):
        backend.load()
