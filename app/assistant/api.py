"""``POST /api/assistant/chat``: one turn, streamed as the AI SDK UI message stream."""

from __future__ import annotations

import sys
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.assistant import stream
from app.assistant.citations import Citer
from app.assistant.claude_route import ClaudeRoute, Turn
from app.assistant.store import SessionStore
from app.llm.prompts import load
from app.log import get
from app.services import Services

log = get(__name__)

router = APIRouter(prefix="/api/assistant")


class ChatPost(BaseModel):
    id: str = ""
    messages: list[dict[str, Any]] = Field(default_factory=list)
    #: The screen the question was asked from: ``{"route": "/m/abc", "meeting_id": "abc",
    #: "scope": "meeting" | "all"}``.
    context: dict[str, Any] = Field(default_factory=dict)
    #: Sent by useChat: ``submit-message``, or ``regenerate-message`` for Retry.
    trigger: str = ""


def services_of(request: Request) -> Services:
    return request.app.state.services  # type: ignore[no-any-return]


def last_user_message(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    for message in reversed(messages):
        if message.get("role") == "user":
            return message
    return None


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
    scope = str(context.get("scope") or ("meeting" if meeting else "all"))
    where = f"\n\nThe user is on the screen {route or '/'}"
    if meeting:
        where += f", looking at the meeting with id {meeting}"
    if meeting and scope == "meeting":
        where += (
            '. The question is about that meeting unless it says otherwise; "this meeting" '
            "means that one"
        )
    else:
        where += ". The question is about all of the user's meetings"
    return text + where + "."


def store_of(svc: Services) -> SessionStore:
    return SessionStore(svc.conn, svc.clock)


@router.post("/chat")
async def chat(request: Request, body: ChatPost) -> StreamingResponse:
    svc = services_of(request)
    question = last_user_text(body.messages)
    chat_id = body.id or uuid.uuid4().hex
    route, problem = route_for(svc)
    store = store_of(svc)

    async def events() -> AsyncIterator[str]:
        message_id = f"a-{uuid.uuid4().hex[:12]}"
        yield stream.sse(stream.start(message_id))
        if route is None:
            assert problem is not None
            yield stream.sse(stream.data("problem", problem))
            text = PROBLEM_TEXT.get(problem["code"], "The assistant cannot answer.")
            yield stream.sse(stream.error(text))
            yield stream.sse(stream.finish("error"))
            yield stream.DONE
            return
        asked = last_user_message(body.messages)
        if not question or asked is None:
            yield stream.sse(stream.error("Ask a question."))
            yield stream.sse(stream.finish("error"))
            yield stream.DONE
            return
        session = store.ensure(chat_id, title=question, provider=route.name)
        if body.trigger == "regenerate-message" and asked.get("id"):
            # Retry: the answer being replaced goes, so it is not stored twice.
            store.truncate_after(chat_id, str(asked["id"]))
        recap = store.recap(chat_id)
        store.append(
            chat_id,
            {
                "id": asked.get("id") or uuid.uuid4().hex,
                "role": "user",
                "parts": asked.get("parts") or [{"type": "text", "text": question}],
            },
        )
        turn = Turn(session_id=session.cli_session_id)
        collector = stream.Collector()
        try:
            async for chunk in route.run(
                question,
                port=svc.config.server_port,
                token=svc.auth.mcp_token,
                system=system_prompt(body.context),
                resume=session.cli_session_id,
                turn=turn,
                citer=Citer(svc.dao),
                recap=recap,
            ):
                collector.feed(chunk)
                yield stream.sse(chunk)
            if turn.session_id and not turn.failed:
                store.set_cli_session(chat_id, turn.session_id, route.name)
            metadata = {"provider": route.name, "model": turn.model}
            yield stream.sse(stream.finish("error" if turn.failed else "stop", metadata))
            yield stream.DONE
        finally:
            # Also when the panel stops the answer part-way: what arrived is kept.
            if collector.has_answer or collector.parts:
                store.append(
                    chat_id,
                    {
                        "id": message_id,
                        "role": "assistant",
                        "parts": collector.parts,
                        "metadata": {"provider": route.name, "model": turn.model},
                    },
                )

    return StreamingResponse(events(), media_type=stream.MEDIA_TYPE, headers=stream.HEADERS)


class SessionPatch(BaseModel):
    title: str


@router.get("/sessions")
def sessions(request: Request) -> dict[str, Any]:
    store = store_of(services_of(request))
    return {"sessions": [session.as_api() for session in store.recent()]}


@router.get("/sessions/{session_id}")
def session(request: Request, session_id: str) -> dict[str, Any]:
    svc = services_of(request)
    store = store_of(svc)
    found = store.get(session_id)
    if found is None:
        raise HTTPException(404, f"no such conversation: {session_id}")
    messages = store.messages(session_id)
    # A citation into a meeting deleted since is shown as such, not as a dead link (D62).
    exists: dict[str, bool] = {}
    for message in messages:
        for part in message["parts"]:
            if isinstance(part, dict) and part.get("type") == "data-citation":
                data = part.get("data") or {}
                meeting_id = str(data.get("meeting_id", ""))
                if meeting_id not in exists:
                    exists[meeting_id] = svc.dao.get_meeting(meeting_id) is not None
                if not exists[meeting_id]:
                    data["missing"] = True
    return {"session": found.as_api(), "messages": messages}


@router.get("/status")
def status(request: Request) -> dict[str, Any]:
    """Which provider answers, and whether it can: the panel names it before a question
    is asked (D62), and says up front when the chosen provider is not served yet."""
    svc = services_of(request)
    route, problem = route_for(svc)
    return {
        "provider": str(svc.config.get("llm.provider", "anthropic")),
        "available": route is not None,
        "problem": problem["code"] if problem else None,
    }


@router.patch("/sessions/{session_id}")
def rename_session(request: Request, session_id: str, body: SessionPatch) -> dict[str, Any]:
    store = store_of(services_of(request))
    if not body.title.strip() or not store.rename(session_id, body.title):
        raise HTTPException(404 if store.get(session_id) is None else 400, "cannot rename")
    found = store.get(session_id)
    assert found is not None
    return {"session": found.as_api()}


@router.delete("/sessions/{session_id}")
def delete_session(request: Request, session_id: str) -> dict[str, Any]:
    if not store_of(services_of(request)).delete(session_id):
        raise HTTPException(404, f"no such conversation: {session_id}")
    return {"deleted": session_id}
