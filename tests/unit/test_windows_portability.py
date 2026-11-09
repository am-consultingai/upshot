"""What the static portability sweep (Windows testing 2) found: behaviour a stranger's
Windows machine, or the windowed frozen build, would trip over."""

from __future__ import annotations

import logging
import os
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
    with pytest.raises(OSError, match="43217 and 43218-43247 are all in use"):
        server.choose_port("127.0.0.1", 43217)


def test_a_second_launch_opens_the_running_instance(tmp_path: Path) -> None:
    opened: list[str] = []
    assert not instance.show_running(tmp_path, opener=opened.append)  # nothing recorded
    instance.record_port(43219, tmp_path)
    assert instance.show_running(tmp_path, opener=opened.append)
    assert opened == ["http://127.0.0.1:43219/"]


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


class _Ran:
    def __init__(self, returncode: int, stderr: bytes) -> None:
        self.returncode = returncode
        self.stderr = stderr


#: How Windows ends a process it had no memory to start (the 2026-11-09 report).
NO_MEMORY = 0xC0000017


@pytest.mark.parametrize(
    ("returncode", "stderr"),
    [(NO_MEMORY, b""), (0xC0000005, b""), (-9, b""), (1, b"")],
    ids=["could-not-start", "crashed-before-a-word", "killed", "silent"],
)
def test_ffmpeg_that_did_not_run_is_not_blamed_on_the_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    returncode: int,
    stderr: bytes,
) -> None:
    monkeypatch.setattr(ingest, "ffmpeg_path", lambda config=None: "ffmpeg")
    monkeypatch.setattr(ingest.subprocess, "run", lambda argv, **kw: _Ran(returncode, stderr))
    with caplog.at_level(logging.WARNING, logger=ingest.log.name):
        with pytest.raises(ingest.FfmpegFailed, match=r"could not run to read call\.m4a"):
            ingest.probe(tmp_path / "call.m4a")
        with pytest.raises(ingest.FfmpegFailed):
            ingest.to_wav(tmp_path / "call.m4a", tmp_path / "out.wav")
    assert sum("ffmpeg did not run on call.m4a" in r.getMessage() for r in caplog.records) == 2
    if returncode == NO_MEMORY:
        assert "0xc0000017" in caplog.text


def test_a_file_ffmpeg_cannot_read_is_still_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    said = b"call.m4a: Invalid data found when processing input\n"
    monkeypatch.setattr(ingest, "ffmpeg_path", lambda config=None: "ffmpeg")
    monkeypatch.setattr(ingest.subprocess, "run", lambda argv, **kw: _Ran(1, said))
    with pytest.raises(ingest.UnsupportedAudio, match="not audio or video"):
        ingest.probe(tmp_path / "call.m4a")
    with (
        caplog.at_level(logging.WARNING, logger=ingest.log.name),
        pytest.raises(ingest.UnsupportedAudio, match="could not convert"),
    ):
        ingest.to_wav(tmp_path / "call.m4a", tmp_path / "out.wav")
    assert "Invalid data" in caplog.text, "a failed conversion is in the log"


