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

import json
import os
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app.config import Config
from app.errors import PermanentError, RecoverableError
from app.llm.client import LlmResult
from app.llm.repair import complete_with_repair, schema_instruction
from app.llm.schema import NOTES_SCHEMA
from app.log import get

log = get(__name__)

#: Removed from the model's context: this provider summarizes text and has no business
#: reading or writing the user's disk.
DISALLOWED_TOOLS = "Bash,Read,Write,Edit,NotebookEdit,Glob,Grep,WebSearch,WebFetch,Task,TodoWrite"

NOT_SIGNED_IN = ("not logged in", "please run /login", "invalid api key", "authentication")
RATE_LIMITED = ("rate limit", "usage limit", "quota", "429", "overloaded")


class Runner(Protocol):
    """Runs the CLI. Injected in tests so no subprocess and no credit is spent."""

    def __call__(self, args: Sequence[str], stdin: str, timeout: float) -> tuple[int, str, str]: ...


def subprocess_runner(args: Sequence[str], stdin: str, timeout: float) -> tuple[int, str, str]:
    # The child must not inherit ANTHROPIC_API_KEY: with it set, Claude Code asks to use
    # the key instead of the subscription session, which is the opposite of the point.
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    completed = subprocess.run(
        list(args),
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )
    return completed.returncode, completed.stdout, completed.stderr


@dataclass(frozen=True)
class CliStatus:
    installed: bool
    version: str = ""
    path: str = ""
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "installed": self.installed,
            "version": self.version,
            "path": self.path,
            "detail": self.detail,
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
        return shutil.which(self.executable) or (
            self.executable if os.path.exists(self.executable) else None
        )

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
        return CliStatus(True, version=out.strip(), path=path)

    def login_command(self) -> list[str]:
        """What the user runs to sign in. We launch it; Anthropic's flow owns it."""
        return [self.resolve() or self.executable]

    # -- protocol ----------------------------------------------------------

    def build_args(self, path: str) -> list[str]:
        extra = list(self.config.get("llm.claude_cli_args", []) or [])
        return [
            path,
            "-p",
            "--output-format",
            "json",
            "--disallowed-tools",
            str(self.config.get("llm.claude_cli_disallowed_tools", DISALLOWED_TOOLS)),
            *extra,
        ]

    def complete_json(
        self,
        *,
        system_blocks: Sequence[dict[str, Any]],
        user: str,
        schema: dict[str, Any] = NOTES_SCHEMA,
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
            args = self.build_args(path)
            self.calls.append({"args": args, "instruction": instruction, "stdin": message})
            try:
                code, out, err = self.runner([*args, instruction], message, self.timeout)
            except subprocess.TimeoutExpired as exc:
                raise RecoverableError(f"Claude Code timed out after {self.timeout:g}s") from exc
            except FileNotFoundError as exc:
                raise PermanentError("Claude Code is not installed", category="setup") from exc
            if code != 0:
                self._raise_for(code, out, err)
            return self._payload(out)

        data, attempts = complete_with_repair(
            send, user, schema, max_repairs=self.max_repairs, provider=self.name
        )
        return LlmResult(data=data, model=f"claude-code:{self.name}", attempts=attempts)

    @staticmethod
    def _raise_for(code: int, out: str, err: str) -> None:
        text = f"{err}\n{out}".strip()
        lowered = text.lower()
        if any(marker in lowered for marker in NOT_SIGNED_IN):
            raise PermanentError(
                "Claude Code is not signed in. Run `claude` and complete the login, then "
                "try again.",
                category="auth",
            )
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
        return int(len(text) / 2.0) + 1
