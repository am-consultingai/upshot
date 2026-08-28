"""Phase 3 exit criteria: a 60-minute synthetic soak, byte-exact."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.audio.fake import SyntheticCapture
from app.audio.recorder import Recorder
from app.audio.writer import read_manifest
from app.clock import FakeClock
from app.config import default_config

MINUTES = 60
RATE = 16000


@pytest.mark.slow
def test_sixty_minute_synthetic_soak(tmp_path: Path) -> None:
    folder = tmp_path / "meeting"
    config = default_config(audio__capture="synthetic")

    def factory(track: str) -> SyntheticCapture:
        pattern = "tone" if track == "them" else "silence"
        return SyntheticCapture(track, pattern, queue_seconds=10.0, block_frames=48000)

    recorder = Recorder(config, factory, clock=FakeClock())  # type: ignore[arg-type]
    recorder.start(folder, "soak")
    seconds = MINUTES * 60
    for _ in range(seconds):  # one block == 1 s of device audio; no real time is spent
        recorder.pump_once(0.0)
    result = recorder.stop()

    expected_samples = seconds * RATE
    for track in ("me", "them"):
        assert abs(recorder_total(result, track) - expected_samples) <= 16, track
    records, torn = read_manifest(folder)
    assert torn == 0
    for track in ("me", "them"):
        durations = sum(r.dur_ms for r in records if r.track == track)
        assert abs(durations - seconds * 1000) <= 50, (track, durations)
        assert all(r.dur_ms <= 70_000 for r in records if r.track == track)
    assert result.dropped == {"me": 0, "them": 0}
    assert result.xruns == {"me": 0, "them": 0}


def recorder_total(result: object, track: str) -> int:
    duration_ms = result.duration_ms[track]
    return round(duration_ms * RATE / 1000)
