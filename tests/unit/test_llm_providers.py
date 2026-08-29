"""The alternative summarization providers, driven through injected transports."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.config import Config, default_config
from app.errors import PermanentError, RecoverableError
from app.llm.claude_cli import ClaudeCliClient
from app.llm.client import make_client, system_blocks
from app.llm.gemini_client import GeminiClient
from app.llm.openai_client import OpenAiClient
from app.llm.repair import complete_with_repair, extract_json, schema_instruction, strip_fence
from app.llm.schema import NOTES_SCHEMA

VALID: dict[str, Any] = {
    "title": "Weekly sync",
    "tldr": ["we shipped", "nothing broke"],
    "topics": [{"heading": "Release", "points": ["ship Tuesday"]}],
    "decisions": [{"what": "ship", "who_decided": "ME", "at_ms": 1000}],
    "action_items": [{"who": "ME", "what": "cut the tag", "due": None, "confidence": 0.9}],
}
BLOCKS = system_blocks("SYSTEM", "GLOSSARY")


# ------------------------------------------------------------------ repair loop


def test_strip_fence_and_extract() -> None:
    assert strip_fence('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json('```\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! {"a": 1} hope that helps') == {"a": 1}
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_repair_loop_feeds_the_error_back() -> None:
    replies = ["not json at all", json.dumps({"title": "x"}), json.dumps(VALID)]
    seen: list[str] = []

    def send(message: str) -> str:
        seen.append(message)
        return replies.pop(0)

    data, attempts = complete_with_repair(send, "TRANSCRIPT", NOTES_SCHEMA, provider="test")
    assert data["title"] == "Weekly sync"
    assert attempts == 3
    assert "rejected" in seen[1] and "rejected" in seen[2]
    assert seen[0] == "TRANSCRIPT"


def test_repair_loop_gives_up_permanently() -> None:
    with pytest.raises(PermanentError, match="schema-valid JSON"):
        complete_with_repair(lambda m: "nope", "x", NOTES_SCHEMA, max_repairs=1, provider="test")


def test_schema_instruction_carries_the_schema() -> None:
    text = schema_instruction(NOTES_SCHEMA)
    assert "JSON Schema" in text and '"action_items"' in text


# ------------------------------------------------------------------ claude CLI


def fake_runner(script: list[tuple[int, str, str]]):  # type: ignore[no-untyped-def]
    calls: list[dict[str, Any]] = []

    def run(args, stdin, timeout):  # type: ignore[no-untyped-def]
        calls.append({"args": list(args), "stdin": stdin, "timeout": timeout})
        return script[min(len(calls) - 1, len(script) - 1)]

    run.calls = calls  # type: ignore[attr-defined]
    return run


def envelope(payload: Any) -> str:
    return json.dumps({"type": "result", "is_error": False, "result": json.dumps(payload)})


def test_claude_cli_summarizes_through_the_subprocess(tmp_path) -> None:  # type: ignore[no-untyped-def]
    runner = fake_runner([(0, envelope(VALID), "")])
    config = default_config(llm__provider="claude-subscription")
    config.set("llm.claude_cli_path", str(tmp_path / "claude"))
    (tmp_path / "claude").write_text("#!/bin/sh\n")
    client = ClaudeCliClient(config, runner=runner)

    result = client.complete_json(system_blocks=BLOCKS, user="**[00:12] ME:** hello")
    assert result.data["title"] == "Weekly sync"
    call = runner.calls[0]  # type: ignore[attr-defined]
    assert "-p" in call["args"] and "--output-format" in call["args"]
    assert call["args"][call["args"].index("--output-format") + 1] == "json"
    # the transcript is piped, never passed as an argument
    assert call["stdin"] == "**[00:12] ME:** hello"
    assert "**[00:12] ME:** hello" not in " ".join(call["args"])


def test_claude_cli_disables_tools_by_default(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Summarizing a transcript has no business reading or writing the user's disk."""
    (tmp_path / "claude").write_text("#!/bin/sh\n")
    config = default_config()
    config.set("llm.claude_cli_path", str(tmp_path / "claude"))
    args = ClaudeCliClient(config).build_args(str(tmp_path / "claude"))
    assert "--disallowed-tools" in args
    denied = args[args.index("--disallowed-tools") + 1]
    for tool in ("Bash", "Read", "Write", "Edit", "WebFetch"):
        assert tool in denied
    assert "--dangerously-skip-permissions" not in args


