from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.fixtures.api import build_harness


@pytest.fixture
def api(tmp_path: Path, app_home: Path):  # type: ignore[no-untyped-def]
    return build_harness(tmp_path)


def test_secrets_are_write_only(api) -> None:  # type: ignore[no-untyped-def]
    client = api.client()
    assert client.get("/api/settings/secrets").json()["secrets"]["anthropic"] is False

    response = client.put(
        "/api/settings/secrets", json={"values": {"anthropic": "sk-ant-SUPERSECRET"}}
    )
    body = response.json()
    assert response.status_code == 200
    assert body["secrets"]["anthropic"] is True
    assert "SUPERSECRET" not in json.dumps(body), "a secret is never echoed back"

    # and it is nowhere in any other response either
    assert "SUPERSECRET" not in client.get("/api/settings").text
    assert "SUPERSECRET" not in client.get("/api/llm/status").text
    assert api.services.config.secrets.get("anthropic") == "sk-ant-SUPERSECRET"


def test_secrets_can_be_cleared(api) -> None:  # type: ignore[no-untyped-def]
    client = api.client()
    client.put("/api/settings/secrets", json={"values": {"gemini": "abc"}})
    assert client.get("/api/settings/secrets").json()["secrets"]["gemini"] is True
    client.put("/api/settings/secrets", json={"values": {"gemini": ""}})
    assert client.get("/api/settings/secrets").json()["secrets"]["gemini"] is False


def test_unknown_secret_is_rejected(api) -> None:  # type: ignore[no-untyped-def]
    response = api.client().put("/api/settings/secrets", json={"values": {"aws": "x"}})
    assert response.status_code == 400


def test_secrets_require_csrf(api) -> None:  # type: ignore[no-untyped-def]
    from app.api.security import CSRF_HEADER

    client = api.client()
    del client.headers[CSRF_HEADER]
    assert client.put("/api/settings/secrets", json={"values": {"gemini": "x"}}).status_code == 403


def test_llm_status_lists_every_provider(api) -> None:  # type: ignore[no-untyped-def]
    body = api.client().get("/api/llm/status").json()
    assert body["active"] == "fake"  # the test harness wires the fake
    ids = {provider["id"] for provider in body["providers"]}
    assert ids == {"anthropic", "gemini", "openai", "claude-subscription", "ollama"}
    by_id = {provider["id"]: provider for provider in body["providers"]}
    assert by_id["anthropic"]["ready"] is False
    assert by_id["gemini"]["console"].startswith("https://aistudio.google.com")
    assert "detail" in by_id["claude-subscription"]


def test_llm_status_follows_the_stored_secret(api) -> None:  # type: ignore[no-untyped-def]
    client = api.client()
    client.put("/api/settings/secrets", json={"values": {"openai": "sk-test"}})
    by_id = {p["id"]: p for p in client.get("/api/llm/status").json()["providers"]}
    assert by_id["openai"]["ready"] is True
    assert by_id["anthropic"]["ready"] is False


def test_llm_test_reports_success_with_the_fake(api) -> None:  # type: ignore[no-untyped-def]
    body = api.client().post("/api/llm/test", json={"provider": "fake"}).json()
    assert body == {"provider": "fake", "ok": True, "model": "fake"}
    assert api.services.config.get("llm.provider") == "fake", "the probe restores the setting"


def test_llm_test_reports_failure_without_a_key(api) -> None:  # type: ignore[no-untyped-def]
    body = api.client().post("/api/llm/test", json={"provider": "gemini"}).json()
    assert body["ok"] is False
    assert "aistudio" in body["error"] or "key" in body["error"].lower()
    assert api.services.config.get("llm.provider") == "fake"


def test_signin_without_the_cli_is_actionable(api) -> None:  # type: ignore[no-untyped-def]
    api.services.config.set("llm.claude_cli_path", "definitely-not-installed-xyz")
    response = api.client().post("/api/llm/signin", json={})
    assert response.status_code == 409
    assert "not installed" in response.json()["detail"]
    assert "never handles your credentials" in response.json()["detail"]


def test_provider_switch_persists(api) -> None:  # type: ignore[no-untyped-def]
    client = api.client()
    client.put("/api/settings", json={"values": {"llm.provider": "gemini"}})
    assert client.get("/api/llm/status").json()["active"] == "gemini"
    assert client.get("/api/settings").json()["config"]["llm"]["provider"] == "gemini"
