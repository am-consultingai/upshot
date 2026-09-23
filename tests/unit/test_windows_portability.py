"""What the static portability sweep (Windows testing 2) found: behaviour a stranger's
Windows machine, or the windowed frozen build, would trip over."""

from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app import log, paths
from app.audio import ingest


@pytest.fixture
def fresh_logging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """``log.setup`` as on a first start, with the root logger put back afterwards."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    hooks = sys.excepthook
    monkeypatch.setattr(log, "_configured", False)
    monkeypatch.setattr(paths, "log_dir", lambda: tmp_path / "logs")
    for handler in handlers:
        root.removeHandler(handler)
    try:
        yield tmp_path / "logs" / "app.log"
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()
        for handler in handlers:
            root.addHandler(handler)
        root.setLevel(level)
        sys.excepthook = hooks


def test_app_log_keeps_the_lines_of_a_hebrew_titled_meeting(fresh_logging: Path) -> None:
    log.setup()
    with log.meeting_context("2026-09-23_1000_ab12cd_פגישת-צוות"):
        log.get("test").info("transcribed — שלום")
    files = [h for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler)]
    # Explicit, not the locale's: on Linux the default is UTF-8 anyway, on Windows cp1252.
    assert [h.encoding for h in files] == ["utf-8"]
    for handler in files:
        handler.flush()
    text = fresh_logging.read_text(encoding="utf-8")
    assert "[2026-09-23_1000_ab12cd_פגישת-צוות]" in text
    assert "transcribed — שלום" in text


def test_the_windowed_build_gets_no_console_handler(
    fresh_logging: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "stderr", None)
    log.setup()
    handlers = logging.getLogger().handlers
    assert not [h for h in handlers if type(h) is logging.StreamHandler]
    assert any(isinstance(h, logging.FileHandler) for h in handlers)


def test_ffmpeg_runs_without_a_console_and_a_hang_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def hangs(argv: list[str], **kwargs: Any) -> None:
        seen.update(kwargs)
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(ingest, "ffmpeg_path", lambda config=None: "ffmpeg")
    monkeypatch.setattr(ingest.subprocess, "run", hangs)
    with pytest.raises(ingest.UnsupportedAudio, match="more than"):
        ingest.to_wav(tmp_path / "call.m4a", tmp_path / "out.wav")
    assert seen["timeout"] == ingest.FFMPEG_TIMEOUT_S
    assert seen["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
