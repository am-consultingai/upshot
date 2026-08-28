"""One protocol, three implementations: Anthropic, Ollama (sensitive meetings), and a fake.

Which one is wired is a config key, so the pipeline test exercises the real wiring.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.config import Config
from app.errors import PermanentError, RecoverableError
from app.llm.schema import NOTES_SCHEMA, ValidationError, validate
from app.log import get

log = get(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class LlmResult:
    data: dict[str, Any]
    stop_reason: str = "end_turn"
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    attempts: int = 1

    @property
    def cache_read_tokens(self) -> int:
        return int(self.usage.get("cache_read_input_tokens", 0) or 0)


@runtime_checkable
class LlmClient(Protocol):
    name: str

    def complete_json(
        self,
        *,
        system_blocks: Sequence[dict[str, Any]],
        user: str,
        schema: dict[str, Any],
        max_tokens: int = 16000,
    ) -> LlmResult: ...

    def count_tokens(self, text: str) -> int: ...


def system_blocks(system_prompt: str, glossary_block: str | None) -> list[dict[str, Any]]:
    """Stable prefix first, each with a cache breakpoint; volatile content never here."""
    blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    if glossary_block:
        blocks.append(
            {
                "type": "text",
                "text": glossary_block,
                "cache_control": {"type": "ephemeral"},
            }
        )
    return blocks


class AnthropicClient:
    """Claude. Structured output against the schema, cached prefix, fallbacks on."""

    name = "anthropic"

    def __init__(self, config: Config, *, client: Any = None) -> None:
        self.config = config
        self.model = str(config.get("llm.model", "claude-opus-5"))
        self.effort = str(config.get("llm.effort", "high"))
        self._client = client
        self.requests: list[dict[str, Any]] = []

    # -- transport ---------------------------------------------------------

    def client(self) -> Any:
        if self._client is None:
            import anthropic

            key = self.config.secret("anthropic", env="ANTHROPIC_API_KEY")
            self._client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
        return self._client

    def build_request(
        self,
        *,
        system_blocks: Sequence[dict[str, Any]],
        user: str,
        schema: dict[str, Any],
        max_tokens: int,
    ) -> dict[str, Any]:
        """The exact kwargs sent to the API. Separated so a test can inspect them."""
        return {
            "model": self.model,
            "max_tokens": max_tokens,
            "thinking": {"type": "adaptive"},
            "output_config": {
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            "betas": [FALLBACK_BETA],
            "fallbacks": "default",
            "system": list(system_blocks),
            # Volatile: after the last cache breakpoint, always.
            "messages": [{"role": "user", "content": user}],
        }

    # -- protocol ----------------------------------------------------------

    def complete_json(
        self,
        *,
        system_blocks: Sequence[dict[str, Any]],
        user: str,
        schema: dict[str, Any] = NOTES_SCHEMA,
        max_tokens: int = 16000,
    ) -> LlmResult:
        request = self.build_request(
            system_blocks=system_blocks, user=user, schema=schema, max_tokens=max_tokens
        )
        self.requests.append(request)
        response = self.client().beta.messages.create(**request)
        return self.interpret(response, schema)

    def interpret(self, response: Any, schema: dict[str, Any]) -> LlmResult:
        """Always check ``stop_reason`` before reading content."""
        stop_reason = str(getattr(response, "stop_reason", "end_turn"))
        if stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = str(getattr(details, "category", None) or "unknown")
            raise PermanentError(
                f"the model declined to summarize this meeting ({category})",
                category=category,
            )
        if stop_reason == "max_tokens":
            raise RecoverableError("the summary hit max_tokens; retry with a smaller window")
        text = _first_text(response)
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise RecoverableError(f"model returned non-JSON output: {exc}") from exc
        validate(payload, schema)
        return LlmResult(
            data=payload,
            stop_reason=stop_reason,
            model=str(getattr(response, "model", self.model)),
            usage=_usage(response),
        )

    def count_tokens(self, text: str) -> int:
        response = self.client().messages.count_tokens(
            model=self.model, messages=[{"role": "user", "content": text}]
        )
        return int(response.input_tokens)


def _first_text(response: Any) -> str:
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "text":
            return str(block.text)
    raise RecoverableError("no text block in the response")


def _usage(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    if isinstance(usage, dict):
        return dict(usage)
    return {
        key: getattr(usage, key)
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
        if getattr(usage, key, None) is not None
    }


Transport = Callable[[dict[str, Any]], str]


class OllamaClient:
    """The sensitive-meeting path: nothing leaves the machine.

    Local models honour schemas less reliably, so an invalid payload is fed back with the
    validation error attached — up to ``max_repairs`` times — before giving up.
    """

    name = "ollama"

    def __init__(
        self,
        config: Config,
        *,
        transport: Transport | None = None,
        max_repairs: int = 2,
    ) -> None:
        self.config = config
        self.url = str(config.get("llm.ollama_url", "http://127.0.0.1:11434")).rstrip("/")
        self.model = str(config.get("llm.local_model", "dictalm3-nemotron-12b"))
        self.max_repairs = max_repairs
        self._transport = transport
        self.requests: list[dict[str, Any]] = []

    def _post(self, payload: dict[str, Any]) -> str:
        if self._transport is not None:
            return self._transport(payload)
        import httpx

        response = httpx.post(f"{self.url}/api/chat", json=payload, timeout=600.0)
        response.raise_for_status()
        body = response.json()
        return str(body.get("message", {}).get("content", ""))

    def complete_json(
        self,
        *,
        system_blocks: Sequence[dict[str, Any]],
        user: str,
        schema: dict[str, Any] = NOTES_SCHEMA,
        max_tokens: int = 16000,
    ) -> LlmResult:
        system_text = "\n\n".join(str(block.get("text", "")) for block in system_blocks)
        message = user
        last_error = ""
        for attempt in range(self.max_repairs + 1):
            payload = {
                "model": self.model,
                "format": "json",
                "stream": False,
                "options": {"num_predict": max_tokens},
                "messages": [
                    {"role": "system", "content": system_text},
                    {"role": "user", "content": message},
                ],
            }
            self.requests.append(payload)
            raw = self._post(payload)
            try:
                data = json.loads(raw)
                validate(data, schema)
            except (ValueError, ValidationError) as exc:
                last_error = str(exc)
                log.warning(
                    "local model returned invalid output (attempt %d): %s", attempt + 1, exc
                )
                message = (
                    f"{user}\n\nYour previous answer was rejected: {last_error}\n"
                    "Return only JSON that satisfies the schema."
                )
                continue
            return LlmResult(data=data, model=self.model, attempts=attempt + 1)
        raise PermanentError(
            f"local model could not produce schema-valid JSON after "
            f"{self.max_repairs + 1} attempts: {last_error}",
            category="schema",
        )

    def count_tokens(self, text: str) -> int:
        # No tokenizer endpoint: a conservative Hebrew-aware estimate.
        return int(len(text) / 2.5) + 1


WORD_RE = re.compile(r"[\w'֐-׿-]+", re.UNICODE)


class FakeLlm:
    """Deterministic notes derived from the transcript. No network, no key, no GPU."""

    name = "fake"

    def __init__(
        self,
        *,
        action_items: int = 2,
        tldr_items: int = 3,
        chars_per_token: float = 4.0,
        unknown_owner: bool = False,
    ) -> None:
        self.action_items = action_items
        self.tldr_items = tldr_items
        self.chars_per_token = chars_per_token
        self.unknown_owner = unknown_owner
        self.calls: list[dict[str, Any]] = []

    def complete_json(
        self,
        *,
        system_blocks: Sequence[dict[str, Any]],
        user: str,
        schema: dict[str, Any] = NOTES_SCHEMA,
        max_tokens: int = 16000,
    ) -> LlmResult:
        self.calls.append({"system": list(system_blocks), "user": user, "schema": schema})
        reduced = schema is not NOTES_SCHEMA and "title" not in schema.get("properties", {})
        lines = [line for line in user.splitlines() if line.strip()]
        first = lines[0][:110] if lines else "Meeting"
        at_ms = _first_at_ms(user)
        topics = [
            {
                "heading": "Status",
                "points": [line[:160] for line in lines[:3]] or ["no content"],
                "quotes": [{"who": "THEM", "text": first, "at_ms": at_ms}],
            }
        ]
        decisions = [
            {
                "what": "Ship the release next week",
                "rationale": "the blocking bug is fixed",
                "who_decided": "ME",
                "at_ms": at_ms,
            }
        ]
        actions = [
            {
                "who": "ME" if index % 2 == 0 else "THEM",
                "what": f"follow up #{index + 1}",
                "due": None,
                "confidence": 0.8,
            }
            for index in range(self.action_items)
        ]
        if self.unknown_owner and actions:
            actions[0] = {**actions[0], "who": "Someone Not In This Meeting"}
        if reduced:
            data: dict[str, Any] = {
                "topics": topics,
                "decisions": decisions,
                "action_items": actions,
                "quotes": [{"who": "THEM", "text": first, "at_ms": at_ms}],
            }
        else:
            data = {
                "title": (first[:60] or "Meeting").strip(),
                "tldr": [f"point {index + 1}" for index in range(self.tldr_items)],
                "participants": [{"name": "ME", "track": "ME"}, {"name": "THEM", "track": "THEM"}],
                "topics": topics,
                "decisions": decisions,
                "action_items": actions,
                "open_questions": [],
                "risks": [],
                "follow_up_email": {
                    "subject": f"Notes: {first[:40]}".strip(),
                    "body_md": "- point 1\n- point 2\n",
                },
            }
        return LlmResult(
            data=data,
            model="fake",
            usage={"input_tokens": self.count_tokens(user), "cache_read_input_tokens": 1},
        )

    def count_tokens(self, text: str) -> int:
        return int(len(text) / self.chars_per_token) + 1


def _first_at_ms(text: str) -> int:
    match = re.search(r"\[(\d+):(\d\d)\]", text)
    if not match:
        return 0
    return (int(match.group(1)) * 60 + int(match.group(2))) * 1000


def make_client(config: Config, *, sensitive: bool = False) -> LlmClient:
    """Which LLM is wired. A sensitive meeting always routes to the local model."""
    provider = "ollama" if sensitive else str(config.get("llm.provider", "anthropic"))
    if provider == "fake":
        return FakeLlm()
    if provider == "ollama":
        return OllamaClient(config)
    return AnthropicClient(config)
