"""The loopback comparison in ``app.audio.analysis``: waveform and envelope correlation."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from app.audio.analysis import (
    ENVELOPE_THRESHOLD,
    WAVEFORM_THRESHOLD,
    cross_correlation,
    envelope,
    envelope_correlation,
    read_wav_at,
)
from app.detect.registry import REG_NOTIFY_CHANGE_LAST_SET

RATE = 16000


def _speechlike(seconds: float, seed: int = 0) -> np.ndarray:
    """Noise bursts of random length and loudness: an envelope with something to lock onto."""
    rng = np.random.default_rng(seed)
    out = np.zeros(int(seconds * RATE))
    at = 0
    while at < len(out):
        span = int(rng.uniform(0.05, 0.4) * RATE)
        out[at : at + span] = rng.normal(0, rng.uniform(0.0, 0.3), len(out[at : at + span]))
        at += span
    return out


def _enhanced(signal: np.ndarray) -> np.ndarray:
    """What machine B's Waves chain did to loopback: EQ, a phase shift and compression."""
    spectrum = np.fft.rfft(signal)
    freqs = np.fft.rfftfreq(len(signal), 1 / RATE)
    gain = np.where(freqs < 200, 4.5, 1.0) * np.where((freqs > 1600) & (freqs < 6400), 3.0, 1.0)
    phase = np.exp(1j * np.pi * np.sin(freqs / 300))
    shaped = np.fft.irfft(spectrum * gain * phase, len(signal))
    return np.asarray(np.tanh(3 * shaped))


def test_envelope_is_short_time_rms() -> None:
    signal = np.concatenate([np.zeros(RATE), np.full(RATE, 0.5)])
    env = envelope(signal, RATE)
    assert len(env) == (len(signal) - 800) // 160 + 1
    assert env[0] == 0.0
    assert abs(env[-1] - 0.5) < 1e-9


def test_envelope_of_a_short_signal_is_empty() -> None:
    assert len(envelope(np.ones(100), RATE)) == 0


def test_enhancement_defeats_the_waveform_but_not_the_envelope() -> None:
    source = _speechlike(10)
    delay = int(0.66 * RATE)  # pre-roll plus output latency, as B measured
    captured = np.concatenate([np.zeros(delay), _enhanced(source), np.zeros(RATE // 2)])

    waveform, _ = cross_correlation(source, captured, max_lag=2 * RATE)
    shape, lag = envelope_correlation(source, captured, RATE, max_lag=2 * RATE)

    assert waveform < WAVEFORM_THRESHOLD
    assert shape >= ENVELOPE_THRESHOLD
    assert abs(-lag - delay) <= 160  # one hop


def test_envelope_rejects_the_wrong_signal() -> None:
    source = _speechlike(10, seed=1)
    other = _speechlike(12, seed=2)
    shape, _ = envelope_correlation(source, other, RATE, max_lag=2 * RATE)
    assert shape < ENVELOPE_THRESHOLD


def _write_wav(path: Path, signal: np.ndarray, rate: int) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(np.round(signal * 32767).astype("<i2").tobytes())


def test_a_sapi_rate_fixture_is_compared_at_the_track_rate(tmp_path: Path) -> None:
    """Job 007: a 22.05 kHz SAPI fixture against a 16 kHz track correlated at 0.02."""
    source = _speechlike(5, seed=3) / 2
    sapi_rate = 22050
    at_sapi = np.interp(
        np.arange(int(5 * sapi_rate)) / sapi_rate, np.arange(len(source)) / RATE, source
    )
    _write_wav(tmp_path / "speech.wav", at_sapi, sapi_rate)
    track = np.round(source * 32767)

    raw = _read_raw(tmp_path / "speech.wav")
    resampled = read_wav_at(tmp_path / "speech.wav", RATE)

    assert abs(len(resampled) - len(track)) <= 2
    assert envelope_correlation(raw, track, RATE, max_lag=RATE)[0] < ENVELOPE_THRESHOLD
    shape, lag = envelope_correlation(resampled, track, RATE, max_lag=RATE)
    assert shape >= 0.95
    assert abs(lag) <= 160


def test_a_fixture_at_the_track_rate_is_read_as_is(tmp_path: Path) -> None:
    source = _speechlike(2, seed=4) / 2
    _write_wav(tmp_path / "tone.wav", source, RATE)
    assert np.array_equal(read_wav_at(tmp_path / "tone.wav", RATE), np.round(source * 32767))


def _read_raw(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        return np.frombuffer(handle.readframes(handle.getnframes()), np.int16).astype(np.float64)


def test_reg_notify_change_last_set_matches_winnt() -> None:
    """pywin32's win32con lacks it, so the watcher carries the winnt.h value itself."""
    assert REG_NOTIFY_CHANGE_LAST_SET == 0x4