def test_probe_keeps_the_banner_so_a_crash_on_the_file_is_never_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the banner, ffmpeg that started has always said something, so a file that
    crashes it while it is being described is refused (415), not retried for ever."""
    seen: list[list[str]] = []
    banner = b"ffmpeg version 8.1 Copyright (c) 2000-2026 the FFmpeg developers\n"

    def crashes(argv: list[str], **kwargs: Any) -> _Ran:
        seen.append(argv)
        return _Ran(0xC0000005, banner)

    monkeypatch.setattr(ingest, "ffmpeg_path", lambda config=None: "ffmpeg")
    monkeypatch.setattr(ingest.subprocess, "run", crashes)
    with pytest.raises(ingest.UnsupportedAudio):
        ingest.probe(tmp_path / "crafted.m4a")
    assert "-hide_banner" not in seen[0]


def test_no_memory_is_the_systems_even_after_the_banner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    banner = b"ffmpeg version 8.1 Copyright (c) 2000-2026 the FFmpeg developers\n"
    monkeypatch.setattr(ingest, "ffmpeg_path", lambda config=None: "ffmpeg")
    monkeypatch.setattr(ingest.subprocess, "run", lambda argv, **kw: _Ran(NO_MEMORY, banner))
    with pytest.raises(ingest.FfmpegFailed, match="0xc0000017"):
        ingest.probe(tmp_path / "call.m4a")


class _WinError(OSError):
    """An OSError as Windows raises it, with ``winerror``, on any platform."""

    def __init__(self, winerror: int, text: str) -> None:
        super().__init__(winerror, text)
        self.winerror = winerror


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_WinError(8, "Not enough memory resources"), "FfmpegFailed"),  # wait, as in the report
        (_WinError(193, "%1 is not a valid Win32 application"), "FfmpegBroken"),
        (_WinError(216, "machine type mismatch"), "FfmpegBroken"),
        (_WinError(5, "Access is denied"), "FfmpegBroken"),
        (_WinError(1260, "blocked by group policy"), "FfmpegBroken"),
        (_WinError(225, "contains a virus"), "FfmpegBroken"),
        (_WinError(2, "cannot find the file"), "FfmpegBroken"),
        (_WinError(32, "used by another process"), "FfmpegFailed"),
        (_WinError(1450, "insufficient system resources"), "FfmpegFailed"),
        (OSError(8, "Exec format error"), "FfmpegBroken"),  # ENOEXEC on Linux is 8 too
        (OSError(21, "Is a directory"), "FfmpegBroken"),
        (OSError(13, "Permission denied"), "FfmpegBroken"),
        (OSError(16, "Device or resource busy"), "FfmpegFailed"),
    ],
    ids=[
        "win-no-memory",
        "win-bad-exe",
        "win-arch",
        "win-denied",
        "win-policy",
        "win-virus",
        "win-gone",
        "win-sharing",
        "win-resources",
        "enoexec",
        "eisdir",
        "eacces",
        "ebusy",
    ],
)
def test_a_start_that_failed_is_waited_for_only_when_waiting_can_help(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: OSError, expected: str
) -> None:
    import errno

    if not isinstance(error, _WinError) and expected == "FfmpegBroken":
        assert error.errno in (errno.ENOEXEC, errno.EISDIR, errno.EACCES)

    def fails(argv: list[str], **kwargs: Any) -> None:
        raise error

    monkeypatch.setattr(ingest, "ffmpeg_path", lambda config=None: "ffmpeg")
    monkeypatch.setattr(ingest.subprocess, "run", fails)
    with pytest.raises(getattr(ingest, expected)):
        ingest.probe(tmp_path / "call.m4a")


@pytest.mark.parametrize("code", [0xC0000135, 0xC000007B, 0xC0000139])
def test_an_ffmpeg_that_can_never_run_is_broken_not_waited_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    monkeypatch.setattr(ingest, "ffmpeg_path", lambda config=None: "ffmpeg")
    monkeypatch.setattr(ingest.subprocess, "run", lambda argv, **kw: _Ran(code, b""))
    with pytest.raises(ingest.FfmpegBroken, match="reinstall Upshot"):
        ingest.probe(tmp_path / "call.m4a")
    with pytest.raises(ingest.FfmpegBroken):
        ingest.to_wav(tmp_path / "call.m4a", tmp_path / "out.wav")


def test_a_missing_ffmpeg_is_broken_in_the_installed_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The app ships ffmpeg, so a missing one was removed: not the file's fault."""
    monkeypatch.setattr(ingest, "ffmpeg_path", lambda config=None: None)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with pytest.raises(ingest.FfmpegBroken, match="missing"):
        ingest.probe(tmp_path / "call.m4a")
    with pytest.raises(ingest.FfmpegBroken):
        ingest.to_wav(tmp_path / "call.m4a", tmp_path / "out.wav")
    monkeypatch.delattr(sys, "frozen")
    with pytest.raises(ingest.UnsupportedAudio, match="not found"):
        ingest.probe(tmp_path / "call.m4a")


def test_a_folder_set_as_ffmpeg_path_is_not_used(tmp_path: Path) -> None:
    from app.config import Config

    config = Config.load()
    config.set("ffmpeg_path", str(tmp_path))
    assert ingest.ffmpeg_path(config) != str(tmp_path)


