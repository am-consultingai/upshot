"""Settings → AI apps: finding the user's AI apps, and the terminal with each one's own
command typed in (ClickUp epic z8tj1hjg3v). Upshot edits no other app's files (D98)."""

from __future__ import annotations

import sys
from typing import Any

import pytest

from app import ai_terminal

BRIDGE = "C:\\Users\\someone\\AppData\\Local\\Programs\\Upshot\\mcp\\upshot-mcp.exe"
CODEX = f'codex mcp add upshot -- "{BRIDGE}"'


def test_running_distros_are_read_from_utf16_and_docker_is_left_out() -> None:
    raw = "Ubuntu-24.04\r\ndocker-desktop\r\ndocker-desktop-data\r\nDebian\r\n".encode("utf-16-le")
    assert ai_terminal.parse_distros(raw) == ["Ubuntu-24.04", "Debian"]
    assert ai_terminal.parse_distros(b"") == []


def test_only_paths_that_command_v_printed_count_as_found() -> None:
    out = "/home/someone/.local/bin/claude\nalias codex='npx codex'\n/usr/local/bin/agy\n"
    assert ai_terminal.apps_in(out) == ["claude_code", "antigravity"]
    assert ai_terminal.apps_in("bash: no job control in this shell\n") == []


def test_the_first_running_distro_with_the_app_is_the_one(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = {"Ubuntu": "/home/a/.local/bin/agy\n", "Debian": "/usr/bin/agy\n/usr/bin/codex\n"}

    class Done:
        def __init__(self, out: str) -> None:
            self.stdout = out.encode()

    def run(argv: list[str], **_: Any) -> Done:
        assert argv[:3] == ["wsl.exe", "-d", argv[2]] and "-lic" in argv
        return Done(answers[argv[2]])

    monkeypatch.setattr(ai_terminal.subprocess, "run", run)
    assert ai_terminal.find_in_wsl(["Ubuntu", "Debian"]) == {
        "antigravity": "Ubuntu",
        "codex": "Debian",
    }


def test_found_reports_both_sides_for_every_app() -> None:
    found = ai_terminal.Found(windows={"codex": "codex.exe"}, wsl={"antigravity": "Ubuntu"})
    assert found.as_dict() == {
        "claude_code": {"windows": False, "wsl": None},
        "codex": {"windows": True, "wsl": None},
        "antigravity": {"windows": False, "wsl": "Ubuntu"},
    }


@pytest.mark.parametrize(
    ("on_path", "expected"),
    [
        # Installed by the ChatGPT app or OpenAI's installer: the command as shown.
        ("C:\\Users\\someone\\AppData\\Local\\Programs\\OpenAI\\Codex\\bin\\codex.exe", CODEX),
        # npm: codex.ps1 beside codex.cmd; PowerShell would pick the script.
        (
            "C:\\Users\\someone\\AppData\\Roaming\\npm\\codex.CMD",
            f'codex.cmd mcp add upshot -- "{BRIDGE}"',
        ),
        # Found only where its installer leaves it, not on the PATH: the full path.
        (None, f'& "C:\\winget\\codex.exe" mcp add upshot -- "{BRIDGE}"'),
    ],
)
def test_the_windows_line_names_the_program_as_powershell_will_find_it(
    monkeypatch: pytest.MonkeyPatch, on_path: str | None, expected: str
) -> None:
    monkeypatch.setattr(ai_terminal.shutil, "which", lambda program, path=None: on_path)
    assert ai_terminal.windows_line(CODEX, "C:\\winget\\codex.exe", "C:\\fresh") == expected


def test_powershell_types_the_line_and_runs_nothing() -> None:
    argv = ai_terminal.powershell_argv('agy mcp add upshot "C:\\it\'s\\upshot-mcp.exe"')
    assert argv[:4] == ["powershell.exe", "-NoExit", "-NoProfile", "-Command"]
    script = argv[4]
    assert (
        "PSConsoleReadLine]::Insert('agy mcp add upshot \"C:\\it''s\\upshot-mcp.exe\"')" in script
    )
    assert "-MaxTriggerCount 1" in script and "PowerShell.OnIdle" in script
    assert "EncodedCommand" not in " ".join(argv)


def test_wsl_gets_the_line_as_an_argument_never_inside_the_script() -> None:
    line = 'codex mcp add upshot -- "/mnt/c/x/upshot-mcp.exe" --wsl-distro "$WSL_DISTRO_NAME"'
    argv = ai_terminal.wsl_argv(line, "Ubuntu-24.04")
    assert argv[:6] == ["wsl.exe", "-d", "Ubuntu-24.04", "--cd", "~", "-e"]
    assert argv[-1] == line and line not in argv[-3]
    assert 'read -e -i "$1"' in argv[-3]
    assert ai_terminal.wsl_argv(line, None)[:3] == ["wsl.exe", "--cd", "~"]


@pytest.mark.skipif(sys.platform == "win32", reason="looks things up on Windows")
def test_nothing_is_looked_up_off_windows() -> None:
    assert ai_terminal.registry_path() is None
    assert ai_terminal.find_on_windows(None) == {}
    assert ai_terminal.running_distros() == []


@pytest.mark.skipif(sys.platform != "win32", reason="the registry is Windows's")
def test_the_path_is_read_again_from_the_registry() -> None:
    fresh = ai_terminal.registry_path()
    assert fresh and "%" not in fresh, "expanded, machine and user joined"
