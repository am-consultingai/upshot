"""The AI SDK "UI message stream" (v1), which ``useChat`` reads: server-sent events,
one JSON chunk per ``data:`` line, ended by ``[DONE]``. The chunk types are the ones
``ai`` 7.x validates (``uiMessageChunkSchema``)."""

from __future__ import annotations

import json
from typing import Any

HEADERS = {
    "x-vercel-ai-ui-message-stream": "v1",
    "Cache-Control": "no-store",
    "X-Accel-Buffering": "no",
}
MEDIA_TYPE = "text/event-stream"
DONE = "data: [DONE]\n\n"


def sse(chunk: dict[str, Any]) -> str:
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"


def start(message_id: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    chunk: dict[str, Any] = {"type": "start", "messageId": message_id}
    if metadata:
        chunk["messageMetadata"] = metadata
    return chunk


def text_start(part_id: str) -> dict[str, Any]:
    return {"type": "text-start", "id": part_id}


def text_delta(part_id: str, delta: str) -> dict[str, Any]:
    return {"type": "text-delta", "id": part_id, "delta": delta}


def text_end(part_id: str) -> dict[str, Any]:
    return {"type": "text-end", "id": part_id}


def tool_input(call_id: str, name: str, arguments: Any) -> dict[str, Any]:
    # `dynamic`: the panel declares no tools of its own, so each call is a dynamic-tool
    # part rather than a typed `tool-<name>` one.
    return {
        "type": "tool-input-available",
        "toolCallId": call_id,
        "toolName": name,
        "input": arguments,
        "dynamic": True,
    }


def tool_output(call_id: str, output: Any) -> dict[str, Any]:
    return {
        "type": "tool-output-available",
        "toolCallId": call_id,
        "output": output,
        "dynamic": True,
    }


def tool_error(call_id: str, text: str) -> dict[str, Any]:
    return {"type": "tool-output-error", "toolCallId": call_id, "errorText": text, "dynamic": True}


def data(name: str, payload: Any, *, transient: bool = False) -> dict[str, Any]:
    chunk: dict[str, Any] = {"type": f"data-{name}", "data": payload}
    if transient:
        chunk["transient"] = True
    return chunk


def error(text: str) -> dict[str, Any]:
    return {"type": "error", "errorText": text}


def finish(reason: str = "stop", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    chunk: dict[str, Any] = {"type": "finish", "finishReason": reason}
    if metadata:
        chunk["messageMetadata"] = metadata
    return chunk


#: A tool's full output is for the model; what is kept of it is enough to show the step.
STORED_OUTPUT_CHARS = 2000


class Collector:
    """Builds the UIMessage the stream describes, to store it (plan step 4).

    Fed every chunk that goes to the panel, it ends up with the same ``parts`` the panel
    assembled, so a stored conversation reopens exactly as it was shown.
    """

    def __init__(self) -> None:
        self.parts: list[dict[str, Any]] = []
        self._text: dict[str, dict[str, Any]] = {}
        self._tools: dict[str, dict[str, Any]] = {}
        self.error = ""

    def feed(self, chunk: dict[str, Any]) -> None:
        kind = str(chunk.get("type", ""))
        part: dict[str, Any] | None
        if kind == "text-start":
            part = {"type": "text", "text": "", "state": "done"}
            self._text[str(chunk["id"])] = part
            self.parts.append(part)
        elif kind == "text-delta":
            part = self._text.get(str(chunk["id"]))
            if part is not None:
                part["text"] += str(chunk.get("delta", ""))
        elif kind == "tool-input-available":
            part = {
                "type": "dynamic-tool",
                "toolName": chunk.get("toolName"),
                "toolCallId": chunk.get("toolCallId"),
                "state": "input-available",
                "input": chunk.get("input"),
            }
            self._tools[str(chunk.get("toolCallId"))] = part
            self.parts.append(part)
        elif kind == "tool-output-available":
            part = self._tools.get(str(chunk.get("toolCallId")))
            if part is not None:
                output = chunk.get("output")
                if isinstance(output, str) and len(output) > STORED_OUTPUT_CHARS:
                    output = output[:STORED_OUTPUT_CHARS] + "…"
                part.update(state="output-available", output=output)
        elif kind == "tool-output-error":
            part = self._tools.get(str(chunk.get("toolCallId")))
            if part is not None:
                part.update(state="output-error", errorText=chunk.get("errorText"))
        elif kind.startswith("data-") and not chunk.get("transient"):
            self.parts.append({"type": kind, "data": chunk.get("data")})
        elif kind == "error":
            self.error = str(chunk.get("errorText", ""))

    @property
    def has_answer(self) -> bool:
        return any(part["type"] == "text" and part["text"].strip() for part in self.parts)
