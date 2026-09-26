"""One protocol, many implementations: Anthropic, Ollama (sensitive meetings), a fake, and
the others ``make_client`` names.

Which one is wired is a config key, so the pipeline test exercises the real wiring.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol, runtime_checkable

from app.config import Config
from app.due import resolve_due
from app.errors import PermanentError, RecoverableError
from app.llm.repair import complete_with_repair
from app.llm.schema import FREE_SCHEMA, validate
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
        schema: dict[str, Any] = FREE_SCHEMA,
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
        schema: dict[str, Any] = FREE_SCHEMA,
        max_tokens: int = 16000,
    ) -> LlmResult:
        system_text = "\n\n".join(str(block.get("text", "")) for block in system_blocks)

        def send(message: str) -> str:
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
            return self._post(payload)

        data, attempts = complete_with_repair(
            send, user, schema, max_repairs=self.max_repairs, provider=self.name
        )
        return LlmResult(data=data, model=self.model, attempts=attempts)

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
        schema: dict[str, Any] = FREE_SCHEMA,
        max_tokens: int = 16000,
    ) -> LlmResult:
        """A believable free-form answer, and nothing structured to invent any more."""
        self.calls.append({"system": list(system_blocks), "user": user, "schema": schema})
        properties = schema.get("properties", {})
        if "answer" in properties:
            return LlmResult(data=_fake_answer(user), model="fake")
        if "summary_html" not in properties:
            # An arbitrary small schema — the /api/llm/test probe uses one. Satisfy it
            # rather than returning a summary that would fail validation.
            return LlmResult(data=_minimal_for(schema), model="fake")
        # The meeting details lead the message (the date always does); the fake's title
        # and body come from what was said, as a real model's would.
        spoken = user.split("Transcript:\n", 1)[-1]
        lines = [line for line in spoken.splitlines() if line.strip()]
        first = lines[0][:110] if lines else "Meeting"
        body = "".join(f"<p>{line[:200]}</p>" for line in lines[:5])
        # Opens with a lead sentence and no heading, as the shipped prompt (v6) asks, so
        # the page's treatment of that first paragraph is exercised by the demo.
        lead = f"<p>The meeting settled on a follow-up about {first[:80]}.</p>"
        data: dict[str, Any] = {"summary_html": f"{lead}{body}", "title": first}
        # Two believable commitments, so the demo and the e2e specs exercise the inbox
        # rather than an empty screen. Only when the caller asked for them: the probe
        # schemas above must keep getting exactly what they requested.
        if "action_items" in properties:
            anchor = _meeting_date(user)
            due_at = resolve_due("this week", anchor) if anchor else None
            data["action_items"] = [
                {
                    "who": "ME",
                    "what": f"follow up on {first[:60]}",
                    "due": "this week",
                    "detail": "so the numbers are in before the review",
                    "due_at": due_at.isoformat() if due_at else None,
                },
                {"who": "THEM", "what": "send the numbers we agreed"},
            ]
        if "chapters" in properties:
            data["chapters"] = _fake_chapters(user)
        return LlmResult(data=data, model="fake")

    def count_tokens(self, text: str) -> int:
        return int(len(text) / self.chars_per_token) + 1


def _minimal_for(schema: dict[str, Any]) -> dict[str, Any]:
    """The smallest object satisfying a schema's required properties."""
    defaults: dict[str, Any] = {
        "boolean": True,
        "string": "ok",
        "integer": 1,
        "number": 1.0,
        "array": [],
        "object": {},
    }
    properties = schema.get("properties", {})
    out: dict[str, Any] = {}
    for name in schema.get("required", []):
        spec = properties.get(name, {})
        kind = spec.get("type", "string")
        if isinstance(kind, list):
            kind = kind[0]
        out[name] = defaults.get(str(kind), "ok")
    return out


STAMP_RE = re.compile(r"\[(\d+):(\d\d)\]")
DATE_LINE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def _meeting_date(user: str) -> date | None:
    """The meeting's date from the details block, which always carries one (D50)."""
    for line in user.splitlines():
        if line.startswith("Date:"):
            match = DATE_LINE_RE.search(line)
            if match:
                return date.fromisoformat(match.group(1))
    return None


