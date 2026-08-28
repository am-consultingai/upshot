"""Generate `tests/fixtures/meeting_10min.wav` — the M0 gate's documented input.

Audio fixtures are generated, never committed (EXECUTION-PLAN.md §1, T1). On Windows the
content is real SAPI speech; elsewhere it is a deterministic speech-shaped signal.

    uv run python scripts/make_fixture.py [--minutes 10] [--out PATH]
"""

from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

DEFAULT_OUT = Path("tests/fixtures/meeting_10min.wav")
RATE = 16000

SENTENCES = [
    "Good morning everyone, can you hear me? Let us start the weekly sync.",
    "The service moved to Kubernetes yesterday and the migration is still failing.",
    "I estimate two more days, so I suggest we delay the release to next week.",
    "Agreed. The decision is to delay, and you will update us on Thursday.",
]


def synthetic(minutes: float, rate: int = RATE) -> np.ndarray:
    """Speech-shaped: a voiced formant burst per word, with pauses between phrases."""
    rng = np.random.default_rng(1234)
    total = int(minutes * 60 * rate)
    out = np.zeros(total, dtype=np.float32)
    cursor = 0
    while cursor < total:
        words = rng.integers(4, 12)
        for _ in range(int(words)):
            length = int(rng.uniform(0.18, 0.45) * rate)
            index = np.arange(length, dtype=np.float64)
            pitch = rng.uniform(95, 190)
            envelope = np.hanning(length)
            tone = sum(
                (1.0 / harmonic) * np.sin(2 * np.pi * pitch * harmonic * index / rate)
                for harmonic in (1, 2, 3, 4)
            )
            block = (0.28 * envelope * tone).astype(np.float32)
            end = min(total, cursor + length)
            out[cursor:end] += block[: end - cursor]
            cursor = end + int(rng.uniform(0.03, 0.12) * rate)
            if cursor >= total:
                break
        cursor += int(rng.uniform(0.4, 1.2) * rate)  # a pause between phrases
    return np.clip(out, -1.0, 1.0)


def from_sapi(minutes: float, out: Path) -> np.ndarray | None:
    try:
        from tests.fixtures import speech
    except Exception:
        return None
    if not speech.available():
        return None
    import soxr

    parts: list[np.ndarray] = []
    while sum(len(part) for part in parts) < minutes * 60 * RATE:
        for sentence in SENTENCES:
            path = speech.synth(sentence, out.parent / f"sapi-{abs(hash(sentence)):x}.wav")
            with wave.open(str(path), "rb") as handle:
                rate = handle.getframerate()
                channels = handle.getnchannels()
                raw = handle.readframes(handle.getnframes())
            audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            if channels > 1:
                audio = audio[: len(audio) // channels * channels].reshape(-1, channels).mean(1)
            if rate != RATE:
                audio = np.asarray(soxr.resample(audio, rate, RATE), dtype=np.float32)
            parts.append(audio)
            parts.append(np.zeros(int(0.4 * RATE), dtype=np.float32))
    return np.concatenate(parts)[: int(minutes * 60 * RATE)]


def write(path: Path, audio: np.ndarray, rate: int = RATE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = np.round(np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(payload.tobytes())
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python scripts/make_fixture.py")
    parser.add_argument("--minutes", type=float, default=10.0)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    audio = from_sapi(args.minutes, args.out)
    source = "SAPI speech"
    if audio is None:
        audio = synthetic(args.minutes)
        source = "synthetic speech-shaped signal"
    path = write(args.out, audio)
    print(f"wrote {path} ({args.minutes:g} min, {source})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
