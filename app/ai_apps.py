"""Settings → AI apps, and the activity page (D98): what the page needs to connect the
user's own AI tools to Upshot, and to show what they asked.

Under ``/api``, not ``/api/v1``: these are the page's routes, behind CSRF like the rest.
The key is shown only on a POST, which a page on another site cannot make. A program on
this computer can, while the page's own API answers anyone (D57): the key keeps other
callers out of ``/mcp/ext/`` and ``/api/v1`` fully only once that is fixed.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request

from app.api.security import DOCKER_HOST, EXT_MCP_PREFIX, reset_access_key
from app.config import DEFAULT_PORT
from app.services import Services
from app.transcription.api import SERVER_NAME, bridge_path, wsl_path

router = APIRouter(prefix="/api/ai-apps")

WSL_ARGS = '--wsl-distro "$WSL_DISTRO_NAME"'


def services_of(request: Request) -> Services:
    return request.app.state.services  # type: ignore[no-any-return]


def commands(bridge: str | None) -> dict[str, Any]:
    """How to add the bridge to each AI app, by its own command or its own file. Upshot
    edits no other app's configuration (D98): the user runs these."""
    if bridge is None:
        return {}
    wsl = wsl_path(bridge)
    stdio = {"command": bridge, "args": []}
    return {
        "claude_code": f'claude mcp add --scope user {SERVER_NAME} -- "{bridge}"',
        "claude_code_wsl": f'claude mcp add --scope user {SERVER_NAME} -- "{wsl}" {WSL_ARGS}',
        "codex": f'codex mcp add {SERVER_NAME} -- "{bridge}"',
        "codex_wsl": f'codex mcp add {SERVER_NAME} -- "{wsl}" {WSL_ARGS}',
        "antigravity": f'agy mcp add {SERVER_NAME} "{bridge}"',
        "antigravity_wsl": f'agy mcp add {SERVER_NAME} -- "{wsl}" {WSL_ARGS}',
        "gemini": f'gemini mcp add --scope user {SERVER_NAME} "{bridge}"',
        # Cursor, LM Studio, mcphost and most others: an mcpServers entry.
        "mcp_json": json.dumps({"mcpServers": {SERVER_NAME: stdio}}, indent=2),
        # VS Code's own shape.
        "vscode_json": json.dumps({"servers": {SERVER_NAME: {"type": "stdio", **stdio}}}, indent=2),
    }


@router.get("")
def overview(request: Request) -> dict[str, Any]:
    svc = services_of(request)
    port = svc.config.server_port
    return {
        "meetings_enabled": bool(svc.config.get("mcp.meetings_enabled", True)),
        "transcription_enabled": bool(svc.config.get("transcription.service_enabled", True)),
        "port": port,
        "usual_port": DEFAULT_PORT,
        # A URL client breaks when the port moves (D95); the page says so.
        "port_moved": port != DEFAULT_PORT,
        "urls": {
            "local": f"http://127.0.0.1:{port}{EXT_MCP_PREFIX}/",
            "docker": f"http://{DOCKER_HOST}:{port}{EXT_MCP_PREFIX}/",
        },
        "bridge": bridge_path(),
        "commands": commands(bridge_path()),
    }


@router.post("/key")
def show_key(request: Request) -> dict[str, str]:
    """The access key, for an app that takes a URL and a key (Open WebUI)."""
    return {"key": services_of(request).auth.access_key}


@router.post("/key/reset")
def new_key(request: Request) -> dict[str, str]:
    """A new key at once: apps holding the old one are refused until given this one.
    The bridge reads the file again by itself."""
    from app import paths

    svc = services_of(request)
    key = reset_access_key(svc.auth, paths.app_home())
    svc.events.publish("ai-apps", changed=["key"])
    return {"key": key}


@router.get("/activity")
def activity(request: Request, limit: int = 200, agent: str = "") -> dict[str, Any]:
    log = services_of(request).access_log
    return {
        "entries": [entry.as_dict() for entry in log.recent(limit, agent)],
        "agents": log.agents(),
    }


@router.post("/activity/clear")
def clear_activity(request: Request) -> dict[str, int]:
    svc = services_of(request)
    cleared = svc.access_log.clear()
    svc.events.publish("ai-apps", changed=["activity"])
    return {"cleared": cleared}