def test_the_home_folder_is_cut_out_only_at_a_folder_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "someone"
    monkeypatch.setattr(ingest.Path, "home", classmethod(lambda cls: home))
    said = f"{home}{os.sep}x.wav: No space left\n{home}else{os.sep}y.wav\n".encode()
    text = ingest._said(tmp_path / "a.m4a", said, 500)
    assert f"~{os.sep}x.wav" in text and f"{home}else" in text


def test_an_os_error_names_no_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refused(argv: list[str], **kwargs: Any) -> None:
        raise PermissionError(13, "Permission denied", str(tmp_path / "someone" / "ffmpeg"))

    monkeypatch.setattr(ingest, "ffmpeg_path", lambda config=None: "ffmpeg")
    monkeypatch.setattr(ingest.subprocess, "run", refused)
    with pytest.raises(ingest.FfmpegBroken) as caught:
        ingest.probe(tmp_path / "call.m4a")
    assert "Permission denied" in str(caught.value) and str(tmp_path) not in str(caught.value)


def test_a_crash_after_ffmpeg_has_spoken_is_the_files_doing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A file that crashes ffmpeg every time would otherwise be sent again for ever."""
    source = tmp_path / "Users" / "someone" / "crafted.m4a"
    said = f"Input #0, mov, from '{source}':\n".encode()
    monkeypatch.setattr(ingest, "ffmpeg_path", lambda config=None: "ffmpeg")
    monkeypatch.setattr(ingest.subprocess, "run", lambda argv, **kw: _Ran(0xC0000005, said))
    with (
        caplog.at_level(logging.WARNING, logger=ingest.log.name),
        pytest.raises(ingest.UnsupportedAudio),
    ):
        ingest.to_wav(source, tmp_path / "out.wav")
    assert "0xc0000005" in caplog.text
    assert str(source) not in caplog.text and "crafted.m4a" in caplog.text, "no full path"


def test_ffmpeg_that_cannot_be_started_at_all_is_ffmpeg_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refused(argv: list[str], **kwargs: Any) -> None:
        raise _WinError(8, "Not enough memory resources")

    monkeypatch.setattr(ingest, "ffmpeg_path", lambda config=None: "ffmpeg")
    monkeypatch.setattr(ingest.subprocess, "run", refused)
    monkeypatch.setattr(ingest.subprocess, "Popen", refused)
    with pytest.raises(ingest.FfmpegFailed, match="Not enough memory"):
        ingest.probe(tmp_path / "call.m4a")
    with pytest.raises(ingest.FfmpegFailed):
        ingest.to_wav(tmp_path / "call.m4a", tmp_path / "out.wav", stop_check=lambda: None)


def test_a_cancellable_conversion_that_could_not_start_is_ffmpeg_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Popen:
        def __init__(self, command: list[str], **kwargs: Any) -> None:
            pass

        def wait(self, timeout: float | None = None) -> int:
            return NO_MEMORY

    monkeypatch.setattr(ingest, "ffmpeg_path", lambda config=None: "ffmpeg")
    monkeypatch.setattr(ingest.subprocess, "Popen", Popen)
    with pytest.raises(ingest.FfmpegFailed, match="0xc0000017"):
        ingest.to_wav(tmp_path / "call.m4a", tmp_path / "out.wav", stop_check=lambda: None)


def test_child_programs_get_no_windows_error_dialogs() -> None:
    from app import errormode

    before = errormode.quiet_child_errors()
    if sys.platform != "win32":
        assert before is None
        return
    import ctypes

    try:
        # What the fix relies on: a program Upshot starts inherits the mode.
        child = subprocess.run(
            [sys.executable, "-c", "import ctypes; print(ctypes.windll.kernel32.GetErrorMode())"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert int(child.stdout) & errormode.SEM_FAILCRITICALERRORS
    finally:
        ctypes.windll.kernel32.SetErrorMode(before)  # type: ignore[attr-defined,unused-ignore]


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
    # Its own browser profile, never one of the user's (z8tj1hca86).
    assert window.app_command("msedge.exe", "http://127.0.0.1:43217/?k=a", Path("H/browser")) == [
        "msedge.exe",
        "--app=http://127.0.0.1:43217/?k=a",
        f"--user-data-dir={Path('H/browser')}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
