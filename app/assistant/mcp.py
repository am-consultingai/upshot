"""Upshot's tools as an MCP server, for the CLIs (D61).

Served by the app itself at ``/mcp/`` over streamable HTTP. The CLI is a local process
the app spawned, so the only credential is a bearer token made when the app starts and
handed to that process in its ``--mcp-config``. Nothing else on the machine knows it;
the page's cookie and CSRF token do not open this door, and this token opens nothing
else.
"""

from __future__ import annotations

import secrets
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

from app.assistant.tools import AssistantTools
from app.log import get
from app.services import Services

log = get(__name__)

PREFIX = "/mcp"
SERVER_NAME = "upshot"
#: What a CLI calls a tool of this server: ``mcp__upshot__search``.
TOOL_PREFIX = f"mcp__{SERVER_NAME}__"


def build_server(svc: Services) -> Any:
    from mcp.server.mcpserver import MCPServer
    from mcp.types import ToolAnnotations

    tools = AssistantTools(svc)
    server = MCPServer(SERVER_NAME, instructions="Read-only access to the user's Upshot meetings.")
    read_only = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)

    @server.tool(
        description=(
            "Search the user's meetings: titles, action items and transcript lines that "
            "contain the words. Returns meeting ids, titles, dates, and for transcript "
            "lines the speaker and the time (at_ms) the line was said."
        ),
        annotations=read_only,
    )
    def search(query: str, limit: int = 20) -> str:
        return tools.search(query, limit)

    return server


class TokenGuard:
    """Pure ASGI, like the app's own middleware, so the stream is never buffered."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            given = ""
            for key, value in scope.get("headers", []):
                if key.lower() == b"authorization":
                    given = value.decode("latin-1")
            if not secrets.compare_digest(given, f"Bearer {self.token}"):
                await send({"type": "http.response.start", "status": 401, "headers": []})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await self.app(scope, receive, send)


def mount(svc: Services) -> tuple[ASGIApp, Any]:
    """The guarded ASGI app to mount at :data:`PREFIX`, and the server whose session
    manager the app's lifespan must run."""
    server = build_server(svc)
    inner = server.streamable_http_app(
        streamable_http_path="/", stateless_http=True, json_response=True
    )
    return TokenGuard(inner, svc.auth.mcp_token), server


def client_config(port: int, token: str) -> dict[str, Any]:
    """The ``--mcp-config`` a CLI gets: this server, and nothing else."""
    return {
        "mcpServers": {
            SERVER_NAME: {
                "type": "http",
                "url": f"http://127.0.0.1:{port}{PREFIX}/",
                "headers": {"Authorization": f"Bearer {token}"},
            }
        }
    }
