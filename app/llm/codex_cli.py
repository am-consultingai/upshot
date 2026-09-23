"""Summarize through the OpenAI Codex CLI, on the user's own ChatGPT plan.

The same shape as ``claude_cli.py`` (D30, D46), and for the same reason: **the application
never holds a credential.** It spawns the ``codex`` the machine's owner installed and
signed into themselves, pipes the prompt to stdin, and reads the answer back from a file
the CLI writes. The sign-in lives in the CLI's own store under ``CODEX_HOME``; nothing
here reads, writes, copies or passes it along.

What is different from the Claude provider, and why (D58):

* **Permission is only half published.** OpenAI documents ``codex exec`` as the way to
  drive Codex from scripts, but has said nothing about a third-party application driving
  it on someone's ChatGPT plan. That is an unanswered question, not a refusal, so this is
  built exactly as carefully as the Claude path, is never the default, and is cheap to
  remove: delete ``codex-subscription`` from ``LLM_PROVIDERS`` in ``app/config.py`` and
  anyone configured onto it falls back to the default with a message.
* **The schema is enforced by the CLI** (``--output-schema``), which the Claude CLI
  cannot do. It is still validated here and still goes through the repair loop: a
  build or a model that ignores the schema must cost a retry, not a meeting.
* **Limits are the product problem.** Plan usage runs on a 5-hour window plus a weekly
  cap shared with everything else the account does in Codex. Hitting it is recognised
  specifically (:class:`~app.errors.QuotaExhausted`) so the job waits for the reset — or
  goes to the fallback provider the user chose — instead of failing the meeting.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app import paths
from app.clock import SystemClock
from app.config import Config
from app.errors import PermanentError, QuotaExhausted, RecoverableError, SignInRequired
from app.llm.claude_cli import (
    CliStatus,
    InstallPlan,
    Runner,
    creation_flags,
    powershell_language_mode,
    winget_works,
)
from app.llm.client import LlmResult
from app.llm.console_log import transcribed
from app.llm.repair import complete_with_repair, schema_instruction
from app.llm.schema import FREE_SCHEMA
from app.llm.tokens import estimate_tokens
from app.log import get

log = get(__name__)

NAME = "codex-subscription"
DEFAULT_EXECUTABLE = "codex"
NPM_PACKAGE = "@openai/codex"
#: The README of the npm package and OpenAI's Codex docs both point here.
INSTALL_DOCS_URL = "https://developers.openai.com/codex/cli"
#: OpenAI's own Windows installer. ``CODEX_NON_INTERACTIVE=1`` is how Codex's own updater
#: runs it: without it the script ends by asking "Start Codex now?" and, answered yes,
#: opens the interactive agent in the window we meant for signing in.
NATIVE_INSTALL = "$env:CODEX_NON_INTERACTIVE=1; irm https://chatgpt.com/codex/install.ps1 | iex"
NPM_INSTALL = f"npm install -g {NPM_PACKAGE}"
#: Published by "OpenAI, Inc." in microsoft/winget-pkgs (0.156.1 on 2026-09-23) though not
#: in OpenAI's docs: a portable zip with a ``codex`` alias. Not ``OpenAI.Codex_…``, the
#: Appx id of the desktop app.
WINGET_PACKAGE = "OpenAI.Codex"
WINGET_INSTALL = (
    f"winget install --id {WINGET_PACKAGE} --exact "
    "--accept-source-agreements --accept-package-agreements"
)

#: Every variable through which the child could be handed a credential other than the
#: sign-in its owner made. With ``OPENAI_API_KEY`` (or its Codex-specific twin) set,
#: ``codex exec`` can bill the API instead of the plan — silently, which is the opposite
#: of the point. A different base URL would send the transcript somewhere else entirely.
STRIPPED_ENV = frozenset(
    {"OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "OPENAI_BASE_URL"}
)

# Matched against stderr and stdout together, lower-cased, with the typographic
# apostrophe the CLI uses ("You’ve hit your usage limit") folded to a plain one.
#: The plan's allowance: "You've hit your usage limit. … Try again at Sep 23, 2026 4:15 PM."
#: Also the plan-specific variants in codex-rs/protocol/src/error.rs: "…usage limit for
#: <model>", a workspace "out of credits", a "spend cap".
QUOTA_SPENT = (
    "hit your usage limit",
    "usage_limit_reached",
    "usage limit reached",
    "credits_depleted",
    "out of credits",
    "hit your spend cap",
)
#: Signed out, or a sign-in that has expired and can no longer refresh itself.
NOT_SIGNED_IN = (
    "not logged in",
    "codex login",
    "please log in",
    "401 unauthorized",
    "refresh token",
    "token_expired",
    "not signed in",
)
#: A transient limit or a dropped stream: the ordinary backoff is the right answer.
TRANSIENT = (
    "rate limit",
    "429",
    "too many requests",
    "stream disconnected",
    "exceeded retry limit",
    "overloaded",
    "503",
    "timed out",
)
#: The model or the service declined the content. Retrying the same transcript cannot help.
REFUSED = ("content policy", "usage policies", "flagged", "safety system")
#: clap's words for an option this build does not have. Retrying cannot help either.
NEEDS_UPDATE = (
    "unexpected argument",
    "unrecognized subcommand",
    "unknown option",
    "invalid value",
    "found argument",
)

_TRY_AGAIN = re.compile(
    r"try again at (?:((?:[A-Za-z]{3,9} \d{1,2}(?:st|nd|rd|th)?,? \d{4},? )?\d{1,2}:\d{2}\s?[AP]M)"
    r"|([^\n]+?)\.?\s*$)",
    re.IGNORECASE | re.MULTILINE,
)
#: The CLI prints the reset in local time: the time alone on the same day ("5:47 AM"),
#: else ``%b %-d<ordinal>, %Y %-I:%M %p`` ("Sep 24th, 2026 5:47 AM") in current source,
#: and "Aug 20, 2026, 7:38 AM" has been seen from a released build. The ordinal and the
#: comma after the year are removed before parsing, so every one of them lands here.
_RESET_FORMATS = ("%b %d %Y %I:%M %p", "%B %d %Y %I:%M %p", "%I:%M %p")
_ORDINAL = re.compile(r"(\d)(?:st|nd|rd|th)\b")


def reset_time(text: str, now: datetime | None = None) -> tuple[datetime | None, str]:
    """When the CLI says the allowance comes back: (the time, the words it used).

    Parsed where the words are one of the known formats; kept verbatim where they are
    not, so the message can still say *when* even if the queue cannot schedule on it.
    """
    match = _TRY_AGAIN.search(text)
    if not match:
        return None, ""
    words = (match.group(1) or match.group(2)).strip().rstrip(".")
    current = now or SystemClock().now()
    normal = " ".join(_ORDINAL.sub(r"\1", words).replace(",", " ").split())
    for fmt in _RESET_FORMATS:
        try:
            parsed = datetime.strptime(normal, fmt)
        except ValueError:
            continue
        if fmt == "%I:%M %p":
            parsed = datetime.combine(current.date(), parsed.time())
            when = parsed.replace(tzinfo=current.tzinfo)
            return (when if when > current else when + timedelta(days=1)), words
        return parsed.replace(tzinfo=current.tzinfo), words
    return None, words


def quota_exhausted(text: str, now: datetime | None = None) -> QuotaExhausted:
    """Plain words, not the CLI's: what ran out, whose it is, and when it comes back."""
    retry_at, words = reset_time(text, now)
    when = f"; it resets at {words}" if words else ""
    return QuotaExhausted(
        f"Your ChatGPT plan's Codex allowance is used up{when}. The summary will be written "
        "when it resets, or by the fallback provider if one is set in Settings.",
        provider=NAME,
        retry_at=retry_at,
    )