def _fake_chapters(user: str) -> list[dict[str, Any]]:
    """Two or three sections cut at the transcript's own timestamps."""
    stamped: list[tuple[int, str]] = []
    for line in user.splitlines():
        match = STAMP_RE.search(line)
        if match:
            at_ms = (int(match.group(1)) * 60 + int(match.group(2))) * 1000
            text = line[match.end() :].replace("*", "").split(":", 1)[-1].strip()
            stamped.append((at_ms, text))
    if len(stamped) < 2:
        return [{"title": "The conversation", "start_ms": 0, "end_ms": None}]
    count = 3 if len(stamped) >= 6 else 2
    size = -(-len(stamped) // count)
    groups = [stamped[i : i + size] for i in range(0, len(stamped), size)]
    out: list[dict[str, Any]] = []
    for index, group in enumerate(groups):
        words = " ".join(group[0][1].split()[:5]) or f"Part {index + 1}"
        following = groups[index + 1][0][0] if index + 1 < len(groups) else None
        out.append({"title": words, "start_ms": group[0][0], "end_ms": following})
    out[0]["start_ms"] = 0
    return out


ASK_LINE_RE = re.compile(r"^\[(\d+):(\d\d)\]\s*([^:]+):\s*(.+)$")
ASK_MEETING_RE = re.compile(r"^=== Meeting (\S+)")


def _fake_answer(user: str) -> dict[str, Any]:
    """The transcript line sharing most words with the question, cited to its moment.

    Deterministic and real enough for an end-to-end test to assert on: ask about a
    sentence and the answer is that sentence, with a citation that seeks to it.
    """
    question = ""
    for line in user.splitlines():
        if line.startswith("Question:"):
            question = line.split(":", 1)[1]
    asked = {w.casefold() for w in WORD_RE.findall(question) if len(w) > 2}
    best: tuple[int, str, int, str] | None = None
    meeting_id = ""
    for line in user.splitlines():
        header = ASK_MEETING_RE.match(line)
        if header:
            meeting_id = header.group(1)
            continue
        match = ASK_LINE_RE.match(line.strip())
        if not match:
            continue
        text = match.group(4).strip()
        overlap = len(asked & {w.casefold() for w in WORD_RE.findall(text)})
        at_ms = (int(match.group(1)) * 60 + int(match.group(2))) * 1000
        if best is None or overlap > best[0]:
            best = (overlap, text, at_ms, meeting_id)
    if best is None or best[0] == 0:
        return {"answer": "The transcript does not say.", "citations": []}
    _, text, at_ms, cited = best
    citation: dict[str, Any] = {"at_ms": at_ms}
    if cited:
        citation["meeting_id"] = cited
    return {"answer": text, "citations": [citation]}


def _first_at_ms(text: str) -> int:
    match = re.search(r"\[(\d+):(\d\d)\]", text)
    if not match:
        return 0
    return (int(match.group(1)) * 60 + int(match.group(2))) * 1000


def make_client(
    config: Config, *, sensitive: bool = False, provider: str | None = None
) -> LlmClient:
    """Which LLM is wired. A sensitive meeting always routes to the local model.

    ``provider`` overrides the configured one — the summarize stage passes
    ``llm.fallback_provider`` here when a plan's allowance is spent — and loses to
    ``sensitive`` like everything else does.
    """
    if not sensitive and provider is None:
        provider = str(config.get("llm.provider", "anthropic"))
    provider = "ollama" if sensitive else str(provider)
    if provider == "none":
        # Transcripts only (D63). The pipeline never asks for a summary then; this is for
        # everything else that wants a model — Ask, the assistant — to say so plainly.
        raise PermanentError(
            "No AI provider is set up, so this needs one first: choose it in Settings, AI agents."
        )
    if provider == "fake":
        return FakeLlm()
    if provider == "ollama":
        return OllamaClient(config)
    if provider == "openai":
        from app.llm.openai_client import OpenAiClient

        return OpenAiClient(config)
    if provider == "gemini":
        from app.llm.gemini_client import GeminiClient

        return GeminiClient(config)
    if provider == "claude-subscription":
        from app.llm.claude_cli import ClaudeCliClient

        return ClaudeCliClient(config)
    if provider == "codex-subscription":
        from app.llm.codex_cli import CodexCliClient

        return CodexCliClient(config)
    return AnthropicClient(config)
