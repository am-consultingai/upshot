from __future__ import annotations

import wave
from pathlib import Path

import pytest

from tests.fixtures import speech

pytestmark = pytest.mark.windows


@pytest.fixture(autouse=True)
def _requires_sapi() -> None:
    if not speech.available():
        pytest.skip("SAPI is only available on Windows")


def test_speech_fixture_generates(tmp_path: Path) -> None:
    out = speech.synth("hello world, this is the meeting agent", tmp_path / "hello.wav")
    assert out.exists()
    with wave.open(str(out), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getnframes() / w.getframerate() > 0.5


def test_speech_fixture_cached() -> None:
    first = speech.synth("cached phrase for the meeting agent")
    mtime = first.stat().st_mtime_ns
    second = speech.synth("cached phrase for the meeting agent")
    assert second == first
    assert second.stat().st_mtime_ns == mtime