# --------------------------------------------------------------------------- install


def install_candidates() -> list[Path]:
    r"""Where the documented installers leave the binary, for a PATH read before them.

    The tray application starts at logon and runs for days, so a Codex installed
    afterwards lands on a ``PATH`` this process never sees — the same trap the Claude
    row fell into. OpenAI's installer links ``%LOCALAPPDATA%\Programs\OpenAI\Codex\bin``
    (``install.ps1``, read 2026-09-23); npm leaves a ``codex.cmd`` shim in
    ``%APPDATA%\npm``.
    """
    found: list[Path] = []
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        found.append(Path(local) / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe")
        winget = Path(local) / "Microsoft" / "WinGet"
        found.append(winget / "Links" / "codex.exe")
        # A portable winget install without the right to create symlinks leaves the
        # binary under Packages and nothing in Links — the trap D46 records for Claude.
        # The layout inside the package is not verified on a real machine (Codex 4).
        with contextlib.suppress(OSError):
            found.extend(sorted((winget / "Packages").glob(f"{WINGET_PACKAGE}_*/**/codex.exe")))
    roaming = os.environ.get("APPDATA", "")
    if roaming:
        found.append(Path(roaming) / "npm" / "codex.cmd")
    home = Path.home()
    found.append(home / ".local" / "bin" / "codex")  # install.sh, on Linux and macOS
    return found


def install_plan() -> InstallPlan | None:
    """What the Install button runs, or ``None`` when nothing here can install it.

    OpenAI's own installer wherever PowerShell can run it: it is what their README
    recommends, it puts the binary on the user's ``PATH`` and ``codex update`` keeps it
    current. Where PowerShell is in Constrained Language Mode ``Invoke-Expression`` does
    not exist, so the fallback is npm — a native tool rather than a script, and OpenAI's
    other documented route — when npm is present, and winget after that. winget comes
    last because OpenAI's docs do not mention its package, the CLI's own updater does not
    know it, and a portable install may land off ``PATH``.

    Never silent: the exact line is rendered beside the button before it is pressed, it
    is the vendor's own domain over HTTPS, and nothing is elevated.
    """
    if sys.platform != "win32":
        return None
    if powershell_language_mode() != "ConstrainedLanguage":
        return InstallPlan(
            "native",
            NATIVE_INSTALL,
            install_console(
                isolated(NATIVE_INSTALL), shown=NATIVE_INSTALL, fallback=_NATIVE_FALLBACK
            ),
        )
    if shutil.which("npm") is not None:
        # `npm.cmd`, not `npm`: under Constrained Language Mode PowerShell would resolve
        # `npm` to the npm.ps1 shim, a script, which is the thing that mode restricts.
        return InstallPlan("npm", NPM_INSTALL, install_console(f"npm.cmd install -g {NPM_PACKAGE}"))
    if winget_works():
        return InstallPlan("winget", WINGET_INSTALL, install_console(WINGET_INSTALL))
    return None


#: The installer edits the user's PATH, which this already-running PowerShell cannot see
#: until it is re-read; the known locations cover a PATH that has not caught up at all.
_FIND_CODEX = (
    "$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' "
    "+ [Environment]::GetEnvironmentVariable('Path','User'); "
    "$c = (Get-Command codex -ErrorAction SilentlyContinue).Source; "
    r"if (-not $c) { $c = @((Join-Path $env:LOCALAPPDATA 'Programs\OpenAI\Codex\bin\codex.exe'), "
    r"(Join-Path $env:APPDATA 'npm\codex.cmd'), "
    r"(Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\codex.exe')) "
    "| Where-Object { Test-Path $_ } | Select-Object -First 1 }"
)

#: What to expect at the hand-over. The browser flow is OpenAI's own.
_LOGIN_GUIDANCE = (
    "Write-Host ''; "
    "Write-Host ('A browser will open. Sign in with your ChatGPT account there; ' + "
    "'this window says when it is done and can then be closed.') -ForegroundColor DarkGray; "
    "Write-Host ''; "
)


def isolated(command: str) -> str:
    r"""Run ``command`` in a non-interactive PowerShell of its own, inside this console.

    **Why this exists.** The install window is an *interactive* console — it has to stay
    open for the sign-in that follows — and an interactive Windows PowerShell 5.1 loads
    PSReadLine. The PSReadLine that ships inside Windows 10 and 11 (2.0.0) carries its own
    internal copy of ``System.Runtime.InteropServices.RuntimeInformation`` with only
    ``OSDescription``, and once it is loaded that copy is what the type name resolves to.
    OpenAI's ``install.ps1`` asks ``RuntimeInformation::OSArchitecture`` under
    ``Set-StrictMode -Version Latest``, so on a stock Windows machine it died with
    ``The property 'OSArchitecture' cannot be found on this object`` (found 2026-09-23 by
    logging the type's assembly from inside the failing window: it was
    ``PSReadLine\2.0.0\Microsoft.PowerShell.PSReadLine.dll``). A non-interactive
    PowerShell never loads PSReadLine, and there the same line answers ``X64``.

    The child writes to the same window, so the user still watches it; its output is
    piped through ``Write-Host`` so the transcript (console_log.py) records it too. Piping
    the *outer* side does not change how the installer treats its own native commands'
    stderr, which is the thing ``claude_cli._console`` warns about. The command travels
    ``-EncodedCommand`` (UTF-16LE, base64): no quoting to go wrong on the way. A non-zero
    exit becomes an exception, so ``install_console``'s fallback still takes over.
    """
    encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
    return (
        "& (Join-Path $env:SystemRoot 'System32\\WindowsPowerShell\\v1.0\\powershell.exe') "
        f"-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand {encoded} 2>&1 "
        "| ForEach-Object { Write-Host $_ }; "
        "if ($LASTEXITCODE -ne 0) { throw ('The installer exited with code ' + $LASTEXITCODE) }"
    )


#: What to try when OpenAI's own installer fails, in the same window: npm, then winget —
#: each detected at run time on whatever machine this is, and only used if present.
#: (Seen in development on 2026-09-23: the installer stopped with ``The property
#: 'OSArchitecture' cannot be found on this object`` although the same line ran cleanly in
#: every other PowerShell tried.) A failed first route is not a reason to stop, and npm
#: and winget are both routes OpenAI publishes a package for.
_NATIVE_FALLBACK = (
    "if (Get-Command npm.cmd -ErrorAction SilentlyContinue) { "
    f"Write-Host 'Trying npm instead: {NPM_INSTALL}' -ForegroundColor Cyan; "
    f"npm.cmd install -g {NPM_PACKAGE} "
    "} elseif (Get-Command winget -ErrorAction SilentlyContinue) { "
    f"Write-Host 'Trying winget instead: {WINGET_INSTALL}' -ForegroundColor Cyan; "
    f"{WINGET_INSTALL} "
    "} else { Write-Host 'Neither npm nor winget is available to try instead.' "
    "-ForegroundColor Yellow }"
)


def install_console(
    command: str, *, shown: str | None = None, fallback: str | None = None
) -> list[str]:
    """Install, then sign in, in one visible window — recorded to ``logs/codex-install.log``.

    The installer runs in the foreground, for the reason ``claude_cli._console`` records
    at length: OpenAI's ``install.ps1`` also sets ``$ErrorActionPreference = 'Stop'``, so
    capturing its streams would turn the first line a native step writes to stderr into a
    terminating error. Only single quotes inside the script, for the same reason as there.

    It runs in a child scope (``& { … }``), so the installer's ``Set-StrictMode`` and
    ``$ErrorActionPreference`` stay inside it instead of leaking into the rest of this
    script — ``iex`` otherwise evaluates in the caller's scope. A failure is written out
    in full (message, error record, script stack) so the transcript holds the cause, and
    ``fallback``, when given, is tried next.
    """
    if fallback is None:
        run = f"{command}; "
    else:
        run = (
            f"try {{ & {{ {command} }} }} catch {{ "
            "Write-Host ('The installer failed: ' + $_.Exception.Message) -ForegroundColor Yellow; "
            "Write-Host ($_ | Out-String); Write-Host $_.ScriptStackTrace; "
            f"{fallback} }}; "
        )
    script = (
        f"Write-Host 'Running: {shown or command}' -ForegroundColor Cyan; "
        f"{run}"
        f"{_FIND_CODEX}; "
        "if ($c) { Write-Host ''; Write-Host 'Now signing in...' -ForegroundColor Cyan; "
        f"{_LOGIN_GUIDANCE}"
        "& $c login } else { Write-Host 'Codex was not found after installing. Open "
        "Settings and use Sign in.' -ForegroundColor Yellow }"
    )
    return [
        "powershell.exe",
        "-NoProfile",
        "-NoExit",
        "-Command",
        transcribed(script, "codex-install"),
    ]


def login_console(argv: Sequence[str]) -> list[str]:
    """Sign in, in a console of its own. Each argument quoted for PowerShell."""
    quoted = " ".join("'" + part.replace("'", "''") + "'" for part in argv)
    script = (
        "Write-Host 'Signing in to Codex with your ChatGPT account.' -ForegroundColor Cyan; "
        f"{_LOGIN_GUIDANCE}"
        f"& {quoted}"
    )
    return [
        "powershell.exe",
        "-NoProfile",
        "-NoExit",
        "-Command",
        transcribed(script, "codex-signin"),
    ]


def update_command(path: str) -> str:
    """A copy-pasteable update line, pinned to the binary this application resolved.

    An npm install is updated by npm; ``codex update`` would otherwise reinstall through
    whichever channel it believes it came from. Anything else is the binary's own
    ``update``, run by path so it is the install the application uses that moves.
    """
    lowered = path.lower().replace("\\", "/")
    if "/npm/" in lowered or "node_modules" in lowered:
        return f"npm install -g {NPM_PACKAGE}@latest"
    if "/winget/" in lowered:
        return f"winget upgrade --id {WINGET_PACKAGE} --exact"
    return f'"{path}" update'


# --------------------------------------------------------------------------- spawning


def workdir() -> str:
    r"""Where the CLI is spawned: a folder of ours, never a project or the recordings.

    Codex treats its working directory as the workspace it may read. Summarizing a
    transcript needs no workspace at all, so it gets an empty one this application owns
    — on local disk, because a ``\\wsl.localhost`` path is where the Claude CLI's file
    watching broke (``claude_cli.workdir``) and there is no reason to find out whether
    this one breaks the same way.
    """
    candidate = paths.app_home() / "codex-cli"
    if not str(candidate).startswith("\\\\"):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return str(candidate)
        except OSError:
            pass
    return tempfile.gettempdir()


def child_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in STRIPPED_ENV}


