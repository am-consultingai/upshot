"""A stand-in for the Claude Code CLI in assistant tests: no model, no credit.

It takes the arguments the app passes, reads the question from stdin, makes a real MCP
``tools/call`` to the server named in ``--mcp-config`` (so the app's tool server is
exercised end to end), and prints ``stream-json`` shaped like a real run's (captured
from Claude Code 2.1.282 on 2026-09-25).

Behaviour by what the question contains:
- ``SIGNED-OUT``  prints the CLI's not-signed-in message and exits 1.
- ``SLOW``        waits between words, so Stop can be tested.
- ``NOTOOL``      answers without calling a tool.
- otherwise       searches for the quoted words, or the question's last word.

Standard library only: it runs under whatever Python the app does.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
import uuid


def arg(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1] if name in argv else ""


def emit(event: dict) -> None:  # type: ignore[type-arg]
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def mcp_call(config_path: str, tool: str, arguments: dict) -> str:  # type: ignore[type-arg]
    with open(config_path, encoding="utf-8") as handle:
        server = json.load(handle)["mcpServers"]["upshot"]
    headers = {
        **server.get("headers", {}),
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }

    def post(payload: dict) -> dict:  # type: ignore[type-arg]
        request = urllib.request.Request(
            server["url"], data=json.dumps(payload).encode(), headers=headers, method="POST"
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
        return json.loads(body) if body.strip() else {}

    post(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "fake-claude", "version": "0"},
            },
        }
    )
    reply = post(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
    )
    content = (reply.get("result") or {}).get("content") or []
    return "".join(block.get("text", "") for block in content if isinstance(block, dict))


def say(message_id: str, text: str, *, slow: bool, tail: list[str] | None = None) -> None:
    """Stream ``text`` word by word, then each piece of ``tail`` as its own delta."""
    pieces = [*re.findall(r"\S+\s*", text), *(tail or [])]
    text = text + "".join(tail or [])
    emit(
        {"type": "stream_event", "event": {"type": "message_start", "message": {"id": message_id}}}
    )
    emit(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        }
    )
    for word in pieces:
        emit(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": word},
                },
            }
        )
        if slow:
            time.sleep(0.4)
    emit(
        {
            "type": "assistant",
            "message": {
                "id": message_id,
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
            },
        }
    )
    emit({"type": "stream_event", "event": {"type": "content_block_stop", "index": 0}})
    emit({"type": "stream_event", "event": {"type": "message_stop"}})


def main() -> int:
    argv = sys.argv[1:]
    question = sys.stdin.read()
    if "SIGNED-OUT" in question:
        sys.stderr.write("Invalid API key · Please run /login\n")
        return 1
    session = arg(argv, "--resume") or str(uuid.uuid4())
    emit(
        {
            "type": "system",
            "subtype": "init",
            "session_id": session,
            "tools": ["mcp__upshot__search"],
        }
    )
    slow = "SLOW" in question
    prefix = "Continuing. " if "--resume" in argv else ""
    if "NOTOOL" in question:
        say("msg_1", f"{prefix}I can answer that without searching.", slow=slow)
    else:
        quoted = re.findall(r"[\"“']([^\"”']+)[\"”']", question)
        words = re.findall(r"\w+", question)
        query = quoted[0] if quoted else (words[-1] if words else "")
        call_id = "toolu_fake_1"
        emit(
            {
                "type": "assistant",
                "message": {
                    "id": "msg_0",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": call_id,
                            "name": "mcp__upshot__search",
                            "input": {"query": query},
                        }
                    ],
                },
            }
        )
        result = mcp_call(arg(argv, "--mcp-config"), "search", {"query": query})
        emit(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "tool_use_id": call_id,
                            "type": "tool_result",
                            "content": json.dumps({"result": result}),
                        }
                    ],
                },
            }
        )
        match = re.search(r'"count":\s*(\d+)', result)
        count = int(match.group(1)) if match else 0
        titles = list(dict.fromkeys(re.findall(r'"meeting_title":\s*"([^"]*)"', result)))[:3]
        tail: list[str] = []
        moment = re.search(
            r'"meeting_id":\s*"([^"]+)"[^{}]*?"kind":\s*"transcript"[^{}]*?"at_ms":\s*(\d+)',
            result,
        )
        if count:
            text = f"{prefix}Found {count} results for “{query}”, in: " + ", ".join(titles) + "."
            if moment:
                # Cited the way the prompt asks: a marker split across two deltas, a
                # moment a little inside the line (it must snap back to the line's
                # start), and one that names no real meeting (it must be dropped).
                meeting_id, at_ms = moment.group(1), int(moment.group(2)) + 500
                tail = [f" [[m:{meeting_id}@", f"{at_ms}]] [[m:no-such-meeting@1]]"]
        else:
            text = f"{prefix}No meetings mention “{query}”."
        say("msg_1", text, slow=slow, tail=tail)
    emit(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "session_id": session,
            "result": "",
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
