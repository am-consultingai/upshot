"""The real ONNX diarizer.

Skipped unless the models are present, because they are a ~37 MB opt-in download:

    uv sync --extra diarization
    uv run python -c "from app.asr.models import download_diarization; \\
        from app.config import default_config; print(download_diarization(default_config()))"
    UP_DIARIZATION_MODELS=<dir> uv run pytest -q tests/e2e/test_diarization_onnx.py
"""

from __future__ import annotations

import os
import time
import wave
from pathlib import Path

import numpy as np
import pytest

from app.asr.diarize import OnnxDiarizer, speaker_count

pytestmark = pytest.mark.slow


def models_dir() -> Path | None:
    from app.config import default_config

    env = os.environ.get("UP_DIARIZATION_MODELS")
    if env and Path(env).is_dir():
        return Path(env)
    from app.asr.models import resolve_diarization

    resolved = resolve_diarization(default_config())
    return resolved.segmentation.parent if resolved.present else None


@pytest.fixture
def diarizer() -> OnnxDiarizer:
    pytest.importorskip("sherpa_onnx")
    directory = models_dir()
    if directory is None:
        pytest.skip("no diarization models — see this module's docstring")
    return OnnxDiarizer(str(directory / "segmentation.onnx"), str(directory / "embedding.onnx"))


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        raw = handle.readframes(handle.getnframes())
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio[: len(audio) // channels * channels].reshape(-1, channels).mean(axis=1)
    return audio, rate


def test_onnx_diarizer_produces_well_formed_turns(diarizer: OnnxDiarizer) -> None:
    rng = np.random.default_rng(5)
    audio = (rng.normal(0, 0.05, 16000 * 12)).astype(np.float32)
    turns = diarizer.diarize(audio, 16000)
    duration = len(audio) / 16000
    for turn in turns:
        assert 0.0 <= turn.start < turn.end <= duration + 0.5
        assert isinstance(turn.speaker, int) and turn.speaker >= 0
    assert [t.start for t in turns] == sorted(t.start for t in turns)
    # ids are normalized: no gaps, always starting at 0
    if turns:
        assert sorted({t.speaker for t in turns}) == list(range(speaker_count(turns)))
    diarizer.unload()


def test_onnx_separates_real_speakers(diarizer: OnnxDiarizer) -> None:
    """The accuracy check. Needs a real multi-speaker recording, not a synthetic tone."""
    sample = os.environ.get("UP_DIARIZATION_SAMPLE")
    if not sample or not Path(sample).exists():
        pytest.skip("set UP_DIARIZATION_SAMPLE to a two-speaker WAV")
    expected = int(os.environ.get("UP_DIARIZATION_SAMPLE_SPEAKERS", "2"))
    audio, rate = read_wav(Path(sample))
    started = time.monotonic()
    turns = diarizer.diarize(audio, rate)
    elapsed = time.monotonic() - started
    found = speaker_count(turns)
    print(
        f"diarization: {len(audio) / rate:.1f}s audio in {elapsed:.1f}s "
        f"({len(audio) / rate / elapsed:.1f}x real time), {len(turns)} turns, {found} speakers"
    )
    assert found == expected, f"expected {expected} speakers, found {found}"
    assert turns[0].start >= 0
