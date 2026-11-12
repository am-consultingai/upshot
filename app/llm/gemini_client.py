"""Gemini, with an AI Studio API key — the one provider with a real free tier.

Raw HTTPS through httpx, which this build already depends on: the request shape is small
and stable, and it keeps the default install free of another SDK.
"""

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

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class ModelBusy(RecoverableError):
    """The model is overloaded or out of its per-model quota (503, 429): another one may
    answer now, and this one later. Machine B, 2026-11-12: gemini-2.5-flash answered 503
    "high demand" five times in four minutes while Flash-Lite answered in a second."""


class GeminiClient:
    name = "gemini"

    def __init__(
        self,
        config: Config,
        *,
        transport: Any = None,
        max_repairs: int = 2,
        timeout: float = 300.0,
    ) -> None:
        self.config = config
        self.model = str(config.get("llm.gemini_model", "gemini-2.5-pro"))
        self.base_url = str(config.get("llm.gemini_base_url", BASE_URL)).rstrip("/")
        self.max_repairs = max_repairs
        self.timeout = timeout
        self._transport = transport  # injected in tests: (path, payload) -> dict
        #: Models to hand over to, in order, when Gemini refuses the one that runs (a
        #: 404): set by ``make_client`` from the list, never for a model the user named.
        self.fallbacks: list[str] = []
        #: Whether a refused model may be handed over (``make_client``): when it may but
        #: there is nothing to hand over to, the refusal is retried later, not final.
        self.hands_over = False
        self._handed_over = False
        self.requests: list[dict[str, Any]] = []

    def key(self) -> str:
        key = self.config.secret("gemini", env="GEMINI_API_KEY")
        if not key:
            raise PermanentError(
                "no Gemini API key — get one free at aistudio.google.com/apikey",
                category="auth",
            )
        return key

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self._transport is not None:
            return dict(self._transport(path, payload))
        import httpx

        try:
            response = httpx.post(
                f"{self.base_url}/{path}",
                json=payload,
                headers={"x-goog-api-key": self.key()},
                timeout=self.timeout,
            )
        except Exception as exc:
            raise RecoverableError(f"Gemini request failed: {exc}") from exc
        if response.status_code == 429:
            raise ModelBusy(
                f"Gemini rate limited {self.model} (the free tier has per-minute limits)"
            )
        if response.status_code == 503:
            raise ModelBusy(f"Gemini returned 503 for {self.model}: {response.text[:200]}")
        # A key Google does not know comes back as a 400, not a 401.
        if response.status_code in (401, 403) or (
            response.status_code == 400 and "API_KEY_INVALID" in response.text
        ):
            raise PermanentError(f"Gemini rejected the key: {response.text[:200]}", category="auth")
        if response.status_code == 404:
            # The key works; the model does not, for it: Google retires models for new
            # keys first ("gemini-2.5-pro is no longer available to new users", 2026-11).
            raise PermanentError(
                f"Gemini does not offer {self.model} to this key: {response.text[:200]}",
                category="model",
            )
        if response.status_code >= 400:
            raise RecoverableError(f"Gemini returned {response.status_code}: {response.text[:200]}")
        return dict(response.json())

    def build_request(
        self, *, system: str, user: str, schema: dict[str, Any], max_tokens: int
    ) -> dict[str, Any]:
        return {
            "systemInstruction": {"parts": [{"text": f"{system}\n\n{schema_instruction(schema)}"}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "maxOutputTokens": max_tokens,
            },
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
            body = self._generate(request)
            usage.update(body.get("usageMetadata", {}) or {})
            candidates = body.get("candidates") or []
            if not candidates:
                blocked = (body.get("promptFeedback") or {}).get("blockReason")
                if blocked:
                    raise PermanentError(
                        f"Gemini declined this meeting ({blocked})", category=str(blocked)
                    )
                raise RecoverableError("Gemini returned no candidates")
            candidate = candidates[0]
            finish.append(str(candidate.get("finishReason", "")))
            if finish[-1] == "SAFETY":
                raise PermanentError(
                    "Gemini's safety filter declined this meeting", category="safety"
                )
            parts = (candidate.get("content") or {}).get("parts") or []
            return "".join(str(part.get("text", "")) for part in parts)

        data, attempts = complete_with_repair(
            send, user, schema, max_repairs=self.max_repairs, provider=self.name
        )
        return LlmResult(
            data=data,
            stop_reason=finish[-1] if finish else "STOP",
            model=self.model,
            usage=usage,
            attempts=attempts,
        )

    def _generate(self, request: dict[str, Any]) -> dict[str, Any]:
        """One generateContent, handing over to the next fallback while the model is
        refused or busy. The model that answered stays for the rest of this client's work.
        With every fallback busy, the error is the last one: the worker tries again later."""
        while True:
            try:
                return self._post(f"models/{self.model}:generateContent", request)
            except ModelBusy:
                if not self.fallbacks:
                    raise
                self._handed_over = True
                busy, self.model = self.model, self.fallbacks.pop(0)
                log.warning("Gemini is busy on %s; using %s", busy, self.model)
            except PermanentError as exc:
                if exc.category != "model":
                    raise
                if not self.fallbacks:
                    if self.hands_over and not self._handed_over:
                        # No list to hand over to yet (it failed or is not fetched): a
                        # later try may have one.
                        raise RecoverableError(
                            f"Gemini does not offer {self.model} to this key, and its model "
                            "list could not be read to choose another; trying again later"
                        ) from exc
                    raise
                self._handed_over = True
                refused, self.model = self.model, self.fallbacks.pop(0)
                log.warning("Gemini refused %s to this key; using %s", refused, self.model)

    def count_tokens(self, text: str) -> int:
        """Gemini has a real countTokens endpoint — use it, fall back to an estimate."""
        try:
            body = self._post(
                f"models/{self.model}:countTokens",
                {"contents": [{"role": "user", "parts": [{"text": text}]}]},
            )
            return int(body["totalTokens"])
        except Exception as exc:
            log.debug("countTokens unavailable (%s); estimating", exc)
            return int(len(text) / 3.2) + 1
