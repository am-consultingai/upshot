"""Playback is one stream: press play, hear the meeting."""

from __future__ import annotations

import io
import random
import wave
from pathlib import Path

import numpy as np
import pytest

from app.audio.mix import layout_for, read_range
from app.audio.writer import HEADER_BYTES
from tests.fixtures.api import build_harness


@pytest.fixture
def api(tmp_path: Path, app_home: Path):  # type: ignore[no-untyped-def]
    return build_harness(tmp_path)


def write_track(folder: Path, name: str, samples: np.ndarray, rate: int = 16000) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    with wave.open(str(folder / f"{name}.wav"), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(samples.astype("<i2").tobytes())


def tone(seconds: float, hz: int, amplitude: int, rate: int = 16000) -> np.ndarray:
    t = np.arange(int(rate * seconds)) / rate
    return (np.sin(2 * np.pi * hz * t) * amplitude).astype(np.int16)


def test_the_mix_is_both_sides_on_one_timeline(tmp_path: Path) -> None:
    audio = tmp_path / "audio"
    me = tone(2.0, 440, 6000)
    them = tone(2.0, 880, 5000)
    write_track(audio, "me", me)
    write_track(audio, "them", them)

    layout = layout_for(tmp_path)
    assert layout is not None
    assert layout.duration_s == pytest.approx(2.0)

    blob = read_range(layout, 0, layout.size - 1)
    with wave.open(io.BytesIO(blob)) as handle:  # a real decoder must accept it
        assert handle.getnchannels() == 1
        assert handle.getframerate() == 16000
        mixed = np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16)

    expected = np.clip(me.astype(np.int32) + them.astype(np.int32), -32768, 32767).astype(np.int16)
    assert np.array_equal(mixed, expected)


def test_every_range_matches_the_whole_file(tmp_path: Path) -> None:
    """Including odd byte offsets, which land mid-sample and must still be exact."""
    audio = tmp_path / "audio"
    write_track(audio, "me", tone(1.5, 440, 6000))
    write_track(audio, "them", tone(1.5, 300, 4000))
    layout = layout_for(tmp_path)
    assert layout is not None
    whole = read_range(layout, 0, layout.size - 1)

    random.seed(11)
    cases = [(0, 43), (43, 45), (HEADER_BYTES - 1, HEADER_BYTES + 1), (layout.size - 3, 10**9)]
    cases += [
        (a, min(layout.size - 1, a + random.randrange(1, 4000)))
        for a in (random.randrange(0, layout.size) for _ in range(120))
    ]
    for start, end in cases:
        got = read_range(layout, start, end)
        assert got == whole[start : min(end, layout.size - 1) + 1], (start, end)


def test_a_silent_track_leaves_the_other_untouched(tmp_path: Path) -> None:
    """The common case here: nobody else spoke, so the mix must be just your voice."""
    audio = tmp_path / "audio"
    me = tone(1.0, 440, 9000)
    write_track(audio, "me", me)
    write_track(audio, "them", np.zeros(16000, dtype=np.int16))
    layout = layout_for(tmp_path)
    assert layout is not None
    mixed = np.frombuffer(read_range(layout, HEADER_BYTES, layout.size - 1), dtype=np.int16)
    assert np.array_equal(mixed, me)


def test_the_shorter_track_is_padded_not_truncated(tmp_path: Path) -> None:
    audio = tmp_path / "audio"
    me = tone(2.0, 440, 6000)
    them = tone(0.5, 880, 6000)
    write_track(audio, "me", me)
    write_track(audio, "them", them)
    layout = layout_for(tmp_path)
    assert layout is not None
    assert layout.duration_s == pytest.approx(2.0), "the mix is as long as the longest track"
    mixed = np.frombuffer(read_range(layout, HEADER_BYTES, layout.size - 1), dtype=np.int16)
    assert np.array_equal(mixed[them.size :], me[them.size :])


def test_loud_overlap_clips_rather_than_wrapping(tmp_path: Path) -> None:
    """Summing int16 without care wraps to a loud crackle; it must saturate instead."""
    audio = tmp_path / "audio"
    loud = np.full(1600, 30000, dtype=np.int16)
    write_track(audio, "me", loud)
    write_track(audio, "them", loud)
    layout = layout_for(tmp_path)
    assert layout is not None
    mixed = np.frombuffer(read_range(layout, HEADER_BYTES, layout.size - 1), dtype=np.int16)
    assert mixed.min() >= 0 and mixed.max() == 32767


