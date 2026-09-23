"""Phase 4 — the spike. These run only on a Windows host with real audio hardware.

They are collected everywhere so the suite reports them as skipped rather than missing;
`docs/windows-run.md` carries the command that closes them.
"""

from __future__ import annotations

import os
import threading
import time
import wave
from pathlib import Path

import numpy as np
import pytest

from app.audio.analysis import cross_correlation
from app.audio.devices import (
    NoDeviceError,
    default_capture,
    default_render,
    list_devices,
    loopback_for,
    play_wav,
)
from app.audio.factory import make_capture
from app.audio.recorder import Recorder
from app.audio.wasapi import WasapiCapture
from app.audio.writer import read_manifest
from app.clock import SystemClock
from app.config import default_config

pytestmark = pytest.mark.audio_hw

RATE = 16000


@pytest.fixture(autouse=True)
def _requires_audio() -> None:
    try:
        list_devices()
    except Exception as exc:
        pytest.skip(f"no WASAPI audio here: {exc}")


@pytest.fixture
def source_wav(tmp_path: Path) -> Path:
    """A 20 s T1 speech fixture, or a swept tone where SAPI is unavailable."""
    from tests.fixtures import speech

    target = tmp_path / "source.wav"
    if speech.available():
        return speech.synth("This is Upshot loopback echo test. " * 8, target)
    index = np.arange(20 * RATE, dtype=np.float64)
    sweep = np.sin(2 * np.pi * (200 + 600 * index / len(index)) * index / RATE)
    envelope = 0.5 + 0.5 * np.sin(2 * np.pi * 3.0 * index / RATE)
    payload = np.round(0.4 * sweep * envelope * 32767).astype("<i2")
    with wave.open(str(target), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(payload.tobytes())
    return target


def _read_track(folder: Path, track: str) -> np.ndarray:
    records, _ = read_manifest(folder)
    parts = []
    for record in sorted((r for r in records if r.track == track), key=lambda r: r.seq):
        with wave.open(str(folder / "audio" / record.file), "rb") as handle:
            parts.append(np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16))
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.int16)


def _recorder(tmp_path: Path) -> Recorder:
    config = default_config(audio__capture="wasapi")
    return Recorder(config, lambda track: make_capture(config, track), clock=SystemClock())


def test_enumerate_devices() -> None:
    devices = list_devices()
    assert devices
    render = default_render()
    assert render.rate > 0 and render.max_output_channels >= 1
    loopback = loopback_for(render)
    assert loopback.is_input and loopback.channels >= 1
    try:
        capture = default_capture()
    except NoDeviceError:
        pytest.skip("no microphone on this machine")
    assert capture.rate > 0 and capture.channels >= 1


def test_loopback_echo(tmp_path: Path, source_wav: Path) -> None:
    """T2: play a fixture out the render endpoint and capture it on loopback."""
    recorder = _recorder(tmp_path)
    folder = tmp_path / "meeting"
    recorder.start(folder, "loopback")
    recorder.start_thread()
    started = time.monotonic()
    time.sleep(0.5)
    player = threading.Thread(target=play_wav, args=(source_wav,), daemon=True)
    player.start()
    player.join(timeout=120)
    time.sleep(0.5)
    elapsed_ms = (time.monotonic() - started) * 1000
    result = recorder.stop()

    with wave.open(str(source_wav), "rb") as handle:
        source_rate = handle.getframerate()
        source = np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16)
    if source_rate != RATE:
        import soxr

        source = np.asarray(soxr.resample(source.astype(np.float32), source_rate, RATE))
    captured = _read_track(folder, "them")
    peak, lag = cross_correlation(
        source.astype(np.float64), captured.astype(np.float64), max_lag=RATE
    )
    assert peak >= 0.8, f"cross-correlation {peak:.3f}"
    assert abs(lag) * 1000 / RATE < 500
    # Loopback keeps flowing through the silence either side of the fixture, so the
    # track is as long as the recording, not as the fixture.
    captured_ms = len(captured) * 1000 / RATE
    assert abs(captured_ms - elapsed_ms) <= max(0.01 * elapsed_ms, 300), (captured_ms, elapsed_ms)
    assert result.xruns.get("them", 0) == 0


