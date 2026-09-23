"""A transcript of every install and sign-in console, in the app's log folder.

Installing or signing in to a subscription CLI happens in a PowerShell window of its own,
because the installer and the vendor's sign-in page need a real console. That window used
to be the only record: when OpenAI's installer failed on 2026-09-23 with
``The property 'OSArchitecture' cannot be found on this object``, the text existed only on
the user's screen, and the application's own log said the console had launched and nothing
more.

``Start-Transcript`` is PowerShell's own recorder. It copies what the console shows into a
file *without* redirecting any stream — which matters, because capturing an installer's
streams is exactly what broke the Claude install once (see ``claude_cli._console``): with
``$ErrorActionPreference = 'Stop'`` a captured stderr line becomes a terminating error.

One file per purpose, appended to (``-Append``), so the last attempt is at the end and the
earlier ones are still there to compare. Each run opens with a few facts about the
PowerShell it ran in: the first thing anyone asks when an installer fails.

Only single quotes inside: the script crosses a Windows command line as one argument.
"""

from __future__ import annotations

from pathlib import Path

from app import paths

LOG_DIR_NAME = "logs"


def log_path(name: str) -> Path:
    """``%LOCALAPPDATA%\\upshot\\logs\\<name>.log`` — next to ``app.log``."""
    return paths.app_home() / LOG_DIR_NAME / f"{name}.log"


def _quoted(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


#: What the console is, stated at the top of every transcript. The architecture is asked
#: for by its assembly-qualified name (``…, mscorlib``): in an interactive window the bare
#: name resolves to PSReadLine 2.0.0's internal copy of the type, which has no
#: ``OSArchitecture`` — the cause of the Codex installer failure (see
#: ``codex_cli.isolated``) — so the PSReadLine version is reported alongside.
_FACTS = (
    "Write-Host ('PowerShell ' + $PSVersionTable.PSVersion + '; 64-bit process: ' + "
    "[Environment]::Is64BitProcess + '; ' + [Environment]::OSVersion.VersionString + "
    "'; .NET ' + [Environment]::Version) -ForegroundColor DarkGray; "
    "& { try { "
    "$a = [System.Runtime.InteropServices.RuntimeInformation, mscorlib]::OSArchitecture } "
    "catch { $a = 'unknown - ' + $_.Exception.Message }; "
    "$r = Get-Module PSReadLine; "
    "Write-Host ('OS architecture: ' + $a + '; PSReadLine loaded: ' + "
    "$(if ($r) { [string]$r.Version } else { 'no' })) -ForegroundColor DarkGray }; "
)


def transcribed(script: str, name: str) -> str:
    """``script``, recorded to ``logs/<name>.log`` from its first line to its last."""
    path = log_path(name)
    start = (
        f"New-Item -ItemType Directory -Force -Path {_quoted(path.parent)} | Out-Null; "
        f"Start-Transcript -Path {_quoted(path)} -Append | Out-Null; "
        f"Write-Host ('This window is recorded to ' + {_quoted(path)}) -ForegroundColor DarkGray; "
    )
    return f"{start}{_FACTS}{script}; Stop-Transcript | Out-Null"
