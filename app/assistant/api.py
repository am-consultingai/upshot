"""``POST /api/assistant/chat``: one turn, streamed as the AI SDK UI message stream."""

from __future__ import annotations

import sys
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.assistant import stream
from app.assistant.citations import Citer
from app.assistant.claude_route import ClaudeRoute, Turn
from app.llm.prompts import load
from app.log import get
from app.services import Services

log = get(__name__)

router = APIRouter(prefix="/api/assistant")

#: Where the conversation's CLI session id is kept until sessions are stored (plan step 4).
SESSIONS = "assistant.cli_sessions"


class ChatPost(BaseModel):
    id: str = ""
    messages: list[dict[str, Any]] = Field(default_factory=list)
    #: The screen the question was asked from: ``{"route": "/m/abc", "meeting_id": "abc"}``.
    context: dict[str, Any] = Field(default_factory=dict)


def services_of(request: Request) -> Services:
    return request.app.state.services  # type: ignore[no-any-return]


def last_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        parts = message.get("parts") or []
        text = "".join(
            str(part.get("text", ""))
            for part in parts
            if isinstance(part, dict) and part.get("type") == "text"
        )
        if text.strip():
            return text.strip()
        if isinstance(message.get("content"), str):
            return str(message["content"]).strip()
    return ""


def route_for(svc: Services) -> tuple[ClaudeRoute | None, dict[str, Any] | None]:
    """The CLI that answers, or why none can. Subscriptions only for now (D62)."""
    provider = str(svc.config.get("llm.provider", "anthropic"))
    command = list(svc.config.get("assistant.cli_command", []) or [])
    if provider == "fake":
        if not command:
            return None, {"code": "not-configured", "provider": provider}
        return ClaudeRoute(
            svc.config, command=[sys.executable if c == "{python}" else c for c in command]
        ), None
    if provider == "claude-subscription":
        return ClaudeRoute(svc.config, command=command or None), None
    if provider == "codex-subscription":
        return None, {"code": "codex", "provider": provider}
    if provider == "ollama":
        return None, {"code": "local-model", "provider": provider}
    return None, {"code": "unsupported-provider", "provider": provider}


CHOOSE_CLAUDE = "Choose Claude Code in Settings → AI to use it."
PROBLEM_TEXT = {
    "local-model": f"The assistant does not work with local models yet. {CHOOSE_CLAUDE}",
    "unsupported-provider": (
        "For now the assistant works with your Claude plan through Claude Code. "
        "Choose it in Settings → AI."
    ),
    "codex": f"The assistant does not work with Codex yet. {CHOOSE_CLAUDE}",
    "not-configured": "The assistant is not configured.",
}


def system_prompt(context: dict[str, Any]) -> str:
    text = load("assistant").text
    route = str(context.get("route") or "")
    meeting = str(context.get("meeting_id") or "")
    where = f"\n\nThe user is on the screen {route or '/'}"
    if meeting:
        where += f', looking at the meeting with id {meeting}. "This meeting" means that one'
    return text + where + "."


@router.post("/chat")
async def chat(request: Request, body: ChatPost) -> StreamingResponse:
    svc = services_of(request)
    question = last_user_text(body.messages)
    chat_id = body.id or uuid.uuid4().hex
    sessions: dict[str, str] = svc.extras.setdefault(SESSIONS, {})
    route, problem = route_for(svc)

    async def events() -> AsyncIterator[str]:
        message_id = f"a-{uuid.uuid4().hex[:12]}"
        yield stream.sse(stream.start(message_id))
        if route is None:
            assert problem is not None
            yield stream.sse(stream.data("problem", problem))
            yield stream.sse(
                stream.error(PROBLEM_TEXT.get(problem["code"], "The assistant cannot answer."))
            )
            yield stream.sse(stream.finish("error"))
            yield stream.DONE
            return
        if not question:
            yield stream.sse(stream.error("Ask a question."))
            yield stream.sse(stream.finish("error"))
            yield stream.DONE
            return
        turn = Turn(session_id=sessions.get(chat_id, ""))
        async for chunk in route.run(
            question,
            port=svc.config.server_port,
            token=svc.auth.mcp_token,
            system=system_prompt(body.context),
            resume=sessions.get(chat_id, ""),
            turn=turn,
            citer=Citer(svc.dao),
        ):
            yield stream.sse(chunk)
        if turn.session_id and not turn.failed:
            sessions[chat_id] = turn.session_id
        yield stream.sse(
            stream.finish("error" if turn.failed else "stop", {"provider": route.name})
        )
        yield stream.DONE

    return StreamingResponse(events(), media_type=stream.MEDIA_TYPE, headers=stream.HEADERS)
