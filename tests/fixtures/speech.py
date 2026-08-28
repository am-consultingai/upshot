"""T1 — speech fixtures synthesized by Windows SAPI.

Audio fixtures are generated on demand rather than committed as binaries, and the
expected transcript is therefore known exactly. Cached by hash of (text, rate, voice)
so a session synthesizes each phrase once.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import wave
from pathlib import Path

SSFM_CREATE_FOR_WRITE = 3


def available() -> bool:
    if sys.platform != "win32":
        return False
    try:  # pragma: no cover - exercised only on Windows
        import win32com.client  # noqa: F401
    except Exception:
        return False
    return True


def cache_dir() -> Path:
    root = Path(os.environ.get("MA_SPEECH_CACHE", Path(tempfile.gettempdir()) / "ma-speech"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def fixture_path(text: str, rate: int = 0, voice: str = "") -> Path:
    key = hashlib.sha256(f"{text}|{rate}|{voice}".encode()).hexdigest()[:16]
    return cache_dir() / f"{key}.wav"


def synth(text: str, out: Path | None = None, rate: int = 0, voice: str = "") -> Path:
    """Synthesize ``text`` to a WAV. Returns the cached file if it already exists."""
    target = out or fixture_path(text, rate, voice)
    if target.exists() and target.stat().st_size > 0:
        return target
    if not available():
        raise RuntimeError("SAPI speech synthesis is only available on Windows")
    import win32com.client  # pragma: no cover - Windows only

    target.parent.mkdir(parents=True, exist_ok=True)
    v = win32com.client.Dispatch("SAPI.SpVoice")
    if voice:
        for token in v.GetVoices():
            if voice.lower() in token.GetDescription().lower():
                v.Voice = token
                break
    fs = win32com.client.Dispatch("SAPI.SpFileStream")
    fs.Open(str(target), SSFM_CREATE_FOR_WRITE)
    v.AudioOutputStream = fs
    v.Rate = rate
    v.Speak(text)
    fs.Close()
    return target


def duration_s(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())
