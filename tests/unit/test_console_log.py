"""Every install and sign-in window is recorded, and a failed Codex installer falls back."""

from __future__ import annotations

from pathlib import Path

from app.llm import claude_cli, codex_cli
from app.llm.console_log import log_path, transcribed


def test_a_script_is_recorded_from_first_line_to_last(app_home: Path) -> None:
    script = transcribed("Write-Host 'hi'", "codex-install")
    path = log_path("codex-install")
    assert path == app_home / "logs" / "codex-install.log"
    assert script.startswith("New-Item -ItemType Directory")
    assert f"Start-Transcript -Path '{path}' -Append" in script
    assert script.endswith("Stop-Transcript | Out-Null")
    # The facts an installer failure is diagnosed by, asked the way the installer asks.
    assert "Set-StrictMode -Version Latest" in script and "OSArchitecture" in script
    # It crosses a Windows command line as one argument: no double quotes inside.
    assert '"' not in script


def test_the_codex_install_window_is_recorded_and_falls_back(app_home: Path) -> None:
    argv = codex_cli.install_console(codex_cli.NATIVE_INSTALL, fallback=codex_cli._NATIVE_FALLBACK)
    script = argv[-1]
    assert argv[:4] == ["powershell.exe", "-NoProfile", "-NoExit", "-Command"]
    assert "codex-install.log" in script
    # The installer runs in a child scope, so its strict mode cannot leak, and a failure
    # is written out in full before npm, then winget, are tried.
    assert f"try {{ & {{ {codex_cli.NATIVE_INSTALL} }} }} catch {{" in script
    assert "$_.ScriptStackTrace" in script
    assert script.index("npm.cmd install -g @openai/codex") < script.index("winget install")
    # And it still goes on to the sign-in afterwards.
    assert script.index("catch {") < script.index("& $c login")


def test_sign_in_and_claude_windows_are_recorded_too(app_home: Path) -> None:
    assert "codex-signin.log" in codex_cli.login_console(["codex", "login"])[-1]
    assert "claude-signin.log" in claude_cli.login_console(["claude", "auth", "login"])[-1]
    install = claude_cli._console("irm https://claude.ai/install.ps1 | iex")
    assert "claude-install.log" in install[-1]
