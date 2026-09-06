from __future__ import annotations

import json
from itertools import pairwise
from typing import Any

import pytest

from app.config import default_config
from app.errors import PermanentError, RecoverableError
from app.llm.client import AnthropicClient, FakeLlm, OllamaClient, make_client, system_blocks
from app.llm.prompts import load as load_prompt
from app.llm.prompts import versions
from app.llm.schema import FREE_SCHEMA, ValidationError, validate
from app.llm.tokens import CachingCounter, CharCounter, tokens_per_word
from app.pipeline.stages.summarize import language_instruction, split_windows

VALID_NOTES: dict[str, Any] = {
    "title": "Weekly sync",
    "summary_html": "<h1>Weekly sync</h1><p>we shipped, we did not break anything</p>",
}


# ------------------------------------------------------------------ schema


def test_schema_accepts_a_good_payload() -> None:
    assert validate(dict(VALID_NOTES)) == VALID_NOTES


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param(lambda n: n.pop("summary_html"), id="missing_the_document"),
        pytest.param(lambda n: n.update(summary_html=""), id="empty_document"),
        pytest.param(lambda n: n.update(summary_html=["not", "a", "string"]), id="wrong_type"),
        pytest.param(lambda n: n.update(topics=[]), id="a_field_from_the_old_schema"),
    ],
)
def test_schema_rejects_malformed(mutation) -> None:  # type: ignore[no-untyped-def]
    """All that is still enforced is the envelope: one non-empty document, and no more.

    The nine-field schema this used to police is gone. It fixed the sections, so a prompt
    asking for a differently shaped document could have no visible effect at all.
    """
    notes = json.loads(json.dumps(VALID_NOTES))
    mutation(notes)
    with pytest.raises(ValidationError):
        validate(notes)


# ------------------------------------------------------------------ windowing


def test_window_split_by_tokens() -> None:
    """With 1 char == 1 token, a 20 000-char transcript makes 4 overlapping windows."""
    text = "".join(chr(ord("a") + (index % 26)) for index in range(20_000))
    counter = CharCounter(1.0)
    windows = split_windows(text, counter, target_tokens=6000, overlap_tokens=300)
    assert len(windows) == 4
    assert [w.end - w.start for w in windows] == [6000, 6000, 6000, 2900]
    assert [w.start for w in windows] == [0, 5700, 11400, 17100]
    for previous, current in pairwise(windows):
        assert previous.end - current.start == 300, "300 tokens of overlap"
    # nothing is lost: concatenate, dropping each window's overlap with the previous one
    rebuilt = windows[0].text + "".join(w.text[300:] for w in windows[1:])
    assert rebuilt == text


def test_single_window_when_short() -> None:
    windows = split_windows("short transcript", CharCounter(1.0), target_tokens=6000)
    assert len(windows) == 1 and windows[0].text == "short transcript"
    assert split_windows("", CharCounter(1.0)) == []


def test_token_counter_cache() -> None:
    counter = CachingCounter(CharCounter(1.0).count)
    assert counter.count("abc") == 3
    assert counter.count("abc") == 3
    assert counter.calls == 1 and counter.hits == 1


def test_tokens_per_word() -> None:
    assert tokens_per_word("one two three", CharCounter(1.0)) == pytest.approx(13 / 3)
    assert tokens_per_word("", CharCounter(1.0)) == 0.0


# ------------------------------------------------------------------ the request


def test_cache_control_placement() -> None:
    client = AnthropicClient(default_config(), client=object())
    blocks = system_blocks("SYSTEM PROMPT", "GLOSSARY BLOCK")
    request = client.build_request(
        system_blocks=blocks, user="VOLATILE TRANSCRIPT", schema=FREE_SCHEMA, max_tokens=16000
    )
    assert all("cache_control" in block for block in request["system"])
    assert request["system"][-1]["text"] == "GLOSSARY BLOCK"
    assert request["messages"] == [{"role": "user", "content": "VOLATILE TRANSCRIPT"}]
    assert "cache_control" not in json.dumps(request["messages"])
    assert request["thinking"] == {"type": "adaptive"}
    assert request["output_config"]["format"]["schema"] is FREE_SCHEMA
    assert request["output_config"]["effort"] == "high"
    assert request["fallbacks"] == "default"
    assert request["betas"] == ["server-side-fallback-2026-07-01"]