def test_claude_cli_reports_a_missing_install() -> None:
    config = default_config()
    config.set("llm.claude_cli_path", "definitely-not-installed-xyz")
    client = ClaudeCliClient(config)
    assert client.status().installed is False
    with pytest.raises(PermanentError, match="not installed"):
        client.complete_json(system_blocks=BLOCKS, user="x")


def test_claude_cli_maps_not_signed_in(tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "claude").write_text("#!/bin/sh\n")
    config = default_config()
    config.set("llm.claude_cli_path", str(tmp_path / "claude"))
    client = ClaudeCliClient(
        config, runner=fake_runner([(1, "", "Not logged in. Please run /login")])
    )
    with pytest.raises(PermanentError, match="not signed in"):
        client.complete_json(system_blocks=BLOCKS, user="x")


def test_claude_cli_maps_rate_limit(tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "claude").write_text("#!/bin/sh\n")
    config = default_config()
    config.set("llm.claude_cli_path", str(tmp_path / "claude"))
    client = ClaudeCliClient(config, runner=fake_runner([(1, "", "Usage limit reached")]))
    with pytest.raises(RecoverableError, match="rate limited"):
        client.complete_json(system_blocks=BLOCKS, user="x")


def test_claude_cli_status_reports_the_version(tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "claude").write_text("#!/bin/sh\n")
    config = default_config()
    config.set("llm.claude_cli_path", str(tmp_path / "claude"))
    client = ClaudeCliClient(config, runner=fake_runner([(0, "2.1.211 (Claude Code)\n", "")]))
    status = client.status()
    assert status.installed is True
    assert status.version == "2.1.211 (Claude Code)"
    assert status.as_dict()["installed"] is True


def test_claude_cli_unwraps_the_envelope() -> None:
    assert json.loads(ClaudeCliClient._payload(envelope(VALID)))["title"] == "Weekly sync"
    assert ClaudeCliClient._payload('{"a": 1}') == '{"a": 1}'  # not an envelope
    assert ClaudeCliClient._payload("plain text") == "plain text"
    with pytest.raises(RecoverableError):
        ClaudeCliClient._payload(json.dumps({"is_error": True, "result": "boom"}))
    with pytest.raises(RecoverableError):
        ClaudeCliClient._payload("   ")


