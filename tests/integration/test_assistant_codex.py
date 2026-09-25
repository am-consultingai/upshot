"""The assistant on the user's ChatGPT plan, through a fake Codex that calls the real
tool server (plan step 7)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.assistant.claude_route import Turn
from app.assistant.codex_route import CodexTranslator
from app.db.dao import Turn as Line
from app.pipeline.states import MeetingState
from tests.fixtures.api import build_harness, serve
from tests.integration.test_assistant_api import ask, chunks_of, text_of

FAKE = str(Path(__file__).resolve().parents[1] / "fixtures" / "fake_codex.py")


@pytest.fixture
def api(tmp_path: Path, app_home: Path):  # type: ignore[no-untyped-def]
    harness = build_harness(tmp_path)
    harness.services.config.set("llm.provider", "codex-subscription")
    harness.services.config.set("assistant.cli_command", [sys.executable, FAKE])
    harness.services.dao.insert_meeting(
        meeting_id="m-budget",
        folder=harness.services.config.data_root / "m-budget",
        source="manual",
        state=MeetingState.RECORDING,
        profile="cpu-deferred",
        title="Q4 budget review",
        started_at="2026-09-20T09:30:00Z",
    )
    harness.services.dao.index_turns(
        "m-budget", [Line(0, "THEM", 4000, "The marketing budget is cut by ten percent.")]
    )
    return harness


def test_codex_answers_through_the_tool_server_with_a_checked_citation(api) -> None:  # type: ignore[no-untyped-def]
    with serve(api) as client:
        chunks = chunks_of(ask(client, "Which meetings mention the budget", chat_id="x"))
    kinds = [c["type"] for c in chunks if c != "[DONE]"]
    assert kinds.count("tool-input-available") == 1 and "tool-output-available" in kinds
    call = next(c for c in chunks if c != "[DONE]" and c["type"] == "tool-input-available")
    assert (call["toolName"], call["input"]) == ("search", {"query": "budget"})
    citation = next(c for c in chunks if c != "[DONE]" and c["type"] == "data-citation")
    assert (citation["data"]["meeting_id"], citation["data"]["at_ms"]) == ("m-budget", 4000)
    assert text_of(chunks) == "Codex found it [1](#cite-1)."
    assert chunks[-2]["messageMetadata"]["provider"] == "codex-subscription"


def test_every_turn_carries_the_conversation_so_far(api) -> None:  # type: ignore[no-untyped-def]
    with serve(api) as client:
        first = text_of(chunks_of(ask(client, "Which meetings mention the budget", chat_id="y")))
        second = text_of(chunks_of(ask(client, "and the 'budget' again?", chat_id="y")))
    assert not first.startswith("Recapped.")
    assert second.startswith("Recapped."), "nothing is kept in Codex; Upshot sends the recap"


def test_signed_out_and_a_spent_plan_say_so(api) -> None:  # type: ignore[no-untyped-def]
    with serve(api) as client:
        signed_out = chunks_of(ask(client, "SIGNED-OUT", chat_id="z1"))
        spent = chunks_of(ask(client, "QUOTA", chat_id="z2"))
    problem = next(c for c in signed_out if c != "[DONE]" and c["type"] == "data-problem")
    assert problem["data"]["code"] == "signed-out"
    problem = next(c for c in spent if c != "[DONE]" and c["type"] == "data-problem")
    assert problem["data"]["code"] == "quota"
    error = next(c for c in spent if c != "[DONE]" and c["type"] == "error")
    assert "usage limit" in error["errorText"]


def test_the_status_says_codex_answers(api) -> None:  # type: ignore[no-untyped-def]
    assert api.client().get("/api/assistant/status").json() == {
        "provider": "codex-subscription",
        "available": True,
        "problem": None,
    }


def test_a_failed_tool_call_is_a_tool_error() -> None:
    translator = CodexTranslator(Turn())
    item = {"id": "i", "type": "mcp_tool_call", "tool": "search", "arguments": {"query": "x"}}
    started = translator.feed({"type": "item.started", "item": {**item, "status": "in_progress"}})
    failed = translator.feed(
        {
            "type": "item.completed",
            "item": {**item, "status": "failed", "error": {"message": "boom"}},
        }
    )
    assert [c["type"] for c in started] == ["tool-input-available"]
    assert failed == [
        {"type": "tool-output-error", "toolCallId": "i", "errorText": "boom", "dynamic": True}
    ]