def subprocess_runner(args: Sequence[str], stdin: str, timeout: float) -> tuple[int, str, str]:
    completed = subprocess.run(
        list(args),
        input=stdin,
        capture_output=True,
        text=True,
        # UTF-8 explicitly: a Hebrew transcript cannot be encoded to cp1252 at all.
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=child_env(),
        cwd=workdir(),
        creationflags=creation_flags(visible=False),
        check=False,
    )
    return completed.returncode, completed.stdout, completed.stderr


# --------------------------------------------------------------------------- schema


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """The schema as OpenAI's strict structured output accepts it.

    Strict mode wants every property listed in ``required`` and ``additionalProperties``
    false, and rejects some keywords outright (``minLength``). Our schemas have optional
    properties, so each optional one becomes required-but-nullable here, and
    :func:`drop_nulls` removes the nulls again before the answer is validated against the
    real schema. Without this the request is refused before the model sees it.
    """
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key in ("minLength", "maxLength"):
            continue
        if key == "properties" and isinstance(value, dict):
            required = set(schema.get("required", []))
            props: dict[str, Any] = {}
            for name, spec in value.items():
                converted = strict_schema(spec) if isinstance(spec, dict) else spec
                if name not in required and isinstance(converted, dict):
                    converted = _nullable(converted)
                props[name] = converted
            out["properties"] = props
            out["required"] = list(value)
            out["additionalProperties"] = False
        elif key == "items" and isinstance(value, dict):
            out["items"] = strict_schema(value)
        elif key != "required":
            out[key] = value
    return out


