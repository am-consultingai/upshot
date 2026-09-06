"""Subtracting the far side from the near track (D37)."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from app.audio.echo import (
    MIN_REDUCTION,
    EchoModel,
    aligned,
    cancel,
    clean_track,
    estimate,
    measure,
)

RATE = 16000
DELAY = 1683  # +105 ms, the value measured on the author's machine
GAIN = 1.715


def speechish(seconds: float, seed: int, amplitude: float = 0.15) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.normal(0, amplitude, int(seconds * RATE)) * 32767).astype(np.int16)


def leaked(own: np.ndarray, them: np.ndarray, gain: float = GAIN, delay: int = DELAY) -> np.ndarray:
    """What the microphone bus hands over: the near voice plus a copy of the far side."""
    mixed = own.astype(np.float64) + gain * aligned(them, delay, len(own))
    return np.clip(mixed, -32768, 32767).astype(np.int16)


def energy(signal: np.ndarray) -> float:
    values = np.asarray(signal, dtype=np.float64)
    return float(values @ values)


def test_a_digital_copy_is_found_and_removed() -> None:
    them = speechish(20.0, seed=1)
    own = speechish(20.0, seed=2, amplitude=0.02)
    me = leaked(own, them)

    model = estimate(me, them, rate=RATE)
    assert model is not None
    assert model.delay == DELAY
    assert model.gain == pytest.approx(GAIN, abs=0.02)
    assert model.correlation > 0.95
    assert model.reduction > 0.95, "a clean digital copy should almost entirely disappear"


def test_the_near_voice_survives_the_subtraction() -> None:
    """The point of the exercise: remove the far side, keep the person holding the mic."""
    them = speechish(20.0, seed=1)
    own = speechish(20.0, seed=2, amplitude=0.02)
    model = estimate(leaked(own, them), them, rate=RATE)
    assert model is not None

    residual = cancel(leaked(own, them), aligned(them, model.delay, len(own)), model.gain)
    assert energy(residual) == pytest.approx(energy(own), rel=0.05)
    correlation = float(np.corrcoef(residual, own.astype(np.float64))[0, 1])
    assert correlation > 0.99, "what is left is the near voice, not a mangled version of it"


def test_two_people_talking_is_not_an_echo() -> None:
    model = estimate(speechish(20.0, seed=2), speechish(20.0, seed=3), rate=RATE)
    assert model is None, "independent voices must not be fitted to each other"


def test_a_silent_far_track_has_nothing_to_subtract() -> None:
    assert estimate(speechish(5.0, seed=2), np.zeros(5 * RATE, dtype=np.int16), rate=RATE) is None


def test_too_short_to_measure() -> None:
    assert estimate(speechish(0.5, seed=1), speechish(0.5, seed=1), rate=RATE) is None


def test_a_partial_leak_is_reported_rather_than_hidden() -> None:
    """Measured at 0.651 on a real recording. It is fitted; whether to *apply* it is the
    caller's decision, and by default it is left alone (D36)."""
    them = speechish(20.0, seed=1)
    own = speechish(20.0, seed=2, amplitude=0.20)
    model = estimate(leaked(own, them, gain=0.5), them, rate=RATE)
    assert model is not None
    assert 0.3 < model.correlation < 0.85
    assert model.reduction > MIN_REDUCTION


# ------------------------------------------------------------------ window selection


def test_the_window_is_chosen_where_the_far_side_is_talking(tmp_path: Path) -> None:
    """A meeting that opens with two minutes of the near side alone still gets measured.

    Fitting "the first minute" would find nothing there — and averaging over the whole
    span would dilute the correlation below the threshold, so the leak would go
    unnoticed on any meeting where the far side is a minority of the audio.
    """
    quiet, loud = 120.0, 60.0
    them = np.concatenate([np.zeros(int(quiet * RATE), dtype=np.int16), speechish(loud, seed=1)])
    own = speechish(quiet + loud, seed=2, amplitude=0.02)
    me = leaked(own, them)

    assert estimate(me[: 60 * RATE], them[: 60 * RATE], rate=RATE) is None

    write(tmp_path / "me.wav", me)
    write(tmp_path / "them.wav", them)
    model = measure(tmp_path / "me.wav", tmp_path / "them.wav", scan_s=600, window_s=60)
    assert model is not None
    assert model.delay == DELAY
    assert model.correlation > 0.9


# ------------------------------------------------------------------ files and meta.json


def write(path: Path, samples: np.ndarray, rate: int = RATE) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(samples.astype("<i2").tobytes())
    return path


def test_the_cleaned_file_matches_the_in_memory_subtraction(tmp_path: Path) -> None:
    """It is written block by block with a seek per block; that must not smear the seam."""
    them = speechish(12.0, seed=1)
    own = speechish(12.0, seed=2, amplitude=0.02)
    me = leaked(own, them)
    write(tmp_path / "me.wav", me)
    write(tmp_path / "them.wav", them)
    model = measure(tmp_path / "me.wav", tmp_path / "them.wav")
    assert model is not None

    clean_track(tmp_path / "me.wav", tmp_path / "them.wav", tmp_path / "clean.wav", model)
    with wave.open(str(tmp_path / "clean.wav"), "rb") as handle:
        assert handle.getframerate() == RATE
        got = np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16)

    expected = np.clip(
        cancel(me, aligned(them, model.delay, len(me)), model.gain), -32768, 32767
    ).astype(np.int16)
    assert np.array_equal(got, expected)
    assert len(got) == len(me), "the cleaned track keeps the meeting's timeline"


def test_a_shorter_far_track_leaves_the_tail_alone(tmp_path: Path) -> None:
    them = speechish(4.0, seed=1)
    own = speechish(12.0, seed=2, amplitude=0.02)
    me = leaked(own, them)
    write(tmp_path / "me.wav", me)
    write(tmp_path / "them.wav", them)
    model = measure(tmp_path / "me.wav", tmp_path / "them.wav")
    assert model is not None
    clean_track(tmp_path / "me.wav", tmp_path / "them.wav", tmp_path / "clean.wav", model)
    with wave.open(str(tmp_path / "clean.wav"), "rb") as handle:
        got = np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16)
    assert np.array_equal(got[5 * RATE :], me[5 * RATE :]), "nothing to subtract after them ends"


def test_the_model_round_trips_through_meta_json() -> None:
    model = EchoModel(gain=1.715, delay=1683, correlation=0.974, reduction=0.948, rate=RATE)
    assert model.delay_ms == pytest.approx(105.19, abs=0.01)
    restored = EchoModel.from_dict(model.as_dict())
    assert restored is not None
    assert (restored.gain, restored.delay, restored.rate) == (1.715, 1683, RATE)


@pytest.mark.parametrize(
    "payload", [None, {}, "echo", {"gain": "x", "delay": 1}, {"gain": 99.0, "delay": 1}]
)
def test_a_broken_model_is_ignored_rather_than_trusted(payload: object) -> None:
    assert EchoModel.from_dict(payload) is None
