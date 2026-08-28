from __future__ import annotations

import os
import wave
from pathlib import Path

import numpy as np
import pytest

from app.audio.fake import SyntheticCapture, tone_block
from app.audio.ring import PreRollRing
from app.audio.vad import FRAME_MS, EnergyGate, SileroVad, TwoStageVad
from app.audio.writer import ChunkWriter, Resampler, read_manifest, recover

RATE = 16000


def tone(seconds: float, rate: int = RATE, freq: float = 440.0, amplitude: float = 0.3):  # type: ignore[no-untyped-def]
    index = np.arange(int(seconds * rate), dtype=np.float64)
    return np.round(amplitude * np.sin(2 * np.pi * freq * index / rate) * 32767).astype(np.int16)


def silence(seconds: float, rate: int = RATE) -> np.ndarray:
    return np.zeros(int(seconds * rate), dtype=np.int16)


# --------------------------------------------------------------------- resampling


def test_resample_sample_count() -> None:
    resampler = Resampler(48000, 16000, channels=2)
    frames = 4800
    total = 0
    for index in range(100):  # 10.0 s of 48 kHz
        mono = tone_block(index * frames, frames, rate=48000)
        stereo = np.repeat(mono[:, None], 2, axis=1).reshape(-1)
        total += len(resampler.process(stereo.tobytes()))
    total += len(resampler.flush())  # the delay line is part of the stream
    assert abs(total - 160_000) <= 16, total


def test_stereo_to_mono_energy() -> None:
    resampler = Resampler(48000, 48000, channels=2)
    left = tone_block(0, 48000, rate=48000, amplitude=0.4)
    right = tone_block(0, 48000, rate=48000, amplitude=0.2)
    stereo = np.stack([left, right], axis=1).reshape(-1)
    mono = resampler.to_mono(stereo)
    expected = (left + right) / 2
    assert np.allclose(mono, expected, atol=1e-6)
    rms = float(np.sqrt(np.mean(mono**2)))
    expected_rms = float(np.sqrt(np.mean(expected**2)))
    assert abs(rms - expected_rms) / expected_rms < 0.01


# --------------------------------------------------------------------- chunking


def test_chunk_prefers_silence_boundary(tmp_path: Path) -> None:
    writer = ChunkWriter(tmp_path, tracks=("me",), rate=RATE)
    writer.write_pcm("me", tone(55))
    writer.write_pcm("me", silence(3))
    writer.write_pcm("me", tone(20))
    records = [r for r in writer.records if r.track == "me"]
    assert records, "a chunk must have been cut"
    cut_ms = records[0].dur_ms
    assert 55_000 <= cut_ms <= 58_000, cut_ms
    assert cut_ms != 60_000
    writer.close()


def test_chunk_hard_cut_on_continuous(tmp_path: Path) -> None:
    writer = ChunkWriter(tmp_path, tracks=("me",), rate=RATE)
    writer.write_pcm("me", tone(75))
    records = [r for r in writer.records if r.track == "me"]
    assert len(records) == 1
    assert records[0].dur_ms == 70_000
    assert records[0].samples == 70 * RATE
    writer.close()


def test_chunk_durations_sum_to_input(tmp_path: Path) -> None:
    writer = ChunkWriter(tmp_path, tracks=("me",), rate=RATE)
    total_s = 200
    for _ in range(total_s):
        writer.write_pcm("me", tone(1))
    records = writer.close()
    assert sum(r.dur_ms for r in records) == total_s * 1000


# --------------------------------------------------------------------- durability


def test_manifest_fsync_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    real_fsync = os.fsync

    def recording_fsync(fd: int) -> None:
        calls.append(f"fsync:{fd}")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", recording_fsync)
    writer = ChunkWriter(tmp_path, tracks=("me",), rate=RATE)
    writer.write_pcm("me", tone(75))
    writer.close()

    events = [e.split(":")[0] for e in writer.events]
    assert events[:4] == ["write", "fsync-wav", "append-manifest", "fsync-manifest"]
    assert len(calls) >= 2, "both the wav and the manifest were fsynced"


def test_manifest_durable_after_kill(tmp_path: Path) -> None:
    writer = ChunkWriter(tmp_path, tracks=("me",), rate=RATE)
    for _ in range(3):
        writer.write_pcm("me", tone(70))
    writer.close()
    manifest = tmp_path / "audio" / "manifest.jsonl"
    lines = manifest.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    # simulate an unclean shutdown: the third line is torn
    manifest.write_text("\n".join(lines[:2]) + "\n" + lines[2][:20], encoding="utf-8")

    records, torn = read_manifest(tmp_path)
    assert len(records) == 2 and torn == 1
    recovered = recover(tmp_path)
    assert len(recovered) == 3, "the third chunk is re-derived from its WAV header"
    third = next(r for r in recovered if r.seq == 3)
    assert third.samples == 70 * RATE
    assert third.t0_ms == 140_000


