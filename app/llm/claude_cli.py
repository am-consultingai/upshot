"""Summarize through the Claude Code CLI, on the user's own subscription.

**The application never holds a credential.** It spawns the CLI the user signed into
themselves, and reads JSON from stdout. That is what keeps this inside Anthropic's rule
that third-party apps must not *"collect, store, or intermediate Claude.ai credentials or
session tokens"* — and it is the only reason this path is available at all.

Two consequences worth knowing:

* Anthropic does not permit third-party products to *offer* claude.ai login. This provider
  is for the machine's own owner using their own plan; it is not the shipped default.
* The CLI returns text, not a schema-enforced payload, so this goes through the shared
  validate-and-repair loop like every other non-Messages-API provider.
"""

from __future__ import annotations

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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from app import paths
from app.config import Config
from app.errors import PermanentError, QuotaExhausted, RecoverableError
from app.llm.client import LlmResult
from app.llm.console_log import transcribed
from app.llm.repair import complete_with_repair, schema_instruction
from app.llm.schema import FREE_SCHEMA
from app.llm.tokens import estimate_tokens
from app.log import get

log = get(__name__)

#: Removed from the model's context: this provider summarizes text and has no business
#: reading or writing the user's disk.
DISALLOWED_TOOLS = "Bash,Read,Write,Edit,NotebookEdit,Glob,Grep,WebSearch,WebFetch,Task,TodoWrite"

NOT_SIGNED_IN = ("not logged in", "please run /login", "invalid api key", "authentication")
RATE_LIMITED = ("rate limit", "usage limit", "quota", "429", "overloaded")
#: The plan's allowance, not a transient 429: "Claude AI usage limit reached|<epoch>" from
#: older builds, "5-hour limit reached ∙ resets 3pm" and "You've hit your limit · resets
#: 3pm" from newer ones. Checked before RATE_LIMITED, which would also match the first.
QUOTA_SPENT = (
    "usage limit reached",
    "limit reached ∙ resets",
    "limit reached · resets",
    "hit your limit",
    "weekly limit reached",
)
_EPOCH = re.compile(r"limit reached\|(\d{9,11})")
_RESETS = re.compile(r"resets ([^\n.]{1,40})", re.IGNORECASE)
#: A build too old for the arguments we send says so on stderr. Retrying cannot help.
NEEDS_UPDATE = ("unknown option", "unknown command", "error: unknown", "unknown argument")

DEFAULT_EXECUTABLE = "claude"
#: Anthropic's own winget package, from the documented install instructions.
WINGET_PACKAGE = "Anthropic.ClaudeCode"
INSTALL_DOCS_URL = "https://code.claude.com/docs/en/setup"

CREATE_NEW_CONSOLE = 0x00000010
CREATE_NO_WINDOW = 0x08000000


def quota_exhausted(text: str) -> QuotaExhausted:
    """The plan's allowance is spent: say so in plain words, and when it comes back.

    The raw CLI text is not what the user reads — "usage limit reached|1760000000" means
    nothing to someone who has never opened a terminal.
    """
    retry_at = None
    when = ""
    epoch = _EPOCH.search(text)
    if epoch:
        retry_at = datetime.fromtimestamp(int(epoch.group(1)), tz=UTC)
        when = f"; it resets at {retry_at.astimezone():%H:%M on %a %d %b}"
    else:
        resets = _RESETS.search(text)
        if resets:
            when = f"; it resets {resets.group(1).strip()}"
    return QuotaExhausted(
        f"Your Claude plan's usage allowance is used up{when}. The summary will be written "
        "when it resets, or by the fallback provider if one is set in Settings.",
        provider="claude-subscription",
        retry_at=retry_at,
    )


def creation_flags(*, visible: bool) -> int:
    """How a child process gets, or is denied, a console of its own. Windows only.

    The frozen build is built with ``console=False`` (``packaging/upshot.spec``)
    and so owns no console. Every console-subsystem child therefore either flashes up a
    window of its own or must be told not to:

    * probes and the summarizer run unattended and must stay invisible;
    * sign-in and install are interactive, have nowhere else to draw, and get a console
      deliberately.
    """
    if sys.platform != "win32":
        return 0
    return CREATE_NEW_CONSOLE if visible else CREATE_NO_WINDOW


