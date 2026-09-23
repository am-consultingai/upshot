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
    # The facts an installer failure is diagnosed by: the real architecture (asked for by
    # its mscorlib name, which PSReadLine's copy cannot shadow) and PSReadLine itself.
    assert "RuntimeInformation, mscorlib]::OSArchitecture" in script
    assert "Get-Module PSReadLine" in script
    # It crosses a Windows command line as one argument: no double quotes inside.
    assert '"' not in script


def test_the_codex_install_window_is_recorded_and_falls_back(app_home: Path) -> None:
    plan_argv = codex_cli.install_console(
        codex_cli.isolated(codex_cli.NATIVE_INSTALL),
        shown=codex_cli.NATIVE_INSTALL,
        fallback=codex_cli._NATIVE_FALLBACK,
    )
    script = plan_argv[-1]
    assert plan_argv[:4] == ["powershell.exe", "-NoProfile", "-NoExit", "-Command"]
    assert "codex-install.log" in script
    # The user is shown OpenAI's line, not the wrapper around it.
    assert f"Write-Host 'Running: {codex_cli.NATIVE_INSTALL}'" in script
    # A failure is written out in full before npm, then winget, are tried.
    assert "$_.ScriptStackTrace" in script
    assert script.index("npm.cmd install -g @openai/codex") < script.index("winget install")
    # And it still goes on to the sign-in afterwards.
    assert script.index("catch {") < script.index("& $c login")


def test_the_installer_runs_where_psreadline_cannot_shadow_its_types() -> None:
    """OpenAI's installer, in a non-interactive PowerShell of its own (codex_cli.isolated).

    In the interactive install window, PSReadLine 2.0.0 — shipped inside Windows — makes
    ``RuntimeInformation`` resolve to its own copy without ``OSArchitecture``, and
    ``install.ps1`` died on every stock machine. A non-interactive PowerShell never loads
    PSReadLine. The command travels base64-encoded, so it arrives exactly as written.
    """
    import base64
    import re

    line = codex_cli.isolated(codex_cli.NATIVE_INSTALL)
    assert "-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand " in line
    assert line.startswith("& (Join-Path $env:SystemRoot ")
    encoded = re.search(r"-EncodedCommand (\S+)", line)
    assert encoded is not None
    assert base64.b64decode(encoded.group(1)).decode("utf-16-le") == codex_cli.NATIVE_INSTALL
    # A failed install is an exception, so the fallback takes over.
    assert "if ($LASTEXITCODE -ne 0) { throw" in line
    # Only single quotes: the whole script crosses a Windows command line as one argument.
    assert '"' not in line
    # And the plan the Install button runs is built this way.
    assert codex_cli.isolated(codex_cli.NATIVE_INSTALL) in codex_cli.install_console(
        codex_cli.isolated(codex_cli.NATIVE_INSTALL), fallback=codex_cli._NATIVE_FALLBACK
    )[-1]


def test_sign_in_and_claude_windows_are_recorded_too(app_home: Path) -> None:
    assert "codex-signin.log" in codex_cli.login_console(["codex", "login"])[-1]
    assert "claude-signin.log" in claude_cli.login_console(["claude", "auth", "login"])[-1]
    install = claude_cli._console("irm https://claude.ai/install.ps1 | iex")
    assert "claude-install.log" in install[-1]
