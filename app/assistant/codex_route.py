"""The assistant on the user's ChatGPT plan: the Codex CLI runs the loop (plan step 7).

Spawned like the summarizer's Codex (``app/llm/codex_cli.py``): the resolved binary,
``--sandbox read-only``, ``--ignore-user-config``, ``--ephemeral``, a neutral folder.
Upshot's tools come in through ``-c`` overrides naming its MCP server; the token goes in
the environment (``bearer_token_env_var``), never on the command line where any process
list would show it. Built-in shell and web search are turned off, and the server's
read-only tools are pre-approved — ``exec`` never asks, so an unapproved call would
simply fail.

``codex exec`` has no system prompt, and ``--ephemeral`` keeps nothing between runs:
nothing lands in the user's own Codex history, which is also why the summarizer runs
this way. So every turn is one run whose prompt is the instructions, a recap of the
conversation Upshot stored, and the question.

Its ``--json`` events differ from Claude Code's: an answer arrives whole on
``item.completed`` rather than as deltas, and a tool call is an ``mcp_tool_call`` item.
Checked against codex-rs ``exec_events.rs``; not yet against a real run on this machine,
where Codex is not installed.
"""

from __future__ import annotations

import os
from typing import Any

from app.assistant import stream
from app.assistant.citations import Citer
from app.assistant.claude_route import ClaudeRoute, Translator, Turn, problem_code
from app.assistant.mcp import PREFIX, SERVER_NAME
from app.errors import PermanentError, QuotaExhausted, RecoverableError
from app.llm import codex_cli

TOKEN_ENV = "UPSHOT_MCP_TOKEN"


class CodexTranslator(Translator):
    def __init__(self, turn: Turn, citer: Citer | None = None) -> None:
        super().__init__(turn, citer)
        self.announced: set[str] = set()

    def done(self, event: dict[str, Any]) -> bool:
        return event.get("type") in ("turn.completed", "turn.failed")

    def feed(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        kind = event.get("type")
        if kind == "thread.started":
            self.turn.session_id = str(event.get("thread_id") or "")
            return []
        if kind in ("item.started", "item.completed"):
            item = event.get("item") or {}
            if not isinstance(item, dict):
                return []
            if item.get("type") == "mcp_tool_call":
                return self._tool(item, finished=kind == "item.completed")
            if item.get("type") == "agent_message" and kind == "item.completed":
                return [
                    *self._emit_text(str(item.get("text") or ""), final=True),
                    *self.close_text(),
                ]
            return []
        if kind == "turn.completed":
            return self.close_text()
        if kind in ("turn.failed", "error"):
            self.turn.failed = True
            error = event.get("error") if kind == "turn.failed" else event
            message = str((error or {}).get("message") or "Codex could not answer")
            chunks = self.close_text()
            code = problem_code(message)
            if code:
                chunks.append(
                    stream.data("problem", {"code": code, "provider": "codex-subscription"})
                )
            return [*chunks, stream.error(f"Codex could not answer: {message[:300]}")]
        return []

    def _tool(self, item: dict[str, Any], *, finished: bool) -> list[dict[str, Any]]:
        call_id = str(item.get("id", ""))
        chunks: list[dict[str, Any]] = []
        if call_id not in self.announced:
            self.announced.add(call_id)
            chunks += self.close_text()
            name = str(item.get("tool", ""))
            arguments = item.get("arguments") or {}
            self.turn.tool_calls.append({"id": call_id, "name": name, "input": arguments})
            chunks.append(stream.tool_input(call_id, name, arguments))
        if not finished:
            return chunks
        error = item.get("error")
        if item.get("status") == "failed" or error:
            message = str((error or {}).get("message") or "the tool failed")
            chunks.append(stream.tool_error(call_id, message[:500]))
            return chunks
        result = item.get("result") or {}
        content = result.get("content") if isinstance(result, dict) else None
        text = "".join(
            str(block.get("text", ""))
            for block in content or []
            if isinstance(block, dict) and block.get("type") == "text"
        )
        chunks.append(stream.tool_output(call_id, text))
        return chunks


class CodexRoute(ClaudeRoute):
    name = "codex-subscription"
    label = "Codex"

    def __init__(self, config: Any, *, command: list[str] | None = None) -> None:
        super().__init__(config, command=command)
        self.cli = codex_cli.CodexCliClient(config)  # type: ignore[assignment]
        self.token = ""

    def env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "CODEX_API_KEY")}
        env[TOKEN_ENV] = self.token
        return env

    def translator(self, turn: Turn, citer: Citer | None) -> Translator:
        return CodexTranslator(turn, citer)

    def classify(self, code: int, err: str, path: str) -> str:
        try:
            codex_cli.CodexCliClient._raise_for(code, "", err, path)
        except QuotaExhausted as exc:
            return str(exc)
        except (PermanentError, RecoverableError) as exc:
            return str(exc)
        return f"Codex exited {code}"

    def codex_args(self, base: list[str], *, port: int) -> list[str]:
        server = f"mcp_servers.{SERVER_NAME}"
        return [
            *base,
            "exec",
            "--json",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--ephemeral",
            "--ignore-user-config",
            "--color",
            "never",
            "--cd",
            codex_cli.workdir(),
            "-c",
            f'{server}.url="http://127.0.0.1:{port}{PREFIX}/"',
            "-c",
            f'{server}.bearer_token_env_var="{TOKEN_ENV}"',
            "-c",
            f'{server}.default_tools_approval_mode="approve"',
            "-c",
            'web_search="disabled"',
            "-c",
            "features.shell_tool=false",
            "-",
        ]

    async def run(
        self,
        question: str,
        *,
        port: int,
        token: str,
        system: str,
        resume: str = "",
        turn: Turn | None = None,
        citer: Citer | None = None,
        recap: str = "",
    ) -> Any:
        turn = turn if turn is not None else Turn()
        base = self.executable()
        if base is None:
            turn.failed = True
            yield stream.data("problem", {"code": "not-installed", "provider": self.name})
            yield stream.error(
                "Codex is not installed. Install it from Settings and sign in, or choose "
                "another provider in Settings → AI."
            )
            return
        self.token = token
        prompt = system
        if recap:
            prompt += f"\n\nThe conversation so far:\n{recap}"
        prompt += f"\n\nThe user's question:\n{question}"
        async for chunk in self._spawn(
            self.codex_args(base, port=port), prompt, turn, base[0], citer, ""
        ):
            yield chunk