def test_player_gets_the_mix_by_default(api) -> None:  # type: ignore[no-untyped-def]
    meeting = api.services.meetings.create(source="manual")
    audio = meeting.path / "audio"
    write_track(audio, "me", tone(1.0, 440, 6000))
    write_track(audio, "them", tone(1.0, 880, 5000))
    client = api.client()

    default = client.get(f"/api/meetings/{meeting.id}/audio")
    assert default.status_code == 200
    explicit = client.get(f"/api/meetings/{meeting.id}/audio?track=mix")
    assert explicit.content == default.content

    single = client.get(f"/api/meetings/{meeting.id}/audio?track=me")
    assert single.status_code == 200
    assert single.content != default.content, "the mix must differ from one track"

    ranged = client.get(f"/api/meetings/{meeting.id}/audio", headers={"Range": "bytes=100-199"})
    assert ranged.status_code == 206
    assert ranged.content == default.content[100:200]


# ------------------------------------------------------- the echo, removed on read (D37)


def leaking(tmp_path: Path, gain: float, delay: int) -> tuple[np.ndarray, np.ndarray]:
    """A meeting recorded through a microphone bus that also carried the playback."""
    from app import meta
    from app.audio.echo import EchoModel, aligned

    rng = np.random.default_rng(4)
    them = (rng.normal(0, 0.15, 3 * 16000) * 32767).astype(np.int16)
    own = (rng.normal(0, 0.04, 3 * 16000) * 32767).astype(np.int16)
    me = np.clip(own + gain * aligned(them, delay, len(own)), -32768, 32767).astype(np.int16)
    write_track(tmp_path / "audio", "me", me)
    write_track(tmp_path / "audio", "them", them)
    meta.write(
        tmp_path,
        {
            "echo": EchoModel(
                gain=gain, delay=delay, correlation=0.97, reduction=0.93, rate=16000
            ).as_dict()
        },
    )
    return me, them


def test_the_mix_subtracts_the_echo_instead_of_playing_it_twice(tmp_path: Path) -> None:
    from app.audio.echo import aligned, cancel

    gain, delay = 1.715, 1683
    me, them = leaking(tmp_path, gain, delay)
    layout = layout_for(tmp_path)
    assert layout is not None and layout.echo is not None

    mixed = np.frombuffer(read_range(layout, HEADER_BYTES, layout.size - 1), dtype=np.int16)
    cleaned = cancel(me, aligned(them, delay, len(me)), gain)
    expected = np.clip(cleaned + them.astype(np.int32), -32768, 32767).astype(np.int16)
    assert np.array_equal(mixed, expected)

    doubled = np.clip(me.astype(np.int32) + them, -32768, 32767).astype(np.int16)
    energy = lambda x: float(np.square(x.astype(np.float64)).sum())  # noqa: E731
    assert energy(mixed) < 0.5 * energy(doubled), "the duplicated far side is gone"


def test_every_range_still_matches_the_whole_file_with_echo_removed(tmp_path: Path) -> None:
    """The reference is read at a shifted offset, including negative ones at the start."""
    leaking(tmp_path, 1.715, 1683)
    layout = layout_for(tmp_path)
    assert layout is not None
    whole = read_range(layout, 0, layout.size - 1)

    random.seed(23)
    cases = [(0, 43), (43, 45), (HEADER_BYTES, HEADER_BYTES + 1), (layout.size - 3, 10**9)]
    cases += [
        (a, min(layout.size - 1, a + random.randrange(1, 5000)))
        for a in (random.randrange(0, layout.size) for _ in range(120))
    ]
    for start, end in cases:
        assert read_range(layout, start, end) == whole[start : min(end, layout.size - 1) + 1], (
            start,
            end,
        )


def test_the_raw_track_is_never_touched(api) -> None:  # type: ignore[no-untyped-def]
    """``?track=me`` is for diagnosis: it must be exactly what the device produced."""
    meeting = api.services.meetings.create(source="manual")
    me, _them = leaking(meeting.path, 1.5, 800)
    client = api.client()

    raw = client.get(f"/api/meetings/{meeting.id}/audio?track=me")
    assert raw.status_code == 200
    assert np.array_equal(np.frombuffer(raw.content[HEADER_BYTES:], dtype=np.int16), me)
    assert client.get(f"/api/meetings/{meeting.id}/audio").content != raw.content


def test_a_meeting_with_no_echo_model_mixes_as_before(tmp_path: Path) -> None:
    audio = tmp_path / "audio"
    write_track(audio, "me", tone(1.0, 440, 6000))
    write_track(audio, "them", tone(1.0, 880, 5000))
    layout = layout_for(tmp_path)
    assert layout is not None and layout.echo is None
