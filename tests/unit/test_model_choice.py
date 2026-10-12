from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.config import Config
from app.llm import model_choice
from app.llm.model_choice import Catalog, Listing, ModelInfo, automatic, resolve

#: What ``agy models`` printed on machine A (2026-10-12), trimmed.
AGY_MODELS = """Fetching available models...
gemini-3.8-flash-high\tGemini 3.8 Flash (High)
gemini-3.8-flash-medium\tGemini 3.8 Flash (Medium)
gemini-3.8-flash-low\tGemini 3.8 Flash (Low)
gemini-3.7-flash-low\tGemini 3.7 Flash (Low)
gemini-3.1-pro-high\tGemini 3.1 Pro (High)
claude-sonnet-5-5-low\tClaude Sonnet 5.5 (Low)
"""

CODEX_MODELS = json.dumps(
    {
        "models": [
            {"slug": "gpt-6-astra", "display_name": "GPT-6-Astra", "visibility": "list"},
            {"slug": "gpt-reserve", "display_name": "GPT-Reserve", "visibility": "hide"},
            {"slug": "gpt-6-luna", "display_name": "GPT-6-Luna", "visibility": "list"},
        ]
    }
)


def config(tmp_path: Path) -> Config:
    return Config.load(file=tmp_path / "app_config.json")


def test_agys_list_is_read_line_by_line() -> None:
    models = model_choice.parse_agy_models(AGY_MODELS)
    assert models[0] == ModelInfo("gemini-3.8-flash-high", "Gemini 3.8 Flash (High)")
    assert len(models) == 6, "the Fetching… line is not a model"


def test_codexs_catalog_keeps_only_what_it_lists_for_people() -> None:
    ids = [m.id for m in model_choice.parse_codex_models("note: x\n" + CODEX_MODELS)]
    assert ids == ["gpt-6-astra", "gpt-6-luna"]


def test_automatic_is_the_newest_flash_for_antigravity_and_the_default_for_the_rest() -> None:
    listing = Listing(models=model_choice.parse_agy_models(AGY_MODELS))
    assert automatic("antigravity-subscription", "summary", listing) == "gemini-3.8-flash-medium"
    assert automatic("antigravity-subscription", "assistant", listing) == "gemini-3.8-flash-low"
    assert automatic("antigravity-subscription", "summary", None) == "", "no list: agy decides"
    assert automatic("claude-subscription", "summary", listing) == ""


