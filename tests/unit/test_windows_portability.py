"""What the static portability sweep (Windows testing 2) found: behaviour a stranger's
Windows machine, or the windowed frozen build, would trip over."""

from __future__ import annotations

import logging
import socket
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app import instance, log, paths, server
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


def test_a_taken_port_falls_back_to_the_next_free_one() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        taken = holder.getsockname()[1]
        assert not server.port_is_free("127.0.0.1", taken)
        chosen = server.choose_port("127.0.0.1", taken)
    assert chosen in server.FALLBACK_PORTS


def test_a_free_port_is_kept() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    assert server.choose_port("127.0.0.1", free) == free


def test_no_free_port_is_an_error_that_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "port_is_free", lambda host, port: False)
    with pytest.raises(OSError, match="8000 and 8010-8040 are all in use"):
        server.choose_port("127.0.0.1", 8000)


def test_a_second_launch_opens_the_running_instance(tmp_path: Path) -> None:
    opened: list[str] = []
    assert not instance.show_running(tmp_path, opener=opened.append)  # nothing recorded
    instance.record_port(8012, tmp_path)
    assert instance.show_running(tmp_path, opener=opened.append)
    assert opened == ["http://127.0.0.1:8012/"]


def test_a_damaged_port_file_is_ignored(tmp_path: Path) -> None:
    (tmp_path / instance.PORT_FILE).write_text("not a port", encoding="utf-8")
    assert instance.recorded_port(tmp_path) is None


def test_the_instance_mutex_is_per_user() -> None:
    assert instance.MUTEX_NAME.startswith("Local\\")


def test_the_installer_and_uninstaller_close_the_running_app() -> None:
    """The tray app has no window for the Restart Manager to close, so the uninstaller
    left it running with 57 files behind (job 022). Both now ask it to quit first."""
    iss = Path("packaging/installer.iss").read_text(encoding="utf-8")
    assert f'#define AppMutex "{instance.MUTEX_NAME}"' in iss
    assert "'--quit'" in iss
    assert "function PrepareToInstall" in iss
    assert "if CurUninstallStep = usUninstall then\n    StopUpshot;" in iss


def test_quit_with_nothing_running_says_so() -> None:
    assert instance.request_quit(r"Local\upshot-quit-test-nobody") is False


@pytest.mark.skipif(sys.platform != "win32", reason="named events are Windows")
def test_quit_reaches_the_running_instance() -> None:
    import threading

    heard = threading.Event()
    name = r"Local\upshot-quit-test"
    assert instance.watch_quit(heard.set, name) is not None
    assert instance.request_quit(name)
    assert heard.wait(5)


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


def test_the_window_opens_in_a_chromium_browser_in_app_mode() -> None:
    """Upshot's window is a browser in app mode (app/window.py): the default browser when
    it can do that, and the executable is read from its registered open command."""
    from app import window

    chrome = r'"C:\Program Files\Google\Chrome\Application\chrome.exe" --single-argument %1'
    assert (
        window.exe_from_command(chrome) == r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    )
    assert window.exe_from_command(r"C:\edge\msedge.exe --x %1") == r"C:\edge\msedge.exe"
    assert window.supports_app_mode(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    assert window.supports_app_mode(r"C:\x\MSEDGE.EXE")
    assert not window.supports_app_mode(r"C:\Program Files\Mozilla Firefox\firefox.exe")
    assert not window.supports_app_mode(None)
    assert window.app_command("msedge.exe", "http://127.0.0.1:8010/?k=a") == [
        "msedge.exe",
        "--app=http://127.0.0.1:8010/?k=a",
    ]
