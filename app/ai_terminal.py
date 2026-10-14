"""Settings → AI apps: which AI apps are on this computer, and a terminal with each one's
own command to add Upshot typed in (ClickUp epic z8tj1hjg3v).

Upshot edits no other app's files (D98). The user presses Enter, and the app's own CLI
(``claude mcp add``, ``codex mcp add``, ``agy mcp add``) writes the app's own file. The
ChatGPT desktop app reads the Codex file, so ``codex mcp add`` reaches it too.

An app can be on Windows and inside WSL, each with its own configuration (seen on the
development machine: ``claude`` and ``agy`` on both), so each side is found and opened on
its own.

Research: ``internal/research/2026-10-14-openai-desktop-mcp.md`` §4.2–4.3 and §10.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.log import get

log = get(__name__)

#: The apps added by a command of their own, and that command's program.
PROGRAMS = {"claude_code": "claude", "codex": "codex", "antigravity": "agy"}

#: The window's title, so the user can tell it from their own terminals.
TITLE = "Upshot"

#: Docker Desktop's own distributions hold no AI app of the user's.
_SKIP_DISTROS = ("docker-desktop",)

#: How long one WSL distribution may take to answer. A running one answered in 0.16 s on
#: the development machine; a profile that waits for input must not hold the page.
_WSL_TIMEOUT_S = 5.0

_CREATE_NO_WINDOW = 0x08000000
_CREATE_NEW_CONSOLE = 0x00000010


@dataclass
class Found:
    """Where each app is: its program on Windows, and the WSL distribution that has it."""

    windows: dict[str, str] = field(default_factory=dict)
    wsl: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {app: {"windows": app in self.windows, "wsl": self.wsl.get(app)} for app in PROGRAMS}


#: What the e2e server reports: no real lookup, and one of each case the page draws.
TEST_FOUND = Found(
    windows={"claude_code": "claude.exe", "codex": "codex.exe", "antigravity": "agy.exe"},
    wsl={"antigravity": "Ubuntu"},
)


def registry_path() -> str | None:
    r"""The PATH a terminal opened now would get: the machine's and the user's, read again.

    Upshot starts at logon and runs for days, so an app installed since then is on a PATH
    this process never saw (D46). The ChatGPT desktop app puts ``codex`` in
    ``%LOCALAPPDATA%\Programs\OpenAI\Codex\bin`` and adds that folder to the user's PATH.
    """
    if sys.platform != "win32":
        return None
    import winreg

    parts: list[str] = []
    for hive, key in (
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
        (winreg.HKEY_CURRENT_USER, "Environment"),
    ):
        try:
            with winreg.OpenKey(hive, key) as handle:
                value, _ = winreg.QueryValueEx(handle, "Path")
        except OSError:
            continue
        if isinstance(value, str) and value:
            parts.append(winreg.ExpandEnvironmentStrings(value))
    return ";".join(parts) or None


def find_on_windows(config: Any) -> dict[str, str]:
    """Each app's program on Windows: on the PATH read again, else where its installer
    leaves it (the CLI clients' own lookup, which also honours a configured path)."""
    if sys.platform != "win32":
        return {}
    from app.llm.antigravity_cli import AntigravityCliClient
    from app.llm.claude_cli import ClaudeCliClient
    from app.llm.codex_cli import CodexCliClient

    fresh = registry_path()
    clients: dict[str, Any] = {
        "claude_code": ClaudeCliClient,
        "codex": CodexCliClient,
        "antigravity": AntigravityCliClient,
    }
    found: dict[str, str] = {}
    for app, program in PROGRAMS.items():
        path = shutil.which(program, path=fresh) if fresh else None
        if path is None:
            try:
                path = clients[app](config).resolve()
            except Exception:  # a broken install reads as not installed
                path = None
        if path:
            found[app] = path
    return found


def running_distros() -> list[str]:
    """The WSL distributions running now. Only these are asked: asking a stopped one
    would start it, and opening Settings must never start WSL."""
    if sys.platform != "win32":
        return []
    try:
        out = subprocess.run(
            ["wsl.exe", "-l", "--running", "-q"],
            capture_output=True,
            timeout=_WSL_TIMEOUT_S,
            creationflags=_CREATE_NO_WINDOW,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_distros(out)


def parse_distros(raw: bytes) -> list[str]:
    """``wsl.exe -l`` writes UTF-16LE; one name a line, Docker Desktop's left out."""
    text = raw.decode("utf-16-le", errors="ignore").replace("\x00", "")
    names = [line.strip() for line in text.splitlines() if line.strip()]
    return [name for name in names if not name.startswith(_SKIP_DISTROS)]


def find_in_wsl(distros: list[str] | None = None) -> dict[str, str]:
    """Each app's distribution: the first running one whose shell finds its program. An
    interactive login shell, as the user's terminal would be, so a PATH set in
    ``.bashrc`` (nvm's npm, for an npm-installed Codex) counts."""
    found: dict[str, str] = {}
    for distro in running_distros() if distros is None else distros:
        try:
            out = subprocess.run(
                [
                    "wsl.exe",
                    "-d",
                    distro,
                    "-e",
                    "bash",
                    "-lic",
                    "command -v " + " ".join(PROGRAMS.values()),
                ],
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=_WSL_TIMEOUT_S,
                creationflags=_CREATE_NO_WINDOW,
                check=False,
            ).stdout.decode("utf-8", errors="replace")
        except (OSError, subprocess.SubprocessError) as exc:
            log.info("could not ask WSL %s for AI apps: %s", distro, exc)
            continue
        for app in apps_in(out):
            found.setdefault(app, distro)
    return found


#: A Windows drive inside WSL. WSL appends the Windows PATH to its own by default
#: (``appendWindowsPath``), and npm on Windows leaves an extensionless shim beside
#: ``codex.cmd``: found there, it is the Windows install, not one in WSL.
_WINDOWS_DRIVE = re.compile(r"^/mnt/[a-z]/", re.IGNORECASE)


def apps_in(command_v: str) -> list[str]:
    """The apps whose program ``command -v`` printed as a path inside WSL (an alias or a
    function prints something else, and a Windows drive is Windows's install)."""
    names = {program: app for app, program in PROGRAMS.items()}
    return [
        names[line.rsplit("/", 1)[-1]]
        for line in command_v.splitlines()
        if line.startswith("/")
        and not _WINDOWS_DRIVE.match(line)
        and line.rsplit("/", 1)[-1] in names
    ]


def find(config: Any, *, test: bool = False) -> Found:
    if test:
        return TEST_FOUND
    return Found(windows=find_on_windows(config), wsl=find_in_wsl())


def windows_line(command: str, program_path: str | None, fresh_path: str | None) -> str:
    """The line typed into PowerShell: the shown command, with its program as PowerShell
    will find it.

    - Found on the PATH as an ``.exe``: the command as shown.
    - An npm install (``codex.cmd`` beside ``codex.ps1``): PowerShell would pick the
      ``.ps1``, which a machine that blocks scripts refuses; the ``.cmd`` (or ``.bat``)
      always runs.
    - Not on the PATH at all (a winget install without its link): the full path.
    """
    program, _, rest = command.partition(" ")
    if program_path is None:
        return command
    on_path = shutil.which(program, path=fresh_path) if fresh_path else None
    if on_path is None:
        return f'& "{program_path}" {rest}'
    extension = os.path.splitext(on_path)[1].lower()
    if extension in (".cmd", ".bat"):
        return f"{program}{extension} {rest}"
    return command


#: What PowerShell takes as a single quote: ASCII's, and the typographic ones (a profile
#: folder named O’Brien). Each is doubled inside a single-quoted string.
_SINGLE_QUOTES = re.compile("(['\u2018\u2019\u201a\u201b])")


def powershell_argv(line: str) -> list[str]:
    """PowerShell with ``line`` at the prompt, not run: the user reads it and presses Enter.

    PSReadLine raises ``PowerShell.OnIdle`` while it waits for input; one handler inserts
    the line there, once. Tested 2026-10-14 on Windows PowerShell 5.1 with PSReadLine 2.0
    (machines A and B): the line appears coloured, cursor at the end, and Enter runs it.
    Where it cannot run (Constrained Language mode, no PSReadLine) the prompt is empty, and
    the page has copied the command already: the first line says to paste it.

    A plain ``-Command``, readable in the process list, not ``-EncodedCommand``: Defender
    has flagged encoded scripts of ours (known-issues K4). Defender stayed silent on this
    one on machine B.
    """
    quoted = _SINGLE_QUOTES.sub(r"\1\1", line)
    script = (
        f"$Host.UI.RawUI.WindowTitle = '{TITLE}'; "
        "Write-Host 'Press Enter to add Upshot. Empty line? Paste it with Ctrl+V.' "
        "-ForegroundColor DarkGray; "
        "Register-EngineEvent -SourceIdentifier PowerShell.OnIdle -MaxTriggerCount 1 "
        f"-Action {{ [Microsoft.PowerShell.PSConsoleReadLine]::Insert('{quoted}') }} | Out-Null"
    )
    return ["powershell.exe", "-NoExit", "-NoProfile", "-Command", script]


#: Bash shows the line ready to edit (``read -e -i``), runs it on Enter, then stays open.
#: ``$0`` is the window's title, ``$1`` the line.
_WSL_SCRIPT = (
    # The title first: the server finds the window by it, to bring it forward.
    "printf '\\033]0;%s\\007' \"$0\"; "
    "printf '\\033[2m%s\\033[0m\\n' 'Press Enter to add Upshot.'; "
    'read -e -i "$1" -p "$ " line && history -s "$line" && eval "$line"; '
    "exec bash -l"
)


def wsl_argv(line: str, distro: str | None) -> list[str]:
    """A WSL shell with ``line`` ready at its prompt. ``-e``, not ``--``: the arguments go
    to bash as they are, not through the user's shell a second time; the line is bash's
    ``$1``, so nothing in it is quoted twice. Tested 2026-10-14 on machine A: started from
    Windows Python with ``CREATE_NEW_CONSOLE``, the window showed a line with ``"`` and
    ``$WSL_DISTRO_NAME`` intact, unexpanded (Enter was not pressed there)."""
    where = ["-d", distro] if distro else []
    return ["wsl.exe", *where, "--cd", "~", "-e", "bash", "-lic", _WSL_SCRIPT, TITLE, line]


def launch(argv: list[str], fresh_path: str | None) -> None:
    """Open ``argv`` in a console of its own: Windows hands a new console to the user's
    default terminal (Windows Terminal, or the classic console). It starts in the user's
    folder, never Upshot's, with the PATH read again."""
    env = dict(os.environ)
    if fresh_path:
        env["PATH"] = fresh_path
    subprocess.Popen(argv, cwd=str(Path.home()), env=env, creationflags=_CREATE_NEW_CONSOLE)