def test_a_choice_stands_while_offered_and_falls_back_when_not(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    catalog = Catalog(cfg, now=lambda: 1000.0)
    provider = "antigravity-subscription"
    cfg.set("llm.summary_models", {provider: "gemini-3.1-pro-high"})
    assert resolve(cfg, provider, "summary", catalog) == "gemini-3.1-pro-high", "no list: trusted"
    catalog._lists[provider] = Listing(
        models=model_choice.parse_agy_models(AGY_MODELS), fetched_at=1000.0
    )
    assert resolve(cfg, provider, "summary", catalog) == "gemini-3.1-pro-high"
    cfg.set("llm.summary_models", {provider: "gemini-2.0-gone"})
    assert resolve(cfg, provider, "summary", catalog) == "gemini-3.8-flash-medium", (
        "a model the provider dropped falls back to automatic, never fails a summary"
    )
    assert resolve(cfg, provider, "assistant", catalog) == "gemini-3.8-flash-low"


class FakeCli:
    def __init__(self, path: str | None) -> None:
        self.path = path

    def resolve(self) -> str | None:
        return self.path


def test_the_catalog_asks_each_cli_and_keeps_the_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def run(args: list[str], timeout: float) -> tuple[int, str, str]:
        calls.append(args)
        return 0, AGY_MODELS if args[1] == "models" else CODEX_MODELS, ""

    monkeypatch.setattr(model_choice, "_cli_client", lambda provider, cfg: FakeCli("cli"))
    clock = {"now": 1000.0}
    catalog = Catalog(config(tmp_path), run=run, now=lambda: clock["now"])
    assert len(catalog.listing("antigravity-subscription").models) == 6
    assert catalog.listing("codex-subscription").models[0].id == "gpt-6-astra"
    claude = catalog.listing("claude-subscription")
    assert claude.source == "fixed" and [m.id for m in claude.models] == ["opus", "sonnet", "haiku"]
    assert calls == [["cli", "models"], ["cli", "debug", "models"]]
    catalog.listing("antigravity-subscription")
    assert len(calls) == 2, "a fresh list is not asked for again"
    clock["now"] += model_choice.FRESH_S + 1
    catalog.listing("antigravity-subscription")
    assert len(calls) == 3, "a stale one is"


def test_a_provider_that_cannot_answer_has_an_empty_list_and_a_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(model_choice, "_cli_client", lambda provider, cfg: FakeCli(None))
    catalog = Catalog(config(tmp_path))
    listing = catalog.listing("antigravity-subscription")
    assert listing.models == [] and listing.error == "not installed"
    assert catalog.listing("gemini").error == "no key saved"


class Http:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.urls: list[str] = []

    def get(self, url: str, **kwargs: Any) -> Any:
        self.urls.append(url)
        payload = self.payload

        class Response:
            def json(self) -> dict[str, Any]:
                return payload

        return Response()


def test_the_api_providers_lists_come_from_their_own_apis(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    cfg.set("secrets.backend", "memory")
    cfg.secrets.set("gemini", "k")
    http = Http(
        {
            "models": [
                {
                    "name": "models/gemini-3-pro",
                    "displayName": "Gemini 3 Pro",
                    "supportedGenerationMethods": ["generateContent"],
                },
                {"name": "models/embed", "supportedGenerationMethods": ["embedContent"]},
            ]
        }
    )
    listing = Catalog(cfg, http=http).listing("gemini")
    assert listing.models == [ModelInfo("gemini-3-pro", "Gemini 3 Pro")], "only what can answer"
    assert http.urls[0].endswith("/models?pageSize=200")


def test_the_chosen_model_reaches_every_cli(tmp_path: Path) -> None:
    from app.llm.client import make_client

    cfg = config(tmp_path)
    cfg.set("llm.summary_models", {"claude-subscription": "sonnet", "codex-subscription": "x"})
    cfg.set("llm.assistant_models", {"claude-subscription": "haiku"})
    model_choice.set_catalog(None)
    claude = make_client(cfg, provider="claude-subscription")
    assert claude.model == "sonnet"  # type: ignore[attr-defined]
    assert claude.build_args("claude")[-2:] == ["--model", "sonnet"]  # type: ignore[attr-defined]
    assert make_client(cfg, provider="claude-subscription", use="assistant").model == "haiku"  # type: ignore[attr-defined]
    assert make_client(cfg, provider="codex-subscription").model == "x"  # type: ignore[attr-defined]
    plain = make_client(cfg, provider="antigravity-subscription")
    assert plain.model == "", "nothing chosen and no list: the CLI's own default"  # type: ignore[attr-defined]


def test_the_conversation_runs_its_own_model_on_every_route(tmp_path: Path) -> None:
    from app.assistant.antigravity_route import AntigravityRoute
    from app.assistant.claude_route import ClaudeRoute
    from app.assistant.codex_route import CodexRoute

    cfg = config(tmp_path)
    cfg.set("llm.summary_models", {"claude-subscription": "opus"})
    cfg.set(
        "llm.assistant_models",
        {
            "claude-subscription": "haiku",
            "codex-subscription": "gpt-6-luna",
            "antigravity-subscription": "gemini-3.8-flash-low",
        },
    )
    model_choice.set_catalog(None)
    claude = ClaudeRoute(cfg).args(["claude"], mcp_config="{}", system="s", resume="")
    assert claude[claude.index("--model") + 1] == "haiku", "the conversation's, not the summary's"
    codex = CodexRoute(cfg).codex_args(["codex"], port=1)
    assert codex[codex.index("--model") + 1] == "gpt-6-luna" and codex[-1] == "-"
    agy = AntigravityRoute(cfg).agy_args(["agy"])
    assert agy[agy.index("--model") + 1] == "gemini-3.8-flash-low"
    cfg.set("llm.assistant_models", {})
    assert "--model" not in ClaudeRoute(cfg).args(["c"], mcp_config="{}", system="s", resume="")