def install_candidates() -> list[Path]:
    r"""Where the documented Windows installers leave the binary.

    ``shutil.which`` alone is not enough. The tray application starts at logon and then
    runs for days, so a Claude Code installed *afterwards* lands on a ``PATH`` this
    process will never see. Immediately after the Install button has visibly succeeded,
    a row still reading "not installed" looks like the application is broken — so the
    documented locations are checked directly.
    """
    home = Path.home()
    found = [
        home / ".local" / "bin" / "claude.exe",  # native installer, and it owns PATH
        home / ".local" / "bin" / "claude",
    ]
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        winget = Path(local) / "Microsoft" / "WinGet"
        found.append(winget / "Links" / "claude.exe")
        # winget installs this package *portable*: the binary lands under Packages, and
        # the symlink in Links — the directory that is actually on PATH — is only created
        # where the machine permits symlinks (Developer Mode, or an elevated install).
        # Without it the package is installed and reachable from nowhere, which is
        # exactly how a successful install came to read as "not installed".
        with contextlib.suppress(OSError):
            found.extend(sorted((winget / "Packages").glob("Anthropic.ClaudeCode*/claude.exe")))
    roaming = os.environ.get("APPDATA", "")
    if roaming:
        found.append(Path(roaming) / "npm" / "claude.cmd")
    return found


#: Cached because the settings page asks while it polls, and policy does not change
#: under a running application. Cheap (~0.3s) but not free at one spawn every few seconds.
_LANGUAGE_MODE: str | None = None


def powershell_language_mode() -> str:
    """``FullLanguage``, ``ConstrainedLanguage``, or ``""`` when PowerShell cannot answer.

    AppLocker and WDAC put PowerShell into Constrained Language Mode, where
    ``Invoke-Expression`` does not exist — so ``irm ... | iex`` cannot run there however
    healthy the machine otherwise looks, and the failure would happen inside a console
    nobody is reading. One spawn asks the very interpreter the installer would run in,
    which is a direct test rather than a guess about what policy is in force.
    """
    global _LANGUAGE_MODE
    if sys.platform != "win32":
        return ""
    if _LANGUAGE_MODE is not None:
        return _LANGUAGE_MODE
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "$ExecutionContext.SessionState.LanguageMode",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=creation_flags(visible=False),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    _LANGUAGE_MODE = completed.stdout.strip() if completed.returncode == 0 else ""
    return _LANGUAGE_MODE


