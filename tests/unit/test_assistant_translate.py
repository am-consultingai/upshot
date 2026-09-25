"""The Claude CLI's stream-json, translated into the stream the panel reads."""

from __future__ import annotations

import json
from pathlib import Path

from app.assistant.claude_route import Translator, Turn, problem_code, tool_result_text

SAMPLE = Path(__file__).resolve().parents[1] / "fixtures" / "claude_stream_sample.jsonl"


def run(events: list[dict]) -> tuple[list[dict], Turn]:  # type: ignore[type-arg]
    turn = Turn()
    translator = Translator(turn)
    chunks = [chunk for event in events for chunk in translator.feed(event)]
    return chunks + translator.close_text(), turn


def test_a_real_run_becomes_a_tool_call_then_streamed_text() -> None:
    """Captured from Claude Code 2.1.282 calling a local MCP server (2026-09-25)."""
    events = [json.loads(line) for line in SAMPLE.read_text(encoding="utf-8").splitlines()]
    chunks, turn = run(events)
    kinds = [chunk["type"] for chunk in chunks]
    assert kinds[0] == "tool-input-available"
    assert chunks[0]["toolName"] == "search", "the MCP prefix is the CLI's, not the panel's"
    assert chunks[0]["input"] == {"query": "budget"}
    assert kinds[1] == "tool-output-available"
    assert "Budget review" in chunks[1]["output"], "the {result: …} envelope is unwrapped"
    assert kinds[2] == "text-start" and kinds[-1] == "text-end"
    text = "".join(chunk["delta"] for chunk in chunks if chunk["type"] == "text-delta")
    assert text.startswith("The search found two meetings")
    assert kinds.count("text-start") == 1, "the full assistant message is not sent twice"
    assert turn.session_id and not turn.failed
    assert turn.text == text


def test_without_partial_messages_the_whole_text_still_arrives() -> None:
    chunks, _ = run(
        [
            {
                "type": "assistant",
                "message": {"id": "m1", "content": [{"type": "text", "text": "Hello"}]},
            }
        ]
    )
    assert [c["type"] for c in chunks] == ["text-start", "text-delta", "text-end"]


def test_an_error_result_is_an_error_chunk() -> None:
    chunks, turn = run([{"type": "result", "subtype": "error_during_execution", "is_error": True}])
    assert chunks[-1]["type"] == "error" and turn.failed


def test_tool_errors_and_result_shapes() -> None:
    chunks, _ = run(
        [
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "t",
                            "is_error": True,
                            "content": "boom",
                        }
                    ]
                },
            }
        ]
    )
    assert chunks == [
        {"type": "tool-output-error", "toolCallId": "t", "errorText": "boom", "dynamic": True}
    ]
    assert tool_result_text([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]) == "ab"
    assert tool_result_text('{"result": "x"}') == "x"
    assert tool_result_text("plain") == "plain"


def test_problem_codes() -> None:
    assert problem_code("Claude Code is not signed in. Run `claude`") == "signed-out"
    assert problem_code("Claude Code is rate limited: 429") == "rate-limited"
    assert problem_code("something else") == ""