def test_loopback_chunks_and_manifest(tmp_path: Path, source_wav: Path) -> None:
    recorder = _recorder(tmp_path)
    folder = tmp_path / "meeting"
    recorder.start(folder, "chunks")
    recorder.start_thread()
    started = time.monotonic()
    play_wav(source_wav)
    time.sleep(0.5)
    elapsed_ms = (time.monotonic() - started) * 1000
    recorder.stop()
    records, torn = read_manifest(folder)
    assert torn == 0
    them = [r for r in records if r.track == "them"]
    assert them
    # The track covers the whole recording, silence included — not only the fixture,
    # whose length depends on the machine's speech voice.
    total_ms = sum(r.dur_ms for r in them)
    assert abs(total_ms - elapsed_ms) <= 1500, (total_ms, elapsed_ms)


def test_mic_stream_smoke() -> None:
    """Silence is a pass — this asserts the stream shape, never its content."""
    try:
        default_capture()
    except NoDeviceError:
        pytest.skip("no microphone on this machine")
    capture = WasapiCapture("me")
    capture.start()
    try:
        deadline = time.monotonic() + 5
        collected = 0
        while time.monotonic() < deadline and collected < capture.format.rate:
            payload = capture.read(0.5)
            if payload:
                collected += len(payload) // capture.format.bytes_per_frame
        assert collected >= capture.format.rate, "at least one second of frames"
        assert capture.format.dtype == "float32"
        assert capture.format.rate >= 8000
    finally:
        capture.stop()


def test_dual_stream_concurrent() -> None:
    """The stop condition of the whole project: both streams, together, for 60 s."""
    mic = WasapiCapture("me")
    loopback = WasapiCapture("them")
    mic.start()
    loopback.start()
    try:
        deadline = time.monotonic() + 60
        counts = {"me": 0, "them": 0}
        while time.monotonic() < deadline:
            for name, capture in (("me", mic), ("them", loopback)):
                payload = capture.read(0.05)
                if payload:
                    counts[name] += len(payload) // capture.format.bytes_per_frame
        assert counts["me"] >= mic.format.rate * 50, counts
        assert counts["them"] >= loopback.format.rate * 50, counts
        assert mic.stats.xruns == 0 and loopback.stats.xruns == 0
        assert mic.stats.dropped == 0 and loopback.stats.dropped == 0
    finally:
        mic.stop()
        loopback.stop()


@pytest.mark.slow
def test_soak_two_hours(tmp_path: Path) -> None:
    """Reports clock drift between the two hardware clocks; fails past 1 s/hour.

    Opt-in with ``UP_SOAK_HOURS`` (2 is the §4.3 run): a default gate that sleeps for two
    hours looks exactly like a hang, which is what it did on machine B.
    """
    setting = os.environ.get("UP_SOAK_HOURS")
    if not setting:
        pytest.skip("the soak runs only when UP_SOAK_HOURS is set")
    hours = float(setting)
    recorder = _recorder(tmp_path)
    folder = tmp_path / "meeting"
    recorder.start(folder, "soak")
    recorder.start_thread()
    time.sleep(hours * 3600)
    result = recorder.stop()
    me_ms = result.duration_ms["me"]
    them_ms = result.duration_ms["them"]
    drift_per_hour = abs(me_ms - them_ms) / 1000.0 / hours
    print(f"clock drift: {drift_per_hour:.3f} s/hour over {hours} h")
    assert drift_per_hour <= 1.0, f"{drift_per_hour:.3f} s/hour exceeds the §4.3 budget"
    assert sum(result.xruns.values()) == 0
