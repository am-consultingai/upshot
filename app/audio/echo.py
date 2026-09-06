"""Removing the far side from the near track (DECISIONS D37).

A microphone endpoint that also carries system audio records the far side twice: once
properly on ``them``, and once as a leak into ``me``. Detection landed in D36; this
removes it.

The leak is not an acoustic echo. It never leaves the machine — a virtual-cable bus
routes playback into the recording device — so what arrives on ``me`` is a *digital copy*
of ``them``, scaled by one gain and delayed by one constant. Measured on the author's
recordings: the delay sits at 1683 samples and does not move across the meeting, the gain
at 1.71 ± 0.01, and subtracting that single tap removes **94.8%** of the near track's
energy (99% within the windows where the far side is actually talking). Fitting an 8-,
32- or 128-tap filter instead buys at most two further points, which is not worth the
machinery.

Nothing here touches the recording. ``me.wav`` on disk stays exactly what the device
produced; the subtraction happens on the way into the ASR and on the way out of the
mixer, and the model that describes it is written to ``meta.json`` so both paths agree.
"""

from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.audio.analysis import cross_correlation
from app.log import get

log = get(__name__)

#: How far apart the two copies may be. The bus adds buffering, not distance: measured
#: values are 69–113 ms, and half a second is already generous.
MAX_LAG_S = 0.5

#: The bus may amplify — measured gains are 1.49 and 1.72, both above unity — but a fit
#: this far from 1.0 is not a leak, it is noise fitting noise.
MAX_GAIN = 8.0

#: Below this the subtraction is not paying for itself and is more likely to be removing
#: something that was genuinely said.
MIN_REDUCTION = 0.05


@dataclass(frozen=True)
class EchoModel:
    """``me[n]`` carries ``gain * them[n - delay]``, and how well that held."""

    gain: float
    delay: int  # samples
    correlation: float
    reduction: float  # fraction of the near track's energy the subtraction removes
    rate: int

    @property
    def delay_ms(self) -> float:
        return self.delay * 1000.0 / self.rate if self.rate else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "gain": round(self.gain, 4),
            "delay": self.delay,
            "delay_ms": round(self.delay_ms, 2),
            "correlation": round(self.correlation, 4),
            "reduction": round(self.reduction, 4),
            "rate": self.rate,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> EchoModel | None:
        if not isinstance(payload, dict):
            return None
        try:
            model = cls(
                gain=float(payload["gain"]),
                delay=int(payload["delay"]),
                correlation=float(payload.get("correlation", 0.0)),
                reduction=float(payload.get("reduction", 0.0)),
                rate=int(payload.get("rate", 16000)),
            )
        except (KeyError, TypeError, ValueError):
            return None
        return model if abs(model.gain) <= MAX_GAIN else None


def aligned(them: np.ndarray, delay: int, length: int) -> np.ndarray:
    """``them`` placed on ``me``'s timeline: ``out[n] = them[n - delay]``, zero outside."""
    out = np.zeros(length, dtype=np.float64)
    source = np.asarray(them, dtype=np.float64)
    if delay >= 0:
        take = source[: max(0, length - delay)]
        out[delay : delay + len(take)] = take
    else:
        take = source[-delay : -delay + length]
        out[: len(take)] = take
    return out


def estimate(
    me: np.ndarray, them: np.ndarray, *, rate: int, max_lag_s: float = MAX_LAG_S
) -> EchoModel | None:
    """Fit one gain and one delay, or ``None`` when there is nothing to fit.

    The gain is least squares rather than a peak ratio: it is the scalar that actually
    minimises what is left over, which is the number the subtraction will be judged by.
    """
    length = min(len(me), len(them))
    if length < rate:  # less than a second is not a measurement
        return None
    near = np.asarray(me[:length], dtype=np.float64)
    far = np.asarray(them[:length], dtype=np.float64)
    energy = float(near @ near)
    if energy <= 0 or not np.any(far):
        return None

    correlation, delay = cross_correlation(near, far, max_lag=int(max_lag_s * rate))
    reference = aligned(far, delay, length)
    denominator = float(reference @ reference)
    if denominator <= 0:
        return None
    gain = float(reference @ near) / denominator
    if not np.isfinite(gain) or abs(gain) > MAX_GAIN:
        return None
    residual = near - gain * reference
    reduction = 1.0 - float(residual @ residual) / energy
    if reduction < MIN_REDUCTION:
        return None
    return EchoModel(
        gain=gain,
        delay=delay,
        correlation=abs(float(correlation)),
        reduction=reduction,
        rate=rate,
    )


