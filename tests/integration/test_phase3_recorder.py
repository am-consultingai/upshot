from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from app.audio.fake import SyntheticCapture
from app.audio.recorder import Recorder
from app.audio.writer import read_manifest
from app.clock import FakeClock
from app.config import default_config


def make_recorder(tmp_path: Path, pattern: str = "tone", **overrides: object) -> Recorder:
    config = default_config(audio__capture="synthetic", **overrides)  # type: ignore[arg-type]
    captures: dict[str, SyntheticCapture] = {}

    def factory(track: str) -> SyntheticCapture:
        capture = captures[track] = SyntheticCapture(track, pattern, queue_seconds=10.0)
        return capture

    recorder = Recorder(config, factory, clock=FakeClock())  # type: ignore[arg-type]
    recorder.captures = captures  # type: ignore[attr-defined]
    return recorder


def pump_seconds(recorder: Recorder, seconds: float) -> None:
    """Drive the recorder for a simulated number of seconds of device audio."""
    for _ in range(int(seconds * 10)):  # blocks are 0.1 s
        recorder.pump_once(0.0)


def test_preroll_is_not_on_disk_until_commit(tmp_path: Path) -> None:
    folder = tmp_path / "meeting"
    recorder = make_recorder(tmp_path)
    recorder.arm()
    pump_seconds(recorder, 20)
    assert not folder.exists()
    assert len(recorder.runtime["me"].ring) > 0
    recorder.discard()
    assert not folder.exists()


def test_commit_flushes_preroll_as_chunk_one(tmp_path: Path) -> None:
    folder = tmp_path / "meeting"
    recorder = make_recorder(tmp_path)
    recorder.arm()
    pump_seconds(recorder, 18)
    recorder.commit(folder, "m1")
    result = recorder.stop()
    records, torn = read_manifest(folder)
    assert torn == 0
    first = next(r for r in records if r.track == "me" and r.seq == 1)
    assert first.t0_ms == 0
    assert 17_000 <= first.dur_ms <= 19_000, first.dur_ms
    assert result.folder == folder
    with wave.open(str(folder / "audio" / "me.wav"), "rb") as handle:
        # One file per track: the pre-roll is the *start* of it, not a file of its own.
        assert first.offset == 0, "the pre-roll must open the recording"
        assert handle.getnframes() == sum(r.samples for r in records if r.track == "me")


def test_device_change_recovery(tmp_path: Path) -> None:
    """A yanked headset costs a 300 ms hole, never the meeting (§4.5)."""
    folder = tmp_path / "meeting"
    recorder = make_recorder(tmp_path)
    recorder.arm()
    recorder.commit(folder, "m1")
    recorder.runtime["them"].capture.glitch_after(30)  # type: ignore[attr-defined]
    pump_seconds(recorder, 12)
    assert recorder.runtime["them"].reopened == 1
    assert recorder.runtime["them"].gap_ms > 0
    result = recorder.stop()
    assert result.gaps_ms["them"] > 0
    records, _ = read_manifest(folder)
    them = [r for r in records if r.track == "them"]
    assert them, "recording continued through the device change"
    assert any(r.gap_ms > 0 for r in them)
    assert recorder.committed is False


def test_pause_stops_writing_to_disk(tmp_path: Path) -> None:
    folder = tmp_path / "meeting"
    recorder = make_recorder(tmp_path)
    recorder.start(folder, "m1")
    pump_seconds(recorder, 5)
    recorder.pause()
    assert recorder.is_active() is False
    pump_seconds(recorder, 5)
    recorder.resume()
    pump_seconds(recorder, 5)
    result = recorder.stop()
    assert 9_000 <= result.duration_ms["me"] <= 11_500, result.duration_ms


def test_levels_reported_per_track(tmp_path: Path) -> None:
    recorder = make_recorder(tmp_path, pattern="tone")
    recorder.arm()
    pump_seconds(recorder, 1)
    levels = recorder.levels()
    assert set(levels) == {"me", "them"}
    assert all(value > 0.1 for value in levels.values())


def test_silence_pattern_is_quiet(tmp_path: Path) -> None:
    recorder = make_recorder(tmp_path, pattern="silence")
    recorder.arm()
    pump_seconds(recorder, 1)
    assert all(value < 0.001 for value in recorder.levels().values())
    ring = recorder.runtime["me"].ring.peek()
    assert np.max(np.abs(ring)) == 0


def test_a_committed_recorder_refuses_to_be_discarded(tmp_path: Path) -> None:
    """Discarding is for a wake that came to nothing. Once a meeting is being written,
    closing the streams would end it silently, halfway through."""
    folder = tmp_path / "meeting"
    recorder = make_recorder(tmp_path)
    recorder.start(folder, "m1")
    pump_seconds(recorder, 5)

    recorder.discard()

    assert recorder.committed, "still recording"
    assert recorder.runtime, "the streams are still open"
    pump_seconds(recorder, 5)
    result = recorder.stop()
    assert result.total_duration_ms > 9000, "all ten seconds were written"
