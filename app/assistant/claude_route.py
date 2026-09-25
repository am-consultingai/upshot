"""The assistant on the user's own Claude plan: the Claude Code CLI runs the loop.

Spawned the way the summarizer spawns it (``app/llm/claude_cli.py``): the resolved
binary, the environment without ``ANTHROPIC_API_KEY``, a neutral working folder, no
window. What differs is the conversation: the CLI gets Upshot's MCP server and nothing
else (``--tools ""`` turns every built-in tool off, ``--strict-mcp-config`` ignores the
user's own servers), streams ``stream-json``, and a later turn resumes its session.

The events are translated into the AI SDK stream here, so the panel never sees the
CLI's format and the Codex route can feed the same panel.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
import threading
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.assistant import stream
from app.assistant.citations import Citer
from app.assistant.mcp import TOOL_PREFIX, client_config
from app.config import Config
from app.errors import PermanentError, QuotaExhausted, RecoverableError
from app.llm import claude_cli
from app.log import get

log = get(__name__)

#: A turn that has said nothing for this long is dead, whatever the CLI thinks.
IDLE_TIMEOUT_S = 300.0


@dataclass
class Turn:
    """What one run of the CLI produced, besides the chunks it streamed."""

    session_id: str = ""
    text: str = ""
    failed: bool = False
    #: The CLI no longer has the session it was asked to resume (plan step 4).
    session_lost: bool = False
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


def tool_name(raw: str) -> str:
    return raw[len(TOOL_PREFIX) :] if raw.startswith(TOOL_PREFIX) else raw


def tool_result_text(content: Any) -> str:
    """A tool result as the CLI reports it: a string, a ``{"result": …}`` envelope, or
    a list of content blocks."""
    if isinstance(content, list):
        return "".join(str(block.get("text", "")) for block in content if isinstance(block, dict))
    text = str(content or "")
    try:
        envelope = json.loads(text)
    except ValueError:
        return text
    if isinstance(envelope, dict) and isinstance(envelope.get("result"), str):
        return str(envelope["result"])
    return text


def classify(code: int, out: str, err: str, path: str) -> str:
    """The same wording the summarizer uses for the same failures."""
    try:
        claude_cli.ClaudeCliClient._raise_for(code, out, err, path)
    except QuotaExhausted as exc:
        return str(exc)
    except (PermanentError, RecoverableError) as exc:
        return str(exc)
    return f"Claude Code exited {code}"


class Translator:
    """``stream-json`` events in, AI SDK chunks out. Stateful across one turn."""

    def __init__(self, turn: Turn, citer: Citer | None = None) -> None:
        self.turn = turn
        self.citer = citer
        self.part = 0
        self.open_text: str | None = None
        #: Message ids whose text arrived as deltas, so the whole message is not re-sent.
        self.streamed: set[str] = set()
        self.current_message = ""

    def _text_id(self) -> str:
        self.part += 1
        return f"t{self.part}"

    def _cited(self, text: str, *, final: bool = False) -> tuple[str, list[dict[str, Any]]]:
        """The text with its citation markers checked, and a part for each new citation."""
        if self.citer is None:
            return text, []
        shown, found = self.citer.feed(text)
        if final:
            rest, more = self.citer.flush()
            shown, found = shown + rest, found + more
        return shown, [stream.data("citation", citation) for citation in found]

    def _emit_text(self, text: str, *, final: bool = False) -> list[dict[str, Any]]:
        shown, chunks = self._cited(text, final=final)
        if shown:
            if self.open_text is None:
                self.open_text = self._text_id()
                chunks.append(stream.text_start(self.open_text))
            self.turn.text += shown
            chunks.append(stream.text_delta(self.open_text, shown))
        return chunks

    def close_text(self) -> list[dict[str, Any]]:
        if self.open_text is None:
            return []
        chunks = self._emit_text("", final=True)
        part, self.open_text = self.open_text, None
        return [*chunks, stream.text_end(part)]

    def feed(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            self.turn.session_id = str(event.get("session_id") or self.turn.session_id)
            return []
        if kind == "stream_event":
            return self._partial(event.get("event") or {})
        if kind == "assistant":
            return self._assistant(event.get("message") or {})
        if kind == "user":
            return self._tool_results(event.get("message") or {})
        if kind == "result":
            self.turn.session_id = str(event.get("session_id") or self.turn.session_id)
            chunks = self.close_text()
            if event.get("is_error") or str(event.get("subtype", "")).startswith("error"):
                self.turn.failed = True
                detail = str(event.get("result") or event.get("subtype") or "failed")
                chunks.append(stream.error(f"Claude Code could not answer: {detail[:300]}"))
            return chunks
        return []

    def _partial(self, ev: dict[str, Any]) -> list[dict[str, Any]]:
        kind = ev.get("type")
        if kind == "message_start":
            self.current_message = str((ev.get("message") or {}).get("id", ""))
            return []
        if kind == "content_block_delta":
            delta = ev.get("delta") or {}
            if delta.get("type") != "text_delta":
                return []
            self.streamed.add(self.current_message)
            return self._emit_text(str(delta.get("text", "")))
        if kind == "content_block_stop":
            return self.close_text()
        return []

    def _assistant(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        chunks: list[dict[str, Any]] = []
        message_id = str(message.get("id", ""))
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and message_id not in self.streamed:
                # A CLI without partial messages: the whole text arrives at once.
                text = str(block.get("text", ""))
                if text:
                    chunks += self._emit_text(text, final=True)
                    chunks += self.close_text()
            elif block.get("type") == "tool_use":
                chunks += self.close_text()
                name = tool_name(str(block.get("name", "")))
                call_id = str(block.get("id", ""))
                arguments = block.get("input") or {}
                self.turn.tool_calls.append({"id": call_id, "name": name, "input": arguments})
                chunks.append(stream.tool_input(call_id, name, arguments))
        return chunks

    def _tool_results(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        chunks: list[dict[str, Any]] = []
        content = message.get("content")
        if not isinstance(content, list):
            return chunks
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            call_id = str(block.get("tool_use_id", ""))
            text = tool_result_text(block.get("content"))
            if block.get("is_error"):
                chunks.append(stream.tool_error(call_id, text[:500]))
            else:
                chunks.append(stream.tool_output(call_id, text))
        return chunks


class ClaudeRoute:
    name = "claude-subscription"

    def __init__(self, config: Config, *, command: Sequence[str] | None = None) -> None:
        self.config = config
        self.cli = claude_cli.ClaudeCliClient(config)
        #: A test hook: the whole command line that stands for ``claude``.
        self.command = list(command) if command else None

    def executable(self) -> list[str] | None:
        if self.command:
            return list(self.command)
        path = self.cli.resolve()
        return [path] if path else None

    def args(self, base: list[str], *, mcp_config: str, system: str, resume: str) -> list[str]:
        args = [
            *base,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--tools",
            "",
            "--strict-mcp-config",
            "--mcp-config",
            mcp_config,
            "--allowedTools",
            f"{TOOL_PREFIX}*",
            "--system-prompt",
            system,
        ]
        if resume:
            args += ["--resume", resume]
        return args

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
    ) -> AsyncIterator[dict[str, Any]]:
        """One turn. A resumed session the CLI has lost is started again with ``recap``,
        the conversation so far, in front of the question — before the user sees any
        of it."""
        turn = turn if turn is not None else Turn(session_id=resume)
        base = self.executable()
        if base is None:
            turn.failed = True
            yield stream.data("problem", {"code": "not-installed", "provider": self.name})
            yield stream.error(
                "Claude Code is not installed. Install it and sign in, or choose another "
                "provider in Settings → AI."
            )
            return
        with tempfile.TemporaryDirectory(prefix="upshot-assistant-") as folder:
            config_path = Path(folder) / "mcp.json"
            config_path.write_text(json.dumps(client_config(port, token)), encoding="utf-8")
            args = self.args(base, mcp_config=str(config_path), system=system, resume=resume)
            async for chunk in self._spawn(args, question, turn, base[0], citer, resume):
                yield chunk
            if turn.session_lost:
                log.info("the CLI has no session %s any more; starting a new one", resume)
                turn.session_lost = False
                turn.session_id = ""
                if recap:
                    question = f"(The conversation so far, for context:\n{recap}\n)\n\n{question}"
                args = self.args(base, mcp_config=str(config_path), system=system, resume="")
                async for chunk in self._spawn(args, question, turn, base[0], citer, ""):
                    yield chunk

    async def _spawn(
        self,
        args: list[str],
        question: str,
        turn: Turn,
        path: str,
        citer: Citer | None,
        resume: str,
    ) -> AsyncIterator[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        lines: asyncio.Queue[str | None] = asyncio.Queue()
        try:
            process = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=claude_cli.child_env(),
                cwd=claude_cli.workdir(),
                creationflags=claude_cli.creation_flags(visible=False),
            )
        except OSError as exc:
            turn.failed = True
            yield stream.data("problem", {"code": "not-installed", "provider": self.name})
            yield stream.error(f"Claude Code could not be started: {exc}")
            return
        stderr: list[str] = []

        def pump() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                loop.call_soon_threadsafe(lines.put_nowait, line)
            loop.call_soon_threadsafe(lines.put_nowait, None)

        def drain_stderr() -> None:
            assert process.stderr is not None
            stderr.append(process.stderr.read())

        threading.Thread(target=pump, name="assistant-cli-out", daemon=True).start()
        threading.Thread(target=drain_stderr, name="assistant-cli-err", daemon=True).start()
        try:
            assert process.stdin is not None
            # The question is piped, so its length and its quoting are never the command
            # line's problem.
            process.stdin.write(question)
            process.stdin.close()
        except OSError:
            pass

        translator = Translator(turn, citer)
        finished = False
        started = False
        lost_candidate: dict[str, Any] | None = None
        try:
            while True:
                try:
                    line = await asyncio.wait_for(lines.get(), timeout=IDLE_TIMEOUT_S)
                except TimeoutError:
                    turn.failed = True
                    yield stream.error("Claude Code stopped responding.")
                    return
                if line is None:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if (
                    resume
                    and not started
                    and event.get("type") == "result"
                    and event.get("is_error")
                    and not event.get("num_turns")
                ):
                    # Maybe a lost session; stderr says, once the process has ended.
                    finished = True
                    lost_candidate = event
                    continue
                if event.get("type") in ("assistant", "stream_event", "user"):
                    started = True
                for chunk in translator.feed(event):
                    yield chunk
                if event.get("type") == "result":
                    finished = True
            code = await loop.run_in_executor(None, process.wait)
            if lost_candidate is not None:
                await loop.run_in_executor(None, _wait_for, stderr)
                if "no conversation found" in "".join(stderr).lower():
                    turn.session_lost = True
                    return
                for chunk in translator.feed(lost_candidate):
                    yield chunk
            for chunk in translator.close_text():
                yield chunk
            if not finished:
                turn.failed = True
                text = classify(code, "", "".join(stderr), path)
                problem = problem_code(text)
                if problem:
                    yield stream.data("problem", {"code": problem, "provider": self.name})
                yield stream.error(text)
        finally:
            # Stop from the panel closes the request, which cancels this generator: the
            # CLI must not go on spending the user's allowance on an answer nobody reads.
            if process.poll() is None:
                process.kill()


def _wait_for(stderr: list[str], timeout: float = 5.0) -> None:
    """The stderr reader appends once, when the stream closes; give it a moment."""
    import time

    deadline = time.monotonic() + timeout
    while not stderr and time.monotonic() < deadline:
        time.sleep(0.02)


def problem_code(text: str) -> str:
    lowered = text.lower()
    if "not signed in" in lowered:
        return "signed-out"
    if "not installed" in lowered:
        return "not-installed"
    if "update it" in lowered:
        return "too-old"
    if "rate limited" in lowered:
        return "rate-limited"
    if "allowance" in lowered or "quota" in lowered or "limit" in lowered:
        return "quota"
    return ""