def _nullable(spec: dict[str, Any]) -> dict[str, Any]:
    kind = spec.get("type")
    if isinstance(kind, list):
        return spec if "null" in kind else {**spec, "type": [*kind, "null"]}
    if isinstance(kind, str):
        return {**spec, "type": [kind, "null"]}
    return spec


def drop_nulls(data: Any, schema: dict[str, Any]) -> Any:
    """Undo :func:`strict_schema`: a null where the real schema has an optional key."""
    if isinstance(data, dict) and isinstance(schema.get("properties"), dict):
        required = set(schema.get("required", []))
        out: dict[str, Any] = {}
        for name, value in data.items():
            spec = schema["properties"].get(name)
            if value is None and name not in required and not _allows_null(spec):
                continue
            out[name] = drop_nulls(value, spec) if isinstance(spec, dict) else value
        return out
    if isinstance(data, list) and isinstance(schema.get("items"), dict):
        return [drop_nulls(item, schema["items"]) for item in data]
    return data


def _allows_null(spec: Any) -> bool:
    if not isinstance(spec, dict):
        return False
    kind = spec.get("type")
    return kind == "null" or (isinstance(kind, list) and "null" in kind)


# --------------------------------------------------------------------------- client


def _lower(text: str) -> str:
    return text.lower().replace("’", "'")