def winget_works() -> bool:
    r"""Does winget actually run here, as opposed to merely appearing on ``PATH``?

    ``%LOCALAPPDATA%\Microsoft\WindowsApps\winget.exe`` is an app execution alias — a
    two-byte reparse point that ``shutil.which`` returns whether or not the App Installer
    package behind it is usable, and an alias whose package is not provisioned opens the
    Microsoft Store instead of running. Presence proves nothing; running it does.
    """
    if sys.platform != "win32" or shutil.which("winget") is None:
        return False
    try:
        completed = subprocess.run(
            ["winget", "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=creation_flags(visible=False),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0 and completed.stdout.strip().startswith("v")


@dataclass(frozen=True)
class InstallPlan:
    """How Claude Code gets installed here, and the exact line that does it."""

    method: str  # native | winget
    display: str  # the install line, rendered beside the button before it is clicked
    argv: list[str]  # what actually gets spawned


def install_plan() -> InstallPlan | None:
    r"""What the Install button runs, or ``None`` when nothing here can install it.

    Anthropic's own installer wherever it can run: it is what their documentation
    recommends, it puts the binary on ``PATH``, and it keeps itself updated afterwards.
    winget only where PowerShell is locked down — it survives Constrained Language Mode
    because it is a native executable rather than a script, but it installs this package
    *portable*, leaving the binary under ``WinGet\Packages`` with nothing on ``PATH``
    and no auto-update. Better than nothing, worse than the alternative, so it is the
    fallback and not the default.

    Piping a remote script into PowerShell is acceptable here only because it is never
    silent: the line is rendered beside the button, it is the vendor's own domain over
    HTTPS, the binary is Authenticode-signed, and nothing is elevated.
    """
    if sys.platform != "win32":
        return None
    if powershell_language_mode() != "ConstrainedLanguage":
        command = "irm https://claude.ai/install.ps1 | iex"
        return InstallPlan("native", command, _console(command, _QUIET_NOTE, spinner=True))
    if winget_works():
        command = (
            f"winget install --id {WINGET_PACKAGE} "
            "--accept-source-agreements --accept-package-agreements"
        )
        return InstallPlan("winget", command, _console(command))
    return None


#: Locate the binary the install just produced, from inside the same console. The
#: installer edits the *user's* PATH, which this already-running PowerShell cannot see
#: until it is re-read, and a winget portable install never lands on PATH at all.
_FIND_CLAUDE = (
    "$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' "
    "+ [Environment]::GetEnvironmentVariable('Path','User'); "
    "$c = (Get-Command claude -ErrorAction SilentlyContinue).Source; "
    r"if (-not $c) { $c = @((Join-Path $env:USERPROFILE '.local\bin\claude.exe'), "
    r"(Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\claude.exe')) "
    "+ @(Get-ChildItem (Join-Path $env:LOCALAPPDATA "
    r"'Microsoft\WinGet\Packages\Anthropic.ClaudeCode*\claude.exe') "
    "-ErrorAction SilentlyContinue | ForEach-Object { $_.FullName }) "
    "| Where-Object { Test-Path $_ } | Select-Object -First 1 }"
)


#: A spinner, in a process of its own, painting into the same console.
#:
#: It watches the folder the installer downloads into and reports megabytes as they
#: land. Crucially it never touches the installer: it is a sibling process, not a
#: wrapper, so the installer's streams stay attached to the real console — which is the
#: condition that has to hold (see ``_console``).
_SPINNER = (
    "$sp=$null; "
    "try { $sp = Start-Process powershell -NoNewWindow -PassThru -ArgumentList "
    "'-NoProfile','-NoLogo','-Command','"
    "$ErrorActionPreference=''SilentlyContinue'';$f=''.  '',''.. '',''...'',''   "
    "'';$i=0;$d=Join-Path $env:USERPROFILE "
    "''.claude\\downloads'';while($true){$g=Get-ChildItem $d -Filter *.exe | Sort-Object "
    "Length -Descending | Select-Object -First 1;$m=0; "
    "if($g){$m=[math]::Round($g.Length/1MB)};$t=''  working'' + $f[$i%4];if($m -gt "
    "0){$t=''  downloading '' + $m + '' MB of about 220'' + "
    "$f[$i%4]};[Console]::Write([char]13 + $t.PadRight(48));$i++;Start-Sleep "
    "-Milliseconds 200}"
    "' } catch { }; "
)


#: Stop it however the install ended, and wipe its line.
_SPINNER_STOP = (
    "finally { if ($sp) { Stop-Process -Id $sp.Id -Force -ErrorAction SilentlyContinue }; "
    "[Console]::Write([char]13 + (' ' * 48) + [char]13) } "
)

#: Anthropic's installer prints nothing until it has fetched a manifest, downloaded
#: ~220 MB and hashed it. The spinner covers the wait; this says how long it will be.
_QUIET_NOTE = (
    "Write-Host 'Downloading Claude Code (about 220 MB). This takes a minute.' "
    "-ForegroundColor DarkGray; "
)


#: What to expect at the hand-over. The login prompt is Claude Code's own — whether it
#: echoes a pasted code is its business and cannot be changed from here — so the useful
#: thing is to say so in advance rather than leave a paste looking like it missed.
_LOGIN_GUIDANCE = (
    "Write-Host ''; "
    # Joined with PowerShell's own '+', not by splitting a quoted string across Python
    # lines: a split inside the literal produces '', which PowerShell reads as an
    # escaped quote and prints back at the user.
    "Write-Host ('A browser will open. Copy the code it shows, paste it here ' + "
    "'with Ctrl+V or a right-click, then press Enter.') -ForegroundColor DarkGray; "
    "Write-Host ('A pasted code may not be echoed back - that prompt belongs to ' + "
    "'Claude Code. Press Enter even if nothing appears.') -ForegroundColor DarkGray; "
    "Write-Host ''; "
)


def login_console(argv: Sequence[str]) -> list[str]:
    """Sign in, in a console of its own, with a word about what is coming.

    Each argument is quoted for PowerShell rather than pasted in raw: the resolved path
    routinely contains spaces, and on this machine it lives under ``AppData\\Local``.
    """
    quoted = " ".join("'" + part.replace("'", "''") + "'" for part in argv)
    script = (
        "Write-Host 'Signing in to Claude Code.' -ForegroundColor Cyan; "
        f"{_LOGIN_GUIDANCE}"
        f"& {quoted}"
    )
    return [
        "powershell.exe",
        "-NoProfile",
        "-NoExit",
        "-Command",
        transcribed(script, "claude-signin"),
    ]


def _console(command: str, note: str = "", *, spinner: bool = False) -> list[str]:
    r"""Install and then sign in, in one visible window.

    **The installer runs in the foreground, and must.** A previous version ran it under
    ``Start-Job`` so the foreground could paint a progress bar. That broke the install: a
    job captures the child's streams, PowerShell turns a native command's stderr into
    error records once they are captured, and ``install.ps1`` sets
    ``$ErrorActionPreference = 'Stop'`` — so the first byte ``claude.exe install`` wrote
    to stderr became a *terminating* error. The download and checksum succeeded, the final
    step aborted, and it left a zero-byte stub in ``.local\share\claude\versions\`` with
    no launcher: an install that reported success and produced nothing.

    Activity is shown instead by a **sibling** process (``_SPINNER``) writing to the same
    console. It observes the download folder from outside and never handles the
    installer's output, so the condition that broke things cannot recur. Starting it is
    wrapped in ``try``/``catch`` and stopping it in ``finally``: a spinner that fails to
    start must not stop the install, and one that starts must not outlive it.

    The login runs in the same window afterwards, because installing alone would send the
    user back to the application to discover there is a second step.

    Only single quotes appear inside the script: it crosses a Windows command line as one
    argument, and embedded double quotes are where that goes wrong. No
    ``-ExecutionPolicy Bypass`` either — that governs script files, and this is
    ``-Command``.
    """
    run = f"try {{ {command} }} {_SPINNER_STOP}" if spinner else f"{command}; "
    script = (
        f"Write-Host 'Running: {command}' -ForegroundColor Cyan; "
        f"{note}"
        f"{_SPINNER if spinner else ''}"
        f"{run}"
        f"{_FIND_CLAUDE}; "
        "if ($c) { Write-Host ''; Write-Host 'Now signing in...' -ForegroundColor Cyan; "
        f"{_LOGIN_GUIDANCE}"
        "& $c auth login } else { Write-Host 'Claude Code was not found after "
        "installing. Open Settings and use Sign in.' -ForegroundColor Yellow }"
    )
    # Recorded, like every install and sign-in window: see app/llm/console_log.py.
    return [
        "powershell.exe",
        "-NoProfile",
        "-NoExit",
        "-Command",
        transcribed(script, "claude-install"),
    ]


def update_command(path: str) -> str:
    """A copy-pasteable update line, pinned to the binary this application resolved.

    Never a bare ``claude update``: with more than one install on the machine, which one
    that upgrades depends on the user's ``PATH``, and upgrading the one the application
    does *not* use looks exactly like the update having silently failed.
    """
    if "winget" in path.lower():
        return f"winget upgrade --id {WINGET_PACKAGE}"
    return f'"{path}" update'


def workdir() -> str:
    r"""Where the CLI is spawned. Never the directory the application runs from.

    Claude Code reads *and watches* the current directory's ``.claude/`` when it starts.
    Two things follow from that, and both have already broken this feature:

    * Started from a source tree on ``\\wsl.localhost\...`` — which is what the Windows
      launcher does, since the checkout lives in WSL — the watch fails with
      ``EISDIR: illegal operation on a directory`` and the CLI dies before showing a
      login prompt. Windows cannot watch a directory over that redirector.
    * In any repository holding a ``.claude/settings.json``, an interactive start stops
      to ask whether the folder is trusted. Nobody is at that console to answer.

    A neutral folder on local disk, owned by this application, has neither problem — and
    the session it starts there carries none of the user's project settings.
    """
    home = paths.app_home()
    candidate = home / "claude-cli"
    # A UNC app home would reintroduce exactly the watch failure this exists to avoid.
    if not str(candidate).startswith("\\\\"):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return str(candidate)
        except OSError:
            pass
    return tempfile.gettempdir()


def child_env() -> dict[str, str]:
    # The child must not inherit ANTHROPIC_API_KEY: with it set, Claude Code asks to use
    # the key instead of the subscription session, which is the opposite of the point.
    return {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}


class Runner(Protocol):
    """Runs the CLI. Injected in tests so no subprocess and no credit is spent."""

    def __call__(self, args: Sequence[str], stdin: str, timeout: float) -> tuple[int, str, str]: ...


def subprocess_runner(args: Sequence[str], stdin: str, timeout: float) -> tuple[int, str, str]:
    completed = subprocess.run(
        list(args),
        input=stdin,
        capture_output=True,
        text=True,
        # Explicit, and not negotiable: text mode otherwise defaults to the locale
        # encoding, which is cp1252 on Windows. A Hebrew transcript cannot be encoded
        # into the child's stdin at all, and the stage dies before it spawns anything.
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=child_env(),
        cwd=workdir(),
        creationflags=creation_flags(visible=False),
        check=False,
    )
    return completed.returncode, completed.stdout, completed.stderr


@dataclass(frozen=True)
class CliStatus:
    installed: bool
    version: str = ""
    path: str = ""
    detail: str = ""
    #: ``None`` when the installed build is too old to be asked.
    signed_in: bool | None = None
    #: Whatever the CLI is willing to say about the account, e.g. "you@example.com · max".
    account: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "installed": self.installed,
            "version": self.version,
            "path": self.path,
            "detail": self.detail,
            "signed_in": self.signed_in,
            "account": self.account,
        }


class ClaudeCliClient:
    name = "claude-subscription"

    def __init__(
        self,
        config: Config,
        *,
        runner: Runner | None = None,
        max_repairs: int = 2,
    ) -> None:
        self.config = config
        self.executable = str(config.get("llm.claude_cli_path", "claude"))
        self.timeout = float(config.get("llm.claude_cli_timeout_s", 600))
        self.max_repairs = max_repairs
        self.runner: Runner = runner or subprocess_runner
        self.calls: list[dict[str, Any]] = []

    # -- availability ------------------------------------------------------

    def resolve(self) -> str | None:
        """The binary this application will actually spawn.

        Prefers a real executable over a shim. An npm install leaves ``claude.cmd``, a
        batch file, and every argument then goes through ``cmd.exe`` quoting — with a
        multi-line instruction among those arguments. A configured path is honoured
        exactly as given and never second-guessed.
        """
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
        """Is the CLI installed, and which version? Cheap, and spends nothing."""
        path = self.resolve()
        if path is None:
            return CliStatus(False, detail=f"{self.executable!r} is not on PATH")
        try:
            code, out, err = self.runner([path, "--version"], "", 30.0)
        except Exception as exc:
            return CliStatus(False, path=path, detail=str(exc))
        if code != 0:
            return CliStatus(False, path=path, detail=(err or out).strip()[:200])
        signed_in, account = self.account_status(path)
        return CliStatus(True, version=out.strip(), path=path, signed_in=signed_in, account=account)

    def account_status(self, path: str) -> tuple[bool | None, str]:
        """Is the CLI signed in, and as whom? Spends nothing — no token is bought.

        ``auth status`` answers in JSON. Builds older than it print a usage error instead,
        which is reported as "unknown" rather than as "signed out": the difference matters,
        because signed-out is worth nagging about and unknown is not.
        """
        try:
            _code, out, _err = self.runner([path, "auth", "status", "--json"], "", 30.0)
        except Exception:
            return None, ""
        try:
            answer = json.loads(out.strip() or "{}")
        except ValueError:
            return None, ""
        if not isinstance(answer, dict) or "loggedIn" not in answer:
            return None, ""
        # Read only: the account it names, never anything that could authenticate as it.
        who = " · ".join(
            str(answer[key]) for key in ("email", "subscriptionType") if answer.get(key)
        )
        return bool(answer["loggedIn"]), who

    def login_command(self) -> list[str]:
        """What the user runs to sign in. We launch it; Anthropic's flow owns it.

        ``auth login`` is a command in its own right: it does not open a project session,
        so it neither asks about trusting the folder nor starts the file watchers that
        broke this on a WSL path. Older builds have no such command, and there the bare
        CLI still walks into the login when it starts unsigned.
        """
        path = self.resolve() or self.executable
        supports_auth = self.account_status(path)[0] is not None
        return [path, "auth", "login"] if supports_auth else [path]

    # -- protocol ----------------------------------------------------------

    def build_args(self, path: str, *, system: str = "") -> list[str]:
        extra = list(self.config.get("llm.claude_cli_args", []) or [])
        args = [
            path,
            "-p",
            "--output-format",
            "json",
            "--disallowed-tools",
            str(self.config.get("llm.claude_cli_disallowed_tools", DISALLOWED_TOOLS)),
        ]
        if system:
            # `--system-prompt` *replaces* Claude Code's own, which is the point. As a
            # user-turn argument the same text lost to a 6000-token transcript and to the
            # CLI's coding-assistant framing: it answered in prose, then in JSON of its
            # own invention (`key_points`, `next_steps`), and never matched the schema.
            # This is a text transformation, so it gets a system prompt that says only
            # that.
            args += ["--system-prompt", system]
        return [*args, *extra]

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
                "Claude Code is not installed. Install it, run `claude` once to sign in, "
                "or switch to an API key in Settings.",
                category="setup",
            )
        system_text = "\n\n".join(str(block.get("text", "")) for block in system_blocks)

        def send(message: str) -> str:
            # The instruction goes as the argument; the transcript window is piped in, so
            # a long window never approaches the command-line length limit.
            instruction = f"{system_text}\n\n{schema_instruction(schema)}"
            args = self.build_args(path, system=instruction)
            self.calls.append({"args": args, "instruction": instruction, "stdin": message})
            try:
                # The window is the whole prompt, piped: no positional argument competes
                # with it, and a long transcript never approaches the command-line limit.
                code, out, err = self.runner(args, message, self.timeout)
            except subprocess.TimeoutExpired as exc:
                raise RecoverableError(f"Claude Code timed out after {self.timeout:g}s") from exc
            except FileNotFoundError as exc:
                raise PermanentError("Claude Code is not installed", category="setup") from exc
            if code != 0:
                self._raise_for(code, out, err, path)
            return self._payload(out)

        data, attempts = complete_with_repair(
            send, user, schema, max_repairs=self.max_repairs, provider=self.name
        )
        return LlmResult(data=data, model=f"claude-code:{self.name}", attempts=attempts)

    @staticmethod
    def _raise_for(code: int, out: str, err: str, path: str = DEFAULT_EXECUTABLE) -> None:
        text = f"{err}\n{out}".strip()
        lowered = text.lower()
        if any(marker in lowered for marker in NEEDS_UPDATE):
            # Retrying an argument the build does not know can never succeed, so this is
            # permanent — the queue must not spend a meeting rediscovering that.
            raise PermanentError(
                "This Claude Code build does not understand the options this app sends. "
                f"Update it with: {update_command(path)}",
                category="setup",
            )
        if any(marker in lowered for marker in NOT_SIGNED_IN):
            raise PermanentError(
                "Claude Code is not signed in. Run `claude` and complete the login, then "
                "try again.",
                category="auth",
            )
        if any(marker in lowered.replace("\u2019", "'") for marker in QUOTA_SPENT):
            raise quota_exhausted(text)
        if any(marker in lowered for marker in RATE_LIMITED):
            raise RecoverableError(f"Claude Code is rate limited: {text[:200]}")
        raise RecoverableError(f"Claude Code exited {code}: {text[:300]}")

    @staticmethod
    def _payload(stdout: str) -> str:
        """`--output-format json` wraps the answer in an envelope; unwrap it."""
        text = stdout.strip()
        if not text:
            raise RecoverableError("Claude Code returned nothing")
        try:
            envelope = json.loads(text)
        except ValueError:
            return text  # not an envelope: treat stdout as the answer
        if isinstance(envelope, dict):
            if envelope.get("is_error"):
                raise RecoverableError(str(envelope.get("result") or envelope)[:300])
            for key in ("result", "text", "content", "response"):
                value = envelope.get(key)
                if isinstance(value, str) and value.strip():
                    return value
        return text

    def count_tokens(self, text: str) -> int:
        """No token endpoint here, so windows are estimated — and deliberately small.

        DESIGN.md §9.1 warns that a character heuristic silently blows the window on
        Hebrew, so this errs high: fewer tokens claimed per character means more windows.
        """
        return estimate_tokens(text)
