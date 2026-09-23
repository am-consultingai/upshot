"""The loopback comparison in ``app.audio.analysis``: waveform and envelope correlation."""

from __future__ import annotations

import numpy as np

from app.audio.analysis import (
    ENVELOPE_THRESHOLD,
    WAVEFORM_THRESHOLD,
    cross_correlation,
    envelope,
    envelope_correlation,
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


def test_reg_notify_change_last_set_matches_winnt() -> None:
    """pywin32's win32con lacks it, so the watcher carries the winnt.h value itself."""
    assert REG_NOTIFY_CHANGE_LAST_SET == 0x4
