"""Signal comparison for the T2 loopback echo test.

Pure numpy: this ships, because ``selftest audio`` runs on a user's machine when they
report "it didn't record me".
"""

from __future__ import annotations

import numpy as np


def normalize(signal: np.ndarray) -> np.ndarray:
    audio = np.asarray(signal, dtype=np.float64)
    if audio.dtype.kind == "i":  # pragma: no cover - defensive
        audio = audio / 32768.0
    audio = audio - audio.mean()
    norm = np.linalg.norm(audio)
    return audio / norm if norm > 0 else audio


def cross_correlation(
    a: np.ndarray, b: np.ndarray, *, max_lag: int | None = None
) -> tuple[float, int]:
    """Peak normalized correlation of ``b`` against ``a``, and the lag in samples."""
    x = normalize(np.asarray(a, dtype=np.float64))
    y = normalize(np.asarray(b, dtype=np.float64))
    if len(x) == 0 or len(y) == 0:
        return 0.0, 0
    size = 1 << int(np.ceil(np.log2(len(x) + len(y))))
    spectrum = np.fft.rfft(x, size) * np.conj(np.fft.rfft(y, size))
    corr = np.fft.irfft(spectrum, size)
    lags = np.arange(size)
    lags = np.where(lags > size // 2, lags - size, lags)
    if max_lag is not None:
        mask = np.abs(lags) <= max_lag
        corr = corr[mask]
        lags = lags[mask]
    index = int(np.argmax(corr))
    return float(corr[index]), int(lags[index])


def envelope(signal: np.ndarray, rate: int, *, window_ms: int = 50, hop_ms: int = 10) -> np.ndarray:
    """Short-time RMS, one value per ``hop_ms``, over ``window_ms`` windows."""
    audio = np.asarray(signal, dtype=np.float64)
    hop = max(1, rate * hop_ms // 1000)
    window = max(hop, rate * window_ms // 1000)
    if len(audio) < window:
        return np.zeros(0)
    power = np.concatenate([[0.0], np.cumsum(np.square(audio))])
    starts = np.arange(0, len(audio) - window + 1, hop)
    return np.asarray(np.sqrt((power[starts + window] - power[starts]) / window))


def envelope_correlation(
    a: np.ndarray, b: np.ndarray, rate: int, *, max_lag: int | None = None, hop_ms: int = 10
) -> tuple[float, int]:
    """Like :func:`cross_correlation`, on the loudness envelopes; the lag is in samples.

    Loopback returns the signal *after* the endpoint's effects. Vendor enhancement chains
    (Waves MaxxAudio on Dell/Realtek laptops, measured on machine B) apply ±13 dB of EQ,
    compression and phase shifts, which take the waveform correlation of a perfect capture
    down to 0.3–0.5. When the sound comes and goes is untouched by all of that: the
    envelope correlation of the same capture was 0.91 on speech.
    """
    hop = max(1, rate * hop_ms // 1000)
    x = envelope(a, rate, hop_ms=hop_ms)
    y = envelope(b, rate, hop_ms=hop_ms)
    peak, lag = cross_correlation(x, y, max_lag=None if max_lag is None else max_lag // hop)
    return peak, lag * hop


#: Loopback carries the played signal when the waveforms match this well, or, through an
#: enhancement chain, when the envelopes match :data:`ENVELOPE_THRESHOLD`. On machine B's
#: captures the envelopes of the right signal scored 0.70–0.87; noise and the wrong
#: capture scored at most 0.14.
WAVEFORM_THRESHOLD = 0.8
ENVELOPE_THRESHOLD = 0.6


def rms(signal: np.ndarray) -> float:
    audio = np.asarray(signal, dtype=np.float64)
    if audio.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(audio))))


#: A digital copy of one track into the other lands well above this; two people genuinely
#: talking land near zero, and even speaker-into-microphone bleed across a room stays well
#: below it, because the acoustic path colours the signal heavily. Fitting the leak — gain,
#: delay and how much of the track it accounts for — is :mod:`app.audio.echo`.
CROSSTALK_THRESHOLD = 0.85