def cancel(me: np.ndarray, reference: np.ndarray, gain: float) -> np.ndarray:
    """``me - gain * reference`` as int32, unclipped. Both arrays are already aligned."""
    near = np.asarray(me, dtype=np.int32)
    far = np.asarray(reference, dtype=np.float64)
    return near - np.rint(gain * far).astype(np.int32)


# --------------------------------------------------------------------------- files


def read_head(path: Path, samples: int) -> tuple[np.ndarray, int]:
    """The first ``samples`` frames. Bounded: a four-hour track is 460 MB."""
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        raw = handle.readframes(min(samples, handle.getnframes()))
    return np.frombuffer(raw, dtype=np.int16), rate


def loudest_window(me: np.ndarray, them: np.ndarray, window: int) -> tuple[np.ndarray, np.ndarray]:
    """The stretch where the far side is loudest, which is where the leak is visible.

    Estimating on "the first minute" fails on a real meeting that opens with a minute of
    the near side alone — measured on `2026-09-03_1833_597be5`, whose first ten seconds
    have no far-side audio at all. Whole-span least squares is not the answer either: the
    *correlation* gate would be diluted by every quiet minute and the leak would go
    unnoticed. So pick the window and judge it there.
    """
    length = min(len(me), len(them))
    if length <= window:
        return me[:length], them[:length]
    step = max(1, window // 4)
    energy = np.square(np.asarray(them[:length], dtype=np.float64))
    best_start, best = 0, -1.0
    for start in range(0, length - window + 1, step):
        total = float(energy[start : start + window].sum())
        if total > best:
            best_start, best = start, total
    return me[best_start : best_start + window], them[best_start : best_start + window]


def measure(
    me_path: Path, them_path: Path, *, scan_s: float = 600.0, window_s: float = 60.0
) -> EchoModel | None:
    """Fit the model from the two track files, reading at most ``scan_s`` of each."""
    me, rate = read_head(me_path, int(scan_s * 16000))
    them, them_rate = read_head(them_path, int(scan_s * 16000))
    if rate != them_rate:  # pragma: no cover - both are written at the storage rate
        log.warning("tracks disagree on sample rate; not estimating echo")
        return None
    near, far = loudest_window(me, them, int(window_s * rate))
    return estimate(near, far, rate=rate)


def clean_track(me_path: Path, them_path: Path, out_path: Path, model: EchoModel) -> Path:
    """Write ``me`` with the far side subtracted, streaming so a long meeting fits in RAM."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    block = 1 << 20  # ~1 M frames, 2 MB per track
    with (
        wave.open(str(me_path), "rb") as near,
        wave.open(str(them_path), "rb") as far,
        wave.open(str(out_path), "wb") as out,
    ):
        rate = near.getframerate()
        frames = near.getnframes()
        far_frames = far.getnframes()
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        for start in range(0, frames, block):
            count = min(block, frames - start)
            chunk = np.frombuffer(near.readframes(count), dtype=np.int16)
            # The reference for me[start..] is them[start - delay ..], zero-padded where
            # that falls outside the far track.
            reference = np.zeros(len(chunk), dtype=np.int16)
            begin = start - model.delay
            pad = max(0, -begin)
            available = min(len(chunk) - pad, far_frames - (begin + pad))
            if available > 0:
                far.setpos(begin + pad)
                read = np.frombuffer(far.readframes(available), dtype=np.int16)
                reference[pad : pad + len(read)] = read
            cleaned = np.clip(cancel(chunk, reference, model.gain), -32768, 32767)
            out.writeframes(cleaned.astype("<i2").tobytes())
    return out_path
