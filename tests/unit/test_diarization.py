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
    label_track,
    make_diarizer,
    merge_minor_speakers,
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
    assert default_config().get("asr.diarization") == "onnx", "always on (D85)"
    assert make_diarizer(default_config(asr__diarization="off")) is None
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


def test_without_its_models_diarization_is_skipped_not_failed(app_home) -> None:  # type: ignore[no-untyped-def]
    assert make_diarizer(default_config()) is None


def test_a_saved_off_is_the_old_default_and_is_forgotten(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import json

    from app.config import Config

    saved = tmp_path / "app_config.json"
    saved.write_text(json.dumps({"setup": {"done": True}, "asr": {"diarization": "off"}}))
    assert Config.load(file=saved, environ={}).get("asr.diarization") == "onnx"


# ------------------------------------------------------------------ the microphone (D85)


def test_a_voice_that_barely_spoke_is_folded_into_the_main_one() -> None:
    turns = [SpeakerTurn(0.0, 300.0, 4), SpeakerTurn(300.0, 305.0, 2), SpeakerTurn(310.0, 600.0, 4)]
    assert speaker_count(merge_minor_speakers(turns)) == 1


def test_two_people_in_the_room_stay_two() -> None:
    turns = [SpeakerTurn(0.0, 120.0, 0), SpeakerTurn(120.0, 200.0, 1), SpeakerTurn(200.0, 300.0, 0)]
    merged = merge_minor_speakers(turns)
    assert speaker_count(merged) == 2
    assert [turn.speaker for turn in merged] == [0, 1, 0]


def test_a_short_meeting_keeps_its_main_voice() -> None:
    """Every voice under the floor: the one that spoke most is still there."""
    turns = [SpeakerTurn(0.0, 8.0, 1), SpeakerTurn(8.0, 10.0, 0)]
    assert merge_minor_speakers(turns) == [SpeakerTurn(0.0, 8.0, 0), SpeakerTurn(8.0, 10.0, 0)]


def test_one_voice_keeps_the_track_label() -> None:
    segments = [seg(0, "me", 0.0, 2.0), seg(1, "them", 1.0, 3.0)]
    one = [SpeakerTurn(0.0, 3.0, 0)]
    assert [s.speaker for s in label_track(segments, one, track="me", base="ME")] == ["ME", "THEM"]
    assert [s.speaker for s in label_track(segments, one, track="them", base="THEM")] == [
        "ME",
        "THEM",
    ]


def test_several_voices_on_the_microphone_are_numbered() -> None:
    segments = [seg(0, "me", 0.0, 2.0), seg(1, "me", 2.0, 4.0), seg(2, "them", 0.0, 4.0)]
    two = [SpeakerTurn(0.0, 2.0, 0), SpeakerTurn(2.0, 4.0, 1)]
    labelled = label_track(segments, two, track="me", base="ME")
    assert [s.speaker for s in labelled] == ["ME_1", "ME_2", "THEM"]


def test_int16_samples_reach_the_onnx_models_scaled_to_one() -> None:
    """The transcribe stage and the file engine pass ``read_wav``'s int16 samples. sherpa-onnx
    wants -1..1; unscaled, every voice embedded alike and a two-voice dialogue came out as one."""
    import numpy as np

    from app.asr.diarize import OnnxDiarizer

    seen: list[np.ndarray] = []

    class Result:
        def sort_by_start_time(self) -> list[object]:
            return []

    class Pipeline:
        sample_rate = 16000

        def process(self, audio: np.ndarray) -> Result:
            seen.append(audio)
            return Result()

    diarizer = OnnxDiarizer("seg.onnx", "emb.onnx")
    diarizer._pipeline = Pipeline()
    diarizer.diarize(np.array([32767, -32768, 16384], dtype=np.int16), 16000)
    diarizer.diarize(np.array([0.5, -0.25], dtype=np.float32), 16000)
    assert seen[0].dtype == np.float32 and np.abs(seen[0]).max() <= 1.0
    assert seen[0][2] == 0.5
    assert seen[1].tolist() == [0.5, -0.25], "float input is already scaled"


# ------------------------------------------------------------------ over-clustering (z8tj1hfdwp)


def unit(*values: float) -> np.ndarray:
    vector = np.array(values, dtype=np.float32)
    return vector / np.linalg.norm(vector)


def near(base: np.ndarray, seed: int, spread: float = 0.4) -> np.ndarray:
    """A centroid close to ``base``: the same voice, clustered apart (cosine ~0.9)."""
    sigma = spread / np.sqrt(base.size)
    noise = np.random.default_rng(seed).normal(0, sigma, base.shape).astype(np.float32)
    return (base + noise) / np.linalg.norm(base + noise)


def test_a_one_to_one_call_split_into_sixteen_clusters_is_one_voice() -> None:
    """The far side of the 2026-10-08 meeting: one person, sixteen clusters whose centroids
    agree (cosine 0.8-0.98), the largest 144 s. They are one voice."""
    from app.asr.diarize import merge_similar_speakers

    voice = unit(*np.random.default_rng(0).normal(0, 1, 192))
    talk = [144, 101, 75, 69, 63, 28, 11, 9, 8, 8, 3, 2, 1, 1, 1, 0.4]
    turns, centroids, clock = [], {}, 0.0
    for speaker, seconds in enumerate(talk):
        turns.append(SpeakerTurn(clock, clock + seconds, speaker))
        centroids[speaker] = near(voice, speaker)
        clock += seconds + 1.0
    assert min(float(centroids[0] @ c) for c in centroids.values()) > 0.7
    assert speaker_count(merge_similar_speakers(turns, centroids)) == 1


def test_two_people_split_into_several_clusters_each_stay_two() -> None:
    """The merge must not buy one voice per track by merging everyone: two people's
    centroids are far apart (cosine ~0.1 with the 3D-Speaker embedding)."""
    from app.asr.diarize import merge_similar_speakers

    rng = np.random.default_rng(1)
    first, second = unit(*rng.normal(0, 1, 192)), unit(*rng.normal(0, 1, 192))
    assert abs(float(first @ second)) < 0.3
    turns, centroids = [], {}
    for speaker in range(6):
        voice = first if speaker % 2 == 0 else second
        turns.append(SpeakerTurn(speaker * 30.0, speaker * 30.0 + 25.0, speaker))
        centroids[speaker] = near(voice, 10 + speaker)
    merged = merge_similar_speakers(turns, centroids)
    assert speaker_count(merged) == 2
    assert [turn.speaker for turn in merged] == [0, 1, 0, 1, 0, 1]


def test_the_merge_bound_is_a_cosine_similarity() -> None:
    from app.asr.diarize import merge_similar_speakers

    turns = [SpeakerTurn(0.0, 10.0, 0), SpeakerTurn(10.0, 20.0, 1)]
    centroids = {0: unit(1.0, 0.0), 1: unit(0.6, 0.8)}  # cosine 0.6
    assert speaker_count(merge_similar_speakers(turns, centroids, bound=0.6)) == 1
    assert speaker_count(merge_similar_speakers(turns, centroids, bound=0.61)) == 2


def test_a_voice_without_a_centroid_is_left_alone() -> None:
    from app.asr.diarize import merge_similar_speakers

    turns = [SpeakerTurn(0.0, 10.0, 0), SpeakerTurn(10.0, 20.0, 1)]
    assert speaker_count(merge_similar_speakers(turns, {0: unit(1.0, 0.0)})) == 2


def test_centroids_weigh_the_long_turns_and_embed_a_short_only_voice_whole() -> None:
    """Embeddings of sub-second turns are noise ("okay", "right"): a voice's centroid comes
    from its turns of a second or more, weighted by length. A voice made only of short
    turns is embedded as one concatenation."""
    from app.asr.diarize import speaker_centroids

    rate = 10
    samples = np.zeros(rate * 40, dtype=np.float32)
    samples[0:100] = 1.0  # speaker 0, 10 s, "points" along x
    samples[100:120] = 2.0  # speaker 0, 2 s, "points" along y
    samples[200:205] = 3.0  # speaker 1, two half-second turns
    samples[300:305] = 3.0
    turns = [
        SpeakerTurn(0.0, 10.0, 0),
        SpeakerTurn(10.0, 12.0, 0),
        SpeakerTurn(12.0, 12.5, 0),
        SpeakerTurn(20.0, 20.5, 1),
        SpeakerTurn(30.0, 30.5, 1),
    ]
    calls: list[int] = []

    def embed(audio: np.ndarray) -> np.ndarray:
        calls.append(len(audio))
        value = float(audio[0]) if len(audio) else 0.0
        return {1.0: unit(1.0, 0.0), 2.0: unit(0.0, 1.0)}.get(value, unit(1.0, 1.0))

    centroids = speaker_centroids(turns, samples, rate, embed)
    assert calls == [100, 20, 10], "two long turns of speaker 0, then speaker 1 as one piece"
    assert centroids[0] == pytest.approx(unit(10.0, 2.0), abs=1e-6)
    assert centroids[1] == pytest.approx(unit(1.0, 1.0), abs=1e-6)


def test_the_far_side_folds_its_crumbs_but_keeps_a_brief_real_voice() -> None:
    """After the merge, a far-side voice under 2 % of the talk is a fragment (a laugh, a
    clipped "yes"); the 4-speaker reference's quietest speaker is 19 % and stays."""
    from app.asr.diarize import diarize_track

    class Fixed:
        name = "fixed"

        def __init__(self, turns: list[SpeakerTurn]) -> None:
            self.turns = turns

        def diarize(self, samples: np.ndarray, rate: int) -> list[SpeakerTurn]:
            return self.turns

        def unload(self) -> None:
            return None

    audio = np.zeros(16000, dtype=np.float32)
    crumbs = Fixed([SpeakerTurn(0.0, 500.0, 0), SpeakerTurn(500.0, 503.0, 1)])
    _, found = diarize_track(crumbs, audio, 16000, [], track="them", base="THEM",
                             config=default_config())  # fmt: skip
    assert found is not None and found["speakers"] == 1
    brief = Fixed([SpeakerTurn(0.0, 11.0, 0), SpeakerTurn(11.0, 19.0, 1),
                   SpeakerTurn(19.0, 25.0, 2), SpeakerTurn(25.0, 31.0, 3)])  # fmt: skip
    _, found = diarize_track(brief, audio, 16000, [], track="them", base="THEM",
                             config=default_config())  # fmt: skip
    assert found is not None and found["speakers"] == 4


def test_the_embedding_is_the_multilingual_one_under_its_own_file_name(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The English VoxCeleb CAM++ could not tell the two people of a Hebrew call apart
    (centroids 0.92 alike); 3D-Speaker's zh-en CAM++ puts them at 0.16. The file is named
    for the model, so an install holding the old ``embedding.onnx`` fetches the new one."""
    from app.asr.models import EMBEDDING_URL, resolve_diarization

    assert "3dspeaker" in EMBEDDING_URL and "voxceleb" not in EMBEDDING_URL
    config = default_config(asr__diarization_dir=str(tmp_path))
    (tmp_path / "segmentation.onnx").write_bytes(b"x")
    (tmp_path / "embedding.onnx").write_bytes(b"old")
    assert not resolve_diarization(config).present


def test_fetching_the_new_embedding_removes_the_retired_one(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """An upgrade downloads the 3D-Speaker embedding and deletes the VoxCeleb one it replaces."""
    import io
    import urllib.request

    from app.asr import model_manager
    from app.asr.models import EMBEDDING_FILE, download_diarization

    fetched: list[str] = []

    def urlopen(url: str, **_: object) -> io.BytesIO:
        fetched.append(url)
        return io.BytesIO(b"onnx")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(model_manager, "tls_context", lambda: None)
    (tmp_path / "segmentation.onnx").write_bytes(b"x")
    (tmp_path / "embedding.onnx").write_bytes(b"old")
    models = download_diarization(default_config(asr__diarization_dir=str(tmp_path)))
    assert models.present and models.embedding.name == EMBEDDING_FILE
    assert len(fetched) == 1 and "3dspeaker" in fetched[0], "segmentation was already there"
    assert not (tmp_path / "embedding.onnx").exists()
