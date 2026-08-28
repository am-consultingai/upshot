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


def rms(signal: np.ndarray) -> float:
    audio = np.asarray(signal, dtype=np.float64)
    if audio.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(audio))))
