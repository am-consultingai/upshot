"""The assistant end to end with a fake Claude CLI that makes real MCP calls."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.db.dao import Turn
from app.pipeline.states import MeetingState
from tests.fixtures.api import build_harness, serve

FAKE = str(Path(__file__).resolve().parents[1] / "fixtures" / "fake_claude.py")


@pytest.fixture
def api(tmp_path: Path, app_home: Path):  # type: ignore[no-untyped-def]
    harness = build_harness(tmp_path)
    harness.services.config.set("assistant.cli_command", [sys.executable, FAKE])
    dao = harness.services.dao
    dao.insert_meeting(
        meeting_id="m-budget",
        folder=harness.services.config.data_root / "m-budget",
        source="manual",
        state=MeetingState.RECORDING,
        profile="cpu-deferred",
        title="Q4 budget review",
        started_at="2026-09-20T09:30:00Z",
    )
    dao.index_turns(
        "m-budget", [Turn(0, "THEM", 4000, "The marketing budget is cut by ten percent.")]
    )
    return harness


def chunks_of(response: httpx.Response) -> list[Any]:
    out: list[Any] = []
    for line in response.text.splitlines():
        if line.startswith("data: "):
            payload = line[len("data: ") :]
            out.append(payload if payload == "[DONE]" else json.loads(payload))
    return out


def ask(client: httpx.Client, text: str, chat_id: str = "c1", **extra: Any) -> httpx.Response:
    body = {
        "id": chat_id,
        "messages": [{"role": "user", "parts": [{"type": "text", "text": text}]}],
        **extra,
    }
    return client.post("/api/assistant/chat", json=body)


def text_of(chunks: list[Any]) -> str:
    return "".join(c["delta"] for c in chunks if isinstance(c, dict) and c["type"] == "text-delta")


def test_a_question_calls_the_real_tool_server_and_streams_the_answer(api) -> None:  # type: ignore[no-untyped-def]
    with serve(api) as client:
        response = ask(client, "Which meetings mention the budget")
    assert response.status_code == 200
    assert response.headers["x-vercel-ai-ui-message-stream"] == "v1"
    chunks = chunks_of(response)
    assert chunks[0]["type"] == "start" and chunks[-1] == "[DONE]"
    call = next(c for c in chunks if c != "[DONE]" and c["type"] == "tool-input-available")
    assert (call["toolName"], call["input"]) == ("search", {"query": "budget"})
    output = next(c for c in chunks if c != "[DONE]" and c["type"] == "tool-output-available")
    assert "Q4 budget review" in output["output"], "the tool ran against this app's data"
    assert "<upshot-data>" in output["output"], "tool output is marked as data"
    assert "Q4 budget review" in text_of(chunks)
    assert chunks[-2]["type"] == "finish" and chunks[-2]["finishReason"] == "stop"


def test_a_follow_up_resumes_the_same_cli_session(api) -> None:  # type: ignore[no-untyped-def]
    with serve(api) as client:
        ask(client, "Which meetings mention the budget", chat_id="c2")
        second = chunks_of(ask(client, "NOTOOL and the first one?", chat_id="c2"))
        other = chunks_of(ask(client, "NOTOOL hello", chat_id="c3"))
    assert text_of(second).startswith("Continuing."), "the CLI was given --resume"
    assert not text_of(other).startswith("Continuing."), "another chat is another session"


def test_signed_out_says_so_with_a_problem_code(api) -> None:  # type: ignore[no-untyped-def]
    with serve(api) as client:
        chunks = chunks_of(ask(client, "SIGNED-OUT anything"))
    problem = next(c for c in chunks if c != "[DONE]" and c["type"] == "data-problem")
    assert problem["data"]["code"] == "signed-out"
    error = next(c for c in chunks if c != "[DONE]" and c["type"] == "error")
    assert "not signed in" in error["errorText"]
    assert chunks[-2]["finishReason"] == "error"


@pytest.mark.parametrize(
    ("provider", "code"),
    [
        ("ollama", "local-model"),
        ("anthropic", "unsupported-provider"),
        ("codex-subscription", "codex"),
    ],
)
def test_providers_the_assistant_does_not_serve_yet(api, provider: str, code: str) -> None:  # type: ignore[no-untyped-def]
    api.services.config.set("llm.provider", provider)
    chunks = chunks_of(ask(api.client(), "anything"))
    problem = next(c for c in chunks if c != "[DONE]" and c["type"] == "data-problem")
    assert problem["data"]["code"] == code


def test_the_chat_route_still_needs_the_csrf_token(api) -> None:  # type: ignore[no-untyped-def]
    client = api.client()
    del client.headers["x-csrf-token"]
    assert ask(client, "anything").status_code == 403


def test_the_tool_server_opens_only_with_its_own_token(api) -> None:  # type: ignore[no-untyped-def]
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "0"},
        },
    }
    headers = {"Accept": "application/json, text/event-stream"}
    with serve(api) as client:
        # The page's cookie and CSRF token are not enough.
        assert client.post("/mcp/", json=init, headers=headers).status_code == 401
        wrong = {**headers, "Authorization": "Bearer nope"}
        assert client.post("/mcp/", json=init, headers=wrong).status_code == 401
        good = {**headers, "Authorization": f"Bearer {api.services.auth.mcp_token}"}
        assert client.post("/mcp/", json=init, headers=good).status_code == 200
        listed = client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers=good,
        ).json()
    names = sorted(tool["name"] for tool in listed["result"]["tools"])
    assert names == [
        "calendar_range",
        "get_meeting",
        "get_transcript",
        "list_action_items",
        "list_meetings",
        "related_meetings",
        "search",
    ]
    assert all(tool["annotations"]["readOnlyHint"] is True for tool in listed["result"]["tools"])
    assert not any(tool["annotations"].get("destructiveHint") for tool in listed["result"]["tools"])


def test_citations_are_checked_snapped_and_numbered(api) -> None:  # type: ignore[no-untyped-def]
    """The fake cites 500 ms inside the line, splits the marker across two deltas, and
    adds one to a meeting that does not exist."""
    with serve(api) as client:
        chunks = chunks_of(ask(client, "Which meetings mention the budget"))
    citations = [c["data"] for c in chunks if c != "[DONE]" and c["type"] == "data-citation"]
    assert len(citations) == 1, "the invented meeting is dropped"
    cite = citations[0]
    assert (cite["n"], cite["meeting_id"], cite["at_ms"]) == (1, "m-budget", 4000), "snapped"
    assert cite["quote"] == "The marketing budget is cut by ten percent."
    assert cite["title"] == "Q4 budget review" and cite["speaker"]
    text = text_of(chunks)
    assert "[1](#cite-1)" in text
    assert "[[" not in text and "no-such-meeting" not in text


# ------------------------------------------------------------------ saved conversations


def test_a_conversation_is_kept_and_reopens_as_it_was_shown(api) -> None:  # type: ignore[no-untyped-def]
    with serve(api) as client:
        ask(client, "Which meetings mention the budget", chat_id="kept")
        listed = client.get("/api/assistant/sessions").json()["sessions"]
        opened = client.get("/api/assistant/sessions/kept").json()
    assert [(s["id"], s["title"]) for s in listed] == [
        ("kept", "Which meetings mention the budget")
    ]
    assert opened["session"]["provider"] == "claude-subscription"
    user, answer = opened["messages"]
    assert (
        user["role"] == "user" and user["parts"][0]["text"] == "Which meetings mention the budget"
    )
    kinds = [part["type"] for part in answer["parts"]]
    assert "dynamic-tool" in kinds and "data-citation" in kinds and "text" in kinds
    tool = next(part for part in answer["parts"] if part["type"] == "dynamic-tool")
    assert (tool["toolName"], tool["state"]) == ("search", "output-available")
    text = next(part for part in answer["parts"] if part["type"] == "text")["text"]
    assert "[1](#cite-1)" in text


def test_rename_and_delete(api) -> None:  # type: ignore[no-untyped-def]
    with serve(api) as client:
        ask(client, "NOTOOL hello", chat_id="r")
        renamed = client.patch("/api/assistant/sessions/r", json={"title": "  Greetings  "})
        assert renamed.json()["session"]["title"] == "Greetings"
        assert client.patch("/api/assistant/sessions/nope", json={"title": "x"}).status_code == 404
        assert client.delete("/api/assistant/sessions/r").status_code == 200
        assert client.get("/api/assistant/sessions/r").status_code == 404
        assert client.delete("/api/assistant/sessions/r").status_code == 404
    assert api.services.conn.execute("SELECT COUNT(*) FROM assistant_messages").fetchone()[0] == 0


def test_a_session_the_cli_lost_starts_again_with_a_recap(api) -> None:  # type: ignore[no-untyped-def]
    from app.assistant.store import SessionStore

    with serve(api) as client:
        ask(client, "NOTOOL first question", chat_id="lost")
        SessionStore(api.services.conn, api.services.clock).set_cli_session(
            "lost", "gone-123", "claude-subscription"
        )
        chunks = chunks_of(ask(client, "NOTOOL and then?", chat_id="lost"))
        session = client.get("/api/assistant/sessions/lost").json()
    assert text_of(chunks).startswith("Recapped."), "the question went with the conversation so far"
    assert not any(c != "[DONE]" and c["type"] == "error" for c in chunks), "the user never saw it"
    stored = SessionStore(api.services.conn, api.services.clock).get("lost")
    assert stored is not None and not stored.cli_session_id.startswith("gone-"), (
        "the new one is kept"
    )
    assert len(session["messages"]) == 4


def test_long_first_questions_make_short_titles() -> None:
    from app.assistant.store import title_from

    assert title_from("  what   did we decide  ") == "what did we decide"
    long = "what did we decide about the pricing page and the Berlin launch in the last three weeks"
    assert title_from(long) == "what did we decide about the pricing page and the Berlin…"