@dataclass
class _Scratch:
    schema_file: Path
    answer_file: Path


class CodexCliClient:
    name = NAME

    def __init__(
        self,
        config: Config,
        *,
        runner: Runner | None = None,
        max_repairs: int = 2,
    ) -> None:
        self.config = config
        self.executable = str(config.get("llm.codex_cli_path", DEFAULT_EXECUTABLE))
        self.timeout = float(config.get("llm.codex_cli_timeout_s", 600))
        self.max_repairs = max_repairs
        self.runner: Runner = runner or subprocess_runner
        self.calls: list[dict[str, Any]] = []

    # -- availability ------------------------------------------------------

    def resolve(self) -> str | None:
        """The binary this application will spawn. A real executable beats a shim."""
        found: list[str] = []
        on_path = shutil.which(self.executable)
        if on_path:
            found.append(on_path)
        if os.path.exists(self.executable):
            found.append(self.executable)
        if self.executable == DEFAULT_EXECUTABLE:
            found.extend(str(c) for c in install_candidates() if c.exists())
        if not found:
            return None
        return max(found, key=lambda candidate: candidate.lower().endswith(".exe"))

    def status(self) -> CliStatus:
        """Installed, which version, signed in as what. Spends nothing."""
        path = self.resolve()
        if path is None:
            return CliStatus(False, detail=f"{self.executable!r} is not on PATH")
        try:
            code, out, err = self.runner([path, "--version"], "", 30.0)
        except Exception as exc:
            return CliStatus(False, path=path, detail=str(exc))
        if code != 0:
            return CliStatus(False, path=path, detail=(err or out).strip()[:200])
        # The version line is stdout's last line: a warning about PATH helpers can
        # precede it on stderr, and on some builds on stdout.
        lines = [line for line in out.splitlines() if line.strip()]
        version = lines[-1].strip() if lines else ""
        signed_in, account = self.account_status(path)
        return CliStatus(True, version=version, path=path, signed_in=signed_in, account=account)

    def account_status(self, path: str) -> tuple[bool | None, str]:
        """Is the CLI signed in, and how? Read from ``codex login status``, never from disk.

        It has no JSON form (0.156.1, 2026-09-23), so its sentences are matched: "Logged in
        using ChatGPT", "Logged in using an API key - …", "Not logged in" (exit 1). A
        build that answers with none of them is **unknown**, never signed out — the
        Claude row learned that nagging a signed-in user to sign in is worse than
        saying nothing.

        Only the method is kept. The API-key line goes on to print part of the key; that
        part is never read past the dash, let alone stored or shown.
        """
        try:
            _code, out, err = self.runner([path, "login", "status"], "", 30.0)
        except Exception:
            return None, ""
        text = _lower(f"{out}\n{err}")
        if "not logged in" in text:
            return False, ""
        if "logged in using chatgpt" in text:
            return True, "ChatGPT account"
        if "logged in using an api key" in text:
            # Signed in — but to the API, so summaries would bill API credit rather than
            # the plan. Worth saying on the row; not a reason to call it signed out.
            return True, "an API key, billed to the API rather than your plan"
        if "logged in using" in text:
            return True, ""
        return None, ""

    def login_command(self) -> list[str]:
        """What the user runs to sign in. We launch it; OpenAI's browser flow owns it."""
        return [self.resolve() or self.executable, "login"]

    # -- protocol ----------------------------------------------------------

    def build_args(self, path: str, scratch: _Scratch) -> list[str]:
        """``codex exec``, made into a pure text task that persists nothing.

        * ``-`` — the whole prompt from stdin, so a long window never approaches the
          command-line limit and nothing multi-line crosses ``cmd.exe`` quoting when the
          binary resolved is npm's ``codex.cmd`` shim.
        * ``--sandbox read-only`` — the agent could still decide to run a command; this
          makes sure it cannot change anything if it does.
        * ``--skip-git-repo-check``, ``--cd`` — our own empty folder, not a repository.
        * ``--ephemeral`` — no session files written to the user's Codex history.
        * ``--ignore-user-config`` — the user's ``config.toml`` (MCP servers, a different
          model provider, a profile) has no business in a meeting summary. Sign-in is
          still read from ``CODEX_HOME`` by the CLI; this flag does not change that.
        * ``--output-schema`` and ``--output-last-message`` — the answer, shaped, in a
          file of ours, instead of scraped from a stream meant for people.
        """
        extra = [str(arg) for arg in self.config.get("llm.codex_cli_args", []) or []]
        return [
            path,
            "exec",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--ephemeral",
            "--ignore-user-config",
            "--color",
            "never",
            "--cd",
            workdir(),
            "--output-schema",
            str(scratch.schema_file),
            "--output-last-message",
            str(scratch.answer_file),
            *extra,
            "-",
        ]

    def complete_json(
        self,
        *,
        system_blocks: Sequence[dict[str, Any]],
        user: str,
        schema: dict[str, Any] = FREE_SCHEMA,
        max_tokens: int = 16000,
    ) -> LlmResult:
        path = self.resolve()
        if path is None:
            raise PermanentError(
                "Codex is not installed. Install it from Settings and sign in with your "
                "ChatGPT account, or choose another summarizer.",
                category="setup",
            )
        system_text = "\n\n".join(str(block.get("text", "")) for block in system_blocks)
        instruction = f"{system_text}\n\n{schema_instruction(schema)}"

        # Our folder, removed afterwards whatever happens: the schema we hand the CLI and
        # the answer it hands back. Nothing lands next to the recordings.
        with tempfile.TemporaryDirectory(prefix="run-", dir=workdir()) as folder:
            scratch = _Scratch(Path(folder) / "schema.json", Path(folder) / "answer.json")
            scratch.schema_file.write_text(json.dumps(strict_schema(schema)), encoding="utf-8")

            def send(message: str) -> str:
                args = self.build_args(path, scratch)
                # No system-prompt flag exists for `exec`, so the instruction leads the
                # prompt and the transcript window follows it.
                prompt = f"{instruction}\n\n{message}"
                self.calls.append({"args": args, "stdin": prompt})
                with contextlib.suppress(OSError):
                    scratch.answer_file.unlink()
                try:
                    code, out, err = self.runner(args, prompt, self.timeout)
                except subprocess.TimeoutExpired as exc:
                    raise RecoverableError(f"Codex timed out after {self.timeout:g}s") from exc
                except FileNotFoundError as exc:
                    raise PermanentError("Codex is not installed", category="setup") from exc
                answer = self._answer(scratch.answer_file, out)
                if code != 0 or not answer:
                    self._raise_for(code, out, err, path)
                return self._relax(answer, schema)

            data, attempts = complete_with_repair(
                send, user, schema, max_repairs=self.max_repairs, provider=self.name
            )
        return LlmResult(data=data, model=f"codex:{self.name}", attempts=attempts)

    @staticmethod
    def _answer(answer_file: Path, stdout: str) -> str:
        """The final message: from our file, else stdout, where ``exec`` also prints it."""
        with contextlib.suppress(OSError):
            text = answer_file.read_text(encoding="utf-8").strip()
            if text:
                return text
        return stdout.strip()

    @staticmethod
    def _relax(answer: str, schema: dict[str, Any]) -> str:
        """Strip the nulls strict mode forced in; leave anything unparseable to repair."""
        try:
            data = json.loads(answer)
        except ValueError:
            return answer
        return json.dumps(drop_nulls(data, schema), ensure_ascii=False)

    @staticmethod
    def _raise_for(code: int, out: str, err: str, path: str = DEFAULT_EXECUTABLE) -> None:
        text = f"{err}\n{out}".strip()
        lowered = _lower(text)
        # Quota first: the limit message names `codex login` nowhere, but it does say
        # "limit", and must not be mistaken for a transient rate limit.
        if any(marker in lowered for marker in QUOTA_SPENT):
            raise quota_exhausted(text)
        if any(marker in lowered for marker in NEEDS_UPDATE):
            raise PermanentError(
                "This Codex build does not understand the options this app sends. "
                f"Update it with: {update_command(path)}",
                category="setup",
            )
        if any(marker in lowered for marker in NOT_SIGNED_IN):
            raise SignInRequired(
                "Codex is not signed in. Open Settings, AI agents, and press Sign in; the "
                "summary will be written once you have.",
                provider=NAME,
            )
        if any(marker in lowered for marker in REFUSED):
            raise PermanentError(
                f"Codex declined to summarize this meeting: {text[:200]}", category="refusal"
            )
        if any(marker in lowered for marker in TRANSIENT):
            raise RecoverableError(f"Codex is rate limited or unreachable: {text[:200]}")
        if code == 0:
            raise RecoverableError("Codex returned nothing")
        raise RecoverableError(f"Codex exited {code}: {text[:300]}")

    def count_tokens(self, text: str) -> int:
        """No token endpoint behind a CLI: the Claude provider's estimate, so windows here
        are approximate — sized to err small, never over-full."""
        return estimate_tokens(text)