def test_chunk_files_are_16k_mono_s16(tmp_path: Path) -> None:
    writer = ChunkWriter(tmp_path, tracks=("me",), rate=RATE)
    writer.write_pcm("me", tone(75))
    writer.close()
    with wave.open(str(tmp_path / "audio" / "me" / "0001.wav"), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == RATE


# --------------------------------------------------------------------- pre-roll


def test_preroll_flush_is_chunk_one(tmp_path: Path) -> None:
    ring = PreRollRing(60, RATE)
    payload = tone(30)
    ring.push(payload)
    assert len(ring) == 30 * RATE
    writer = ChunkWriter(tmp_path, tracks=("me",), rate=RATE)
    ring.flush_to(writer, "me")
    writer.flush_track("me")
    records = writer.close()
    assert len(records) == 1
    assert records[0].t0_ms == 0
    assert records[0].samples == 30 * RATE
    with wave.open(str(tmp_path / "audio" / "me" / "0001.wav"), "rb") as handle:
        written = np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16)
    assert np.array_equal(written, payload)


def test_preroll_drop_leaves_no_files(tmp_path: Path) -> None:
    folder = tmp_path / "meeting"
    ring = PreRollRing(60, RATE)
    ring.push(tone(30))
    ring.drop()
    assert len(ring) == 0
    assert not folder.exists()


def test_ring_is_bounded(tmp_path: Path) -> None:
    ring = PreRollRing(2, RATE)
    for _ in range(10):
        ring.push(tone(1))
    assert len(ring) == 2 * RATE
    assert ring.dropped == 8 * RATE
    assert ring.duration_ms == 2000


# --------------------------------------------------------------------- backpressure


def test_backpressure_drops_ring_not_committed(tmp_path: Path) -> None:
    capture = SyntheticCapture("them", "tone", queue_seconds=1.0, generate_on_read=False)
    capture.start()
    for _ in range(50):  # far more than the queue holds, and nothing is draining
        capture.emit()
    assert capture.stats.dropped > 0

    writer = ChunkWriter(tmp_path, tracks=("them",), rate=RATE)
    resampler = Resampler(48000, RATE, 2)
    drained = 0
    while True:
        payload = capture.read(0.0)
        if payload is None:
            break
        drained += 1
        writer.write_pcm("them", resampler.process(payload))
    writer.write_pcm("them", resampler.flush())
    records = writer.close()
    committed_samples = sum(r.samples for r in records)
    expected = drained * 1600  # 4800 device frames at 48 kHz → 1600 at 16 kHz
    assert abs(committed_samples - expected) <= 16, (committed_samples, expected)
    assert all(r.dur_ms > 0 for r in records)


def test_gap_marker_written(tmp_path: Path) -> None:
    writer = ChunkWriter(tmp_path, tracks=("me",), rate=RATE)
    writer.write_pcm("me", tone(70))
    writer.note_gap("me", 300)
    writer.write_pcm("me", tone(70))
    records = writer.close()
    assert records[0].gap_ms == 0
    assert records[1].gap_ms == 300
    assert records[1].t0_ms == 70_300, "the timeline is preserved, not shortened"
    assert records[1].dur_ms == 70_000


# --------------------------------------------------------------------- VAD


def test_vad_two_stage_agreement() -> None:
    """The energy gate is the looser stage: it may never reject what Silero accepts."""
    silero = SileroVad()
    if not silero.available():
        pytest.skip("Silero VAD model unavailable")
    rng = np.random.default_rng(11)
    speechish = (rng.normal(0, 0.15, RATE * 5) * 32767).astype(np.int16)
    for pcm in (tone(5), silence(5), speechish, np.concatenate([silence(2), tone(3)])):
        gate_flags = EnergyGate().voiced_frames(pcm, RATE)
        silero_flags = silero.voiced_frames(pcm, RATE)
        for index, (gated, voiced) in enumerate(zip(gate_flags, silero_flags, strict=True)):
            assert not (voiced and not gated), f"gate rejected frame {index} that Silero accepted"


def test_vad_silence_is_unvoiced() -> None:
    vad = TwoStageVad()
    assert vad.voiced_ratio(silence(5), RATE) < 0.02
    assert vad.silero_calls == 0, "the cheap gate short-circuits silence"


def test_vad_frame_geometry() -> None:
    flags = EnergyGate().voiced_frames(tone(1), RATE)
    assert len(flags) == 1000 // FRAME_MS
    assert all(flags)


@pytest.mark.windows
def test_vad_on_speech_fixture(tmp_path: Path) -> None:
    """T1: synthesized speech is voiced; digital silence is not."""
    from tests.fixtures import speech

    if not speech.available():
        pytest.skip("SAPI is only available on Windows")
    import soxr

    wav = speech.synth(
        "This is a meeting recording test. The quick brown fox jumps over the lazy dog.",
        tmp_path / "speech.wav",
    )
    with wave.open(str(wav), "rb") as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        raw = handle.readframes(handle.getnframes())
    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1)
    if rate != RATE:
        pcm = soxr.resample(pcm / 32768.0, rate, RATE) * 32768.0
    speech_pcm = pcm.astype(np.int16)

    vad = TwoStageVad()
    assert vad.voiced_ratio(speech_pcm, RATE) > 0.5
    assert vad.voiced_ratio(silence(5), RATE) < 0.02
