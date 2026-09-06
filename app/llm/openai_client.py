"""OpenAI, with an API key. A ChatGPT subscription does not grant API access."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.config import Config
from app.errors import PermanentError, RecoverableError
from app.llm.client import LlmResult
from app.llm.repair import complete_with_repair, schema_instruction
from app.llm.schema import FREE_SCHEMA
from app.log import get

log = get(__name__)


def _usage(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    if isinstance(usage, dict):
        return dict(usage)
    return {
        key: getattr(usage, key)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        if getattr(usage, key, None) is not None
    }


class OpenAiClient:
    """Chat Completions in JSON mode, then validated locally.

    `json_object` rather than a strict `json_schema` response format: our schema does not
    list every property in `required`, which strict mode demands, and this keeps the
    provider usable against any OpenAI-compatible endpoint (`llm.openai_base_url`).
    """

    name = "openai"

    def __init__(self, config: Config, *, client: Any = None, max_repairs: int = 2) -> None:
        self.config = config
        self.model = str(config.get("llm.openai_model", "gpt-5"))
        self.base_url = config.get("llm.openai_base_url") or None
        self.max_repairs = max_repairs
        self._client = client
        self.requests: list[dict[str, Any]] = []

    def client(self) -> Any:
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise PermanentError(
                    "the OpenAI provider needs `uv sync --extra openai`", category="setup"
                ) from exc
            key = self.config.secret("openai", env="OPENAI_API_KEY")
            if not key:
                raise PermanentError("no OpenAI API key — add one in Settings", category="auth")
            self._client = OpenAI(api_key=key, base_url=self.base_url)
        return self._client

    def build_request(
        self, *, system: str, user: str, schema: dict[str, Any], max_tokens: int
    ) -> dict[str, Any]:
        return {
            "model": self.model,
            "max_completion_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": f"{system}\n\n{schema_instruction(schema)}"},
                {"role": "user", "content": user},
            ],
        }

    def complete_json(
        self,
        *,
        system_blocks: Sequence[dict[str, Any]],
        user: str,
        schema: dict[str, Any] = FREE_SCHEMA,
        max_tokens: int = 16000,
    ) -> LlmResult:
        system = "\n\n".join(str(block.get("text", "")) for block in system_blocks)
        usage: dict[str, Any] = {}
        finish: list[str] = []

        def send(message: str) -> str:
            request = self.build_request(
                system=system, user=message, schema=schema, max_tokens=max_tokens
            )
            self.requests.append(request)
            try:
                response = self.client().chat.completions.create(**request)
            except Exception as exc:
                raise self._mapped(exc) from exc
            choice = response.choices[0]
            finish.append(str(getattr(choice, "finish_reason", "")))
            usage.update(_usage(response))
            if finish[-1] == "content_filter":
                raise PermanentError(
                    "OpenAI's content filter declined this meeting", category="content_filter"
                )
            return str(choice.message.content or "")

        data, attempts = complete_with_repair(
            send, user, schema, max_repairs=self.max_repairs, provider=self.name
        )
        return LlmResult(
            data=data,
            stop_reason=finish[-1] if finish else "stop",
            model=self.model,
            usage=usage,
            attempts=attempts,
        )

    @staticmethod
    def _mapped(exc: Exception) -> Exception:
        text = f"{type(exc).__name__}: {exc}".lower()
        if "rate limit" in text or "429" in text:
            return RecoverableError(f"OpenAI rate limited: {exc}")
        if "authentication" in text or "api key" in text or "401" in text:
            return PermanentError(f"OpenAI rejected the key: {exc}", category="auth")
        return RecoverableError(f"OpenAI request failed: {exc}")

    def count_tokens(self, text: str) -> int:
        """No count endpoint; ~3.2 chars/token is conservative for mixed Hebrew/English."""
        return int(len(text) / 3.2) + 1
