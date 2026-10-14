"""Settings → AI apps, and the activity page (D98): what the page needs to connect the
user's own AI tools to Upshot, and to show what they asked.

Under ``/api``, not ``/api/v1``: these are the page's routes, behind CSRF like the rest.
The key is shown only on a POST, which a page on another site cannot make. A program on
this computer can, while the page's own API answers anyone (D57): the key keeps other
callers out of ``/mcp/ext/`` and ``/api/v1`` fully only once that is fixed.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.api.security import DOCKER_HOST, EXT_MCP_PREFIX, reset_access_key
from app.config import DEFAULT_PORT
from app.log import get
from app.services import Services
from app.transcription.api import SERVER_NAME, TEST_INSTALL_DIR, bridge_path, install_dir, wsl_path

log = get(__name__)

router = APIRouter(prefix="/api/ai-apps")

WSL_ARGS = '--wsl-distro "$WSL_DISTRO_NAME"'


def services_of(request: Request) -> Services:
    return request.app.state.services  # type: ignore[no-any-return]


def commands(bridge: str | None) -> dict[str, Any]:
    """How to add the bridge to each AI app, by its own command or its own file (Claude's
    are in the transcription section's connect card). Upshot
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
        # Cursor, VS Code, LM Studio, mcphost and most others: an mcpServers entry. VS Code
        # nests the same entry under "servers"; the page says so in its one line.
        "mcp_json": json.dumps({"mcpServers": {SERVER_NAME: stdio}}, indent=2),
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


def _test_install() -> bool:
    """The e2e server: a made-up install, and no real lookup of the user's apps."""
    return install_dir() == TEST_INSTALL_DIR


@router.get("/found")
def found(request: Request) -> dict[str, Any]:
    """Which AI apps are on this computer, on Windows and in a running WSL distribution.
    Asked when the page opens or comes back into view, apart from the overview: asking
    WSL takes a moment. Nothing of the apps' own is read."""
    from app.ai_terminal import find

    return {"apps": find(services_of(request).config, test=_test_install()).as_dict()}


class TerminalPost(BaseModel):
    app: str
    side: str = "windows"


@router.post("/terminal")
def open_terminal(request: Request, body: TerminalPost) -> dict[str, Any]:
    """A terminal with the app's own command to add Upshot typed in; the user presses
    Enter (D98: Upshot edits no other app's files). The line is built here from the same
    commands the page shows, never taken from the request."""
    from app import ai_terminal

    if body.app not in ai_terminal.PROGRAMS or body.side not in ("windows", "wsl"):
        raise HTTPException(400, "Unknown app.")
    key = body.app if body.side == "windows" else f"{body.app}_wsl"
    command = commands(bridge_path()).get(key)
    if command is None:
        raise HTTPException(409, "Available in the installed app.")
    if sys.platform != "win32" or _test_install():
        # Nowhere to open it: the page has copied the command and says to paste it.
        return {"launched": False, "command": command}
    fresh = ai_terminal.registry_path()
    if body.side == "windows":
        program = ai_terminal.find_on_windows(services_of(request).config).get(body.app)
        line = ai_terminal.windows_line(command, program, fresh)
        argv = ai_terminal.powershell_argv(line)
    else:
        line = command
        argv = ai_terminal.wsl_argv(line, ai_terminal.find_in_wsl().get(body.app))
    try:
        ai_terminal.launch(argv, fresh)
    except OSError as exc:
        log.error("could not open a terminal for %s (%s): %s", body.app, body.side, exc)
        raise HTTPException(500, f"Could not open a terminal: {exc}") from exc
    log.info("opened a terminal to add Upshot to %s (%s)", body.app, body.side)
    from app.window import bring_terminal_forward

    bring_terminal_forward(ai_terminal.TITLE)
    return {"launched": True, "command": line}


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