def test_claude_cli_never_sees_a_credential(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The whole point: the app holds no token, it just runs the signed-in CLI."""
    import inspect

    from app.llm import claude_cli

    source = inspect.getsource(claude_cli)
    for marker in ("keyring", "api_key", "Authorization", "session_token", ".credentials"):
        assert marker not in source, f"{marker} must not appear in the subscription provider"
    assert "ANTHROPIC_API_KEY" in source, "the child env must have the key stripped"


# ------------------------------------------------------------------ openai


class StubOpenAI:
    def __init__(self, contents: list[str], finish: str = "stop") -> None:
        self.contents = contents
        self.finish = finish
        self.requests: list[dict[str, Any]] = []
        outer = self

        class Completions:
            def create(self, **kwargs: Any) -> Any:
                outer.requests.append(kwargs)
                message = type("M", (), {"content": outer.contents.pop(0)})()
                choice = type("C", (), {"message": message, "finish_reason": outer.finish})()
                usage = type(
                    "U", (), {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
                )()
                return type("R", (), {"choices": [choice], "usage": usage})()

        self.chat = type("Chat", (), {"completions": Completions()})()


def test_openai_requests_json_and_validates() -> None:
    stub = StubOpenAI([json.dumps(VALID)])
    client = OpenAiClient(default_config(), client=stub)
    result = client.complete_json(system_blocks=BLOCKS, user="TRANSCRIPT")
    assert result.data["title"] == "Weekly sync"
    request = stub.requests[0]
    assert request["response_format"] == {"type": "json_object"}
    assert "JSON Schema" in request["messages"][0]["content"]
    assert request["messages"][1]["content"] == "TRANSCRIPT"
    assert result.usage["total_tokens"] == 15


def test_openai_repairs_then_succeeds() -> None:
    stub = StubOpenAI(["{oops", json.dumps(VALID)])
    result = OpenAiClient(default_config(), client=stub).complete_json(
        system_blocks=BLOCKS, user="x"
    )
    assert result.attempts == 2


def test_openai_content_filter_is_permanent() -> None:
    stub = StubOpenAI([json.dumps(VALID)], finish="content_filter")
    with pytest.raises(PermanentError, match="content filter"):
        OpenAiClient(default_config(), client=stub).complete_json(system_blocks=BLOCKS, user="x")


def test_openai_without_a_key_is_actionable() -> None:
    config = default_config(secrets__backend="memory")
    with pytest.raises(PermanentError, match="no OpenAI API key"):
        OpenAiClient(config).complete_json(system_blocks=BLOCKS, user="x")


# ------------------------------------------------------------------ gemini


def gemini_transport(texts: list[str], finish: str = "STOP"):  # type: ignore[no-untyped-def]
    seen: list[tuple[str, dict[str, Any]]] = []

    def transport(path: str, payload: dict[str, Any]) -> dict[str, Any]:
        seen.append((path, payload))
        if path.endswith(":countTokens"):
            return {"totalTokens": 1234}
        return {
            "candidates": [
                {"content": {"parts": [{"text": texts.pop(0)}]}, "finishReason": finish}
            ],
            "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20},
        }

    transport.seen = seen  # type: ignore[attr-defined]
    return transport


def test_gemini_request_shape_and_validation() -> None:
    transport = gemini_transport([json.dumps(VALID)])
    client = GeminiClient(default_config(), transport=transport)
    result = client.complete_json(system_blocks=BLOCKS, user="TRANSCRIPT")
    assert result.data["title"] == "Weekly sync"
    path, payload = transport.seen[0]  # type: ignore[attr-defined]
    assert path.endswith(":generateContent")
    assert payload["generationConfig"]["responseMimeType"] == "application/json"
    assert payload["contents"][0]["parts"][0]["text"] == "TRANSCRIPT"
    assert "JSON Schema" in payload["systemInstruction"]["parts"][0]["text"]
    assert result.usage["promptTokenCount"] == 100


def test_gemini_uses_the_real_token_counter() -> None:
    transport = gemini_transport([])
    assert GeminiClient(default_config(), transport=transport).count_tokens("שלום") == 1234


def test_gemini_token_counter_falls_back() -> None:
    def broken(path: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("offline")

    assert GeminiClient(default_config(), transport=broken).count_tokens("x" * 32) == 11


def test_gemini_safety_block_is_permanent() -> None:
    def blocked(path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"promptFeedback": {"blockReason": "SAFETY"}}

    with pytest.raises(PermanentError, match="declined"):
        GeminiClient(default_config(), transport=blocked).complete_json(
            system_blocks=BLOCKS, user="x"
        )


def test_gemini_without_a_key_is_actionable() -> None:
    config = default_config(secrets__backend="memory")
    with pytest.raises(PermanentError, match=r"aistudio\.google\.com"):
        GeminiClient(config).key()


# ------------------------------------------------------------------ selection


@pytest.mark.parametrize(
    ("provider", "name"),
    [
        ("anthropic", "anthropic"),
        ("openai", "openai"),
        ("gemini", "gemini"),
        ("claude-subscription", "claude-subscription"),
        ("ollama", "ollama"),
        ("fake", "fake"),
    ],
)
def test_provider_is_selected_by_config(provider: str, name: str) -> None:
    assert make_client(default_config(llm__provider=provider)).name == name


def test_sensitive_meetings_still_force_local() -> None:
    for provider in ("anthropic", "openai", "gemini", "claude-subscription"):
        client = make_client(default_config(llm__provider=provider), sensitive=True)
        assert client.name == "ollama", "a sensitive meeting never leaves the machine"


def test_default_is_still_the_anthropic_api() -> None:
    assert default_config().get("llm.provider") == "anthropic"
    from app.errors import ConfigError

    with pytest.raises(ConfigError):
        Config({"llm": {"provider": "not-a-provider"}}).validate()
