"""The MCP stdio transport and the part of the protocol a tool server answers.

Messages are JSON-RPC 2.0, one per line on stdin and stdout. A request runs on a thread of
its own, so a tool that waits (a transcription long-poll) does not hold up ping or
cancellation. When stdin closes, the process exits at once: a client stopping its server
closes stdin, and a server that lingers keeps its files locked (bug z8tj1he4zz).
"""

from __future__ import annotations

import json
import os
import sys
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import IO, Any

#: Protocol versions this server speaks, newest first. A client asking for one of these
#: gets it; any other request gets the newest.
SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class ToolError(Exception):
    """A failure the model should read, returned as a tool result with ``isError``."""


class Cancelled(Exception):
    """The client cancelled the request this tool call belongs to."""


@dataclass
class Call:
    """What a running tool is handed: its arguments, and how to report and check."""

    arguments: dict[str, Any]
    progress: Callable[[float, float | None, str | None], None]
    cancelled: threading.Event

    def check(self) -> None:
        if self.cancelled.is_set():
            raise Cancelled


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    run: Callable[[Call], str]
    read_only: bool = True
    title: str | None = None

    def describe(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": {
                "readOnlyHint": self.read_only,
                "destructiveHint": False,
                "openWorldHint": False,
            },
        }
        if self.title:
            out["title"] = self.title
            out["annotations"]["title"] = self.title
        return out


@dataclass
class Server:
    name: str
    version: str
    instructions: str
    tools: list[Tool]
    stdin: IO[str] = field(default_factory=lambda: sys.stdin)
    stdout: IO[str] = field(default_factory=lambda: sys.stdout)
    _write_lock: threading.Lock = field(default_factory=threading.Lock)
    _running: dict[Any, threading.Event] = field(default_factory=dict)

    # -- output -------------------------------------------------------------

    def send(self, message: dict[str, Any]) -> None:
        line = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            self.stdout.write(line + "\n")
            self.stdout.flush()

    def _result(self, request_id: Any, result: dict[str, Any]) -> None:
        self.send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _error(self, request_id: Any, code: int, message: str) -> None:
        self.send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})

    # -- the loop ------------------------------------------------------------

    def serve(self) -> None:
        """Until stdin closes; then the process ends, whatever is still running."""
        for raw in self.stdin:
            line = raw.strip()
            if line:
                self.handle_line(line)
        self.stdout.flush()
        os._exit(0)

    def handle_line(self, line: str) -> None:
        try:
            message = json.loads(line)
        except ValueError:
            self._error(None, PARSE_ERROR, "not JSON")
            return
        if isinstance(message, list):  # a batch, from an older client
            for item in message:
                self.handle(item)
            return
        self.handle(message)

    def handle(self, message: Any) -> None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            self._error(None, INVALID_REQUEST, "not a JSON-RPC 2.0 message")
            return
        method = message.get("method")
        if method is None:
            return  # a response to something we never send
        params = message.get("params") or {}
        if "id" not in message:
            self._notification(method, params)
            return
        request_id = message["id"]
        if method == "tools/call":
            cancelled = threading.Event()
            self._running[request_id] = cancelled
            threading.Thread(
                target=self._call_tool, args=(request_id, params, cancelled), daemon=True
            ).start()
            return
        try:
            self._result(request_id, self._request(method, params))
        except _MethodNotFound:
            self._error(request_id, METHOD_NOT_FOUND, f"unknown method {method!r}")

    def _notification(self, method: str, params: dict[str, Any]) -> None:
        if method == "notifications/cancelled":
            event = self._running.get(params.get("requestId"))
            if event is not None:
                event.set()

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            asked = params.get("protocolVersion")
            version = asked if asked in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
            return {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": self.name, "version": self.version},
                "instructions": self.instructions,
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [tool.describe() for tool in self.tools]}
        if method in ("prompts/list", "resources/list", "resources/templates/list"):
            key = {"prompts/list": "prompts", "resources/list": "resources"}.get(
                method, "resourceTemplates"
            )
            return {key: []}
        raise _MethodNotFound

    def _call_tool(
        self, request_id: Any, params: dict[str, Any], cancelled: threading.Event
    ) -> None:
        try:
            name = params.get("name")
            tool = next((t for t in self.tools if t.name == name), None)
            if tool is None:
                self._error(request_id, INVALID_PARAMS, f"unknown tool {name!r}")
                return
            arguments = params.get("arguments") or {}
            token = (params.get("_meta") or {}).get("progressToken")

            def progress(
                done: float, total: float | None = None, message: str | None = None
            ) -> None:
                if token is None:
                    return
                note: dict[str, Any] = {"progressToken": token, "progress": done}
                if total is not None:
                    note["total"] = total
                if message:
                    note["message"] = message
                self.send({"jsonrpc": "2.0", "method": "notifications/progress", "params": note})

            try:
                text = tool.run(Call(arguments, progress, cancelled))
                result = {"content": [{"type": "text", "text": text}], "isError": False}
            except ToolError as exc:
                result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
            except Cancelled:
                return  # the client no longer wants an answer
            except Exception as exc:  # a bug: say so to the model rather than hang it
                traceback.print_exc(file=sys.stderr)
                result = {
                    "content": [{"type": "text", "text": f"upshot-mcp failed: {exc}"}],
                    "isError": True,
                }
            if not cancelled.is_set():
                self._result(request_id, result)
        finally:
            self._running.pop(request_id, None)


class _MethodNotFound(Exception):
    pass