class Response:
    def __init__(self, stop_reason: str = "end_turn", text: str = "{}", category: str = "") -> None:
        self.stop_reason = stop_reason
        self.model = "claude-opus-5"
        self.content = [type("Block", (), {"type": "text", "text": text})()]
        self.usage = {"input_tokens": 10, "cache_read_input_tokens": 7}
        self.stop_details = type("Details", (), {"category": category})() if category else None


def test_refusal_is_permanent() -> None:
    client = AnthropicClient(default_config(), client=object())
    with pytest.raises(PermanentError) as info:
        client.interpret(Response("refusal", category="cyber"), FREE_SCHEMA)
    assert info.value.category == "cyber"


def test_max_tokens_is_recoverable() -> None:
    client = AnthropicClient(default_config(), client=object())
    with pytest.raises(RecoverableError):
        client.interpret(Response("max_tokens"), FREE_SCHEMA)


def test_non_json_is_recoverable() -> None:
    client = AnthropicClient(default_config(), client=object())
    with pytest.raises(RecoverableError):
        client.interpret(Response(text="I'm afraid I can't do that"), FREE_SCHEMA)


def test_invalid_payload_raises_validation_error() -> None:
    client = AnthropicClient(default_config(), client=object())
    with pytest.raises(ValidationError):
        client.interpret(Response(text=json.dumps({"title": "x"})), FREE_SCHEMA)


def test_good_response_is_parsed() -> None:
    client = AnthropicClient(default_config(), client=object())
    result = client.interpret(Response(text=json.dumps(VALID_NOTES)), FREE_SCHEMA)
    assert result.data["title"] == "Weekly sync"
    assert result.cache_read_tokens == 7


# ------------------------------------------------------------------ ollama


def test_ollama_repair_loop() -> None:
    replies = ["{not json", json.dumps(VALID_NOTES)]

    def transport(payload: dict[str, Any]) -> str:
        return replies.pop(0)

    client = OllamaClient(default_config(), transport=transport)
    result = client.complete_json(system_blocks=system_blocks("s", None), user="transcript")
    assert result.attempts == 2
    assert result.data["title"] == "Weekly sync"
    assert "rejected" in client.requests[1]["messages"][1]["content"]
    assert client.requests[0]["format"] == "json"


def test_ollama_gives_up_after_three_attempts() -> None:
    def transport(payload: dict[str, Any]) -> str:
        return "{still not json"

    client = OllamaClient(default_config(), transport=transport)
    with pytest.raises(PermanentError):
        client.complete_json(system_blocks=system_blocks("s", None), user="transcript")
    assert len(client.requests) == 3


def test_sensitive_meetings_route_to_ollama() -> None:
    assert make_client(default_config(), sensitive=True).name == "ollama"
    assert make_client(default_config(llm__provider="fake")).name == "fake"
    assert make_client(default_config()).name == "anthropic"


# ------------------------------------------------------------------ prompts


def test_prompt_version_front_matter() -> None:
    prompt = load_prompt("system")
    assert prompt.version and prompt.version != "0"
    assert "version:" not in prompt.text
    assert prompt.text
    assert set(versions("system")) == {"system"}


def test_output_language_in_prompt() -> None:
    assert "English (en)" in language_instruction("en")
    assert "Hebrew (he)" in language_instruction("he")
    assert "verbatim" in language_instruction("he")


def test_fake_llm_is_schema_valid() -> None:
    fake = FakeLlm()
    result = fake.complete_json(system_blocks=system_blocks("s", None), user="**[00:12] ME:** hi")
    validate(result.data)
    assert "<h1>" in result.data["summary_html"]
