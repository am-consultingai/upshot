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
