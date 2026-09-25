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
            "Search the user's meetings by words: meeting titles, action items, summaries "
            "and transcript lines that contain them. Hebrew words are found inside their "
            "prefixed forms. Returns meeting ids, titles and dates, and for transcript "
            "lines the speaker and at_ms, the time the line was said."
        ),
        annotations=read_only,
    )
    def search(query: str, limit: int = 20) -> str:
        return tools.search(query, limit)

    @server.tool(
        description=(
            "List meetings, newest first. Optional: date_from and date_to (YYYY-MM-DD), "
            "and contains (words the meeting must mention)."
        ),
        annotations=read_only,
    )
    def list_meetings(
        date_from: str = "", date_to: str = "", contains: str = "", limit: int = 30
    ) -> str:
        return tools.list_meetings(date_from, date_to, contains, limit)

    @server.tool(
        description=(
            "One meeting in full: title, date, participants, the summary as text, and its "
            "action items (who, what, due, done)."
        ),
        annotations=read_only,
    )
    def get_meeting(meeting_id: str) -> str:
        return tools.get_meeting(meeting_id)

    @server.tool(
        description=(
            "A meeting's transcript, or the part of it between from_ms and to_ms. Each "
            "line has at_ms (when it was said), the speaker and the text. Long "
            "transcripts come in parts; the result says where the next part starts."
        ),
        annotations=read_only,
    )
    def get_transcript(meeting_id: str, from_ms: int = 0, to_ms: int = 0) -> str:
        return tools.get_transcript(meeting_id, from_ms, to_ms)

    @server.tool(
        description=(
            "Action items across all meetings, or one meeting's. open_only (default "
            'true) leaves out what is done. person filters by who owes it; "me" is '
            "the user."
        ),
        annotations=read_only,
    )
    def list_action_items(
        open_only: bool = True, person: str = "", meeting_id: str = "", limit: int = 100
    ) -> str:
        return tools.list_action_items(open_only, person, meeting_id, limit)

    @server.tool(
        description=(
            "The user's calendar events between date_from and date_to (YYYY-MM-DD; one "
            "day if date_to is left out), with attendees and, when it was recorded, the "
            "meeting id of the recording."
        ),
        annotations=read_only,
    )
    def calendar_range(date_from: str, date_to: str = "") -> str:
        return tools.calendar_range(date_from, date_to)

    @server.tool(
        description=(
            "Meetings related to one meeting — the same people, the same calendar "
            "series, shared action items, or a rare shared word — and why."
        ),
        annotations=read_only,
    )
    def related_meetings(meeting_id: str) -> str:
        return tools.related_meetings(meeting_id)

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
