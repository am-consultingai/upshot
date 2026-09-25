"""A stand-in for the Codex CLI in assistant tests: no model, no credit.

It checks it was started the way the app must start it (read-only sandbox, nothing kept,
no shell, no web search, Upshot's server pre-approved, the token only in the
environment), makes a real MCP ``tools/call`` to the server the ``-c`` overrides name,
and prints ``codex exec --json`` events in the shape of codex-rs ``exec_events.rs``.

Behaviour by what the question contains:
- ``SIGNED-OUT``  fails the way an unauthenticated Codex does.
- ``QUOTA``       fails with the plan's usage-limit message.
- otherwise       searches for the quoted words, or the question's last word, and cites.

A prompt that carries "The conversation so far" is answered "Recapped. …".
Standard library only.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request

REQUIRED = [
    ("--sandbox", "read-only"),
    ("--ephemeral", None),
    ("--ignore-user-config", None),
    ("-c", "features.shell_tool=false"),
    ("-c", 'web_search="disabled"'),
    ("-c", 'mcp_servers.upshot.default_tools_approval_mode="approve"'),
]


def emit(event: dict) -> None:  # type: ignore[type-arg]
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def overrides(argv: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for index, arg in enumerate(argv):
        if arg == "-c" and index + 1 < len(argv):
            key, _, value = argv[index + 1].partition("=")
            found[key] = value.strip('"')
    return found


def mcp_call(url: str, token: str, tool: str, arguments: dict) -> str:  # type: ignore[type-arg]
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }

    def post(payload: dict) -> dict:  # type: ignore[type-arg]
        request = urllib.request.Request(
            url, data=json.dumps(payload).encode(), headers=headers, method="POST"
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
                "clientInfo": {"name": "fake-codex", "version": "0"},
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


def main() -> int:
    argv = sys.argv[1:]
    for flag, value in REQUIRED:
        pairs = list(zip(argv, [*argv[1:], ""], strict=True))
        if not any(a == flag and (value is None or b == value) for a, b in pairs):
            sys.stderr.write(f"fake codex: started without {flag} {value or ''}\n")
            return 2
    if argv[:2] != ["exec", "--json"] or argv[-1] != "-":
        sys.stderr.write("fake codex: expected `exec --json … -`\n")
        return 2
    config = overrides(argv)
    token = os.environ.get(config.get("mcp_servers.upshot.bearer_token_env_var", ""), "")
    if not token or any(token in arg for arg in argv):
        sys.stderr.write("fake codex: the token must be in the environment, not the arguments\n")
        return 2

    prompt = sys.stdin.read()
    question = prompt.rsplit("The user's question:\n", 1)[-1]
    if "SIGNED-OUT" in question:
        body = '{"error":{"message":"No auth credentials found"}}'
        sys.stderr.write(f"unexpected status 401 Unauthorized: {body}\n")
        return 1
    emit({"type": "thread.started", "thread_id": "0199a213-81c0-7800-8aa1-bbab2a035a53"})
    emit({"type": "turn.started"})
    if "QUOTA" in question:
        emit(
            {
                "type": "turn.failed",
                "error": {
                    "message": "You've hit your usage limit. Try again at Sep 18th, 2026 12:04 AM."
                },
            }
        )
        return 1

    quoted = re.findall(r"[\"“']([^\"”']+)[\"”']", question)
    words = re.findall(r"\w+", question)
    query = quoted[0] if quoted else (words[-1] if words else "")
    item = {
        "id": "item_1",
        "type": "mcp_tool_call",
        "server": "upshot",
        "tool": "search",
        "arguments": {"query": query},
    }
    emit(
        {
            "type": "item.started",
            "item": {**item, "result": None, "error": None, "status": "in_progress"},
        }
    )
    result = mcp_call(config["mcp_servers.upshot.url"], token, "search", {"query": query})
    emit(
        {
            "type": "item.completed",
            "item": {
                **item,
                "result": {
                    "content": [{"type": "text", "text": result}],
                    "structured_content": None,
                },
                "error": None,
                "status": "completed",
            },
        }
    )
    prefix = "Recapped. " if "The conversation so far" in prompt else ""
    moment = re.search(
        r'"meeting_id":"([^"]+)"[^{}]*?"kind":"transcript"[^{}]*?"at_ms":(\d+)', result
    )
    text = f"{prefix}Codex found it" + (
        f" [[m:{moment.group(1)}@{moment.group(2)}]]." if moment else " nowhere."
    )
    emit(
        {"type": "item.completed", "item": {"id": "item_2", "type": "agent_message", "text": text}}
    )
    emit(
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 5},
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
