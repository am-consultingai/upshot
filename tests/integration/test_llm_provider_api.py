from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

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


def test_status_reports_more_than_installed(api) -> None:  # type: ignore[no-untyped-def]
    """Installed is not signed in, and the row has to be able to say which."""
    row = {p["id"]: p for p in api.client().get("/api/llm/status").json()["providers"]}[
        "claude-subscription"
    ]
    for field in (
        "signed_in",
        "path",
        "account",
        "can_install",
        "install_command",
        "install_method",
        "install_docs",
    ):
        assert field in row, f"the settings row cannot render without {field}"
    assert row["install_docs"].startswith("https://")


def test_install_reports_what_it_would_run(api) -> None:  # type: ignore[no-untyped-def]
    """The command is disclosed either way; only Windows has one to offer at all."""
    body = api.client().post("/api/llm/install", json={}).json()
    assert body["docs"].startswith("https://")
    if body["launched"]:
        assert body["command"], "a launched install must say what it ran"
    else:
        assert body["command"] == "", "nothing ran, so nothing may be claimed"


def test_a_failed_console_launch_is_logged_and_explained(api, caplog) -> None:  # type: ignore[no-untyped-def]
    """A launch that fails silently looks exactly like a button that does nothing.

    Reported as "the Install button is not responsive"; the server had raised a 500 and
    written nothing at all to the log, so there was no way to see why.
    """
    import logging

    from app.api import routes

    def refuse(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise PermissionError("[WinError 5] Access is denied")

    with caplog.at_level(logging.ERROR):
        monkey = pytest.MonkeyPatch()
        monkey.setattr(routes.sys, "platform", "win32")
        monkey.setattr("subprocess.Popen", refuse)
        try:
            with pytest.raises(HTTPException) as raised:
                routes.launch_console(["powershell.exe", "-Command", "exit"], "could not start")
        finally:
            monkey.undo()

    assert "Access is denied" in str(raised.value.detail), "the UI must be told the reason"
    assert any("console launch attempt" in record.message for record in caplog.records), (
        "and the log must carry it, with context"
    )


def test_forced_retry_marks_every_later_stage(api) -> None:  # type: ignore[no-untyped-def]
    """A fresh summary rendered from the previous summary's HTML is not a fresh summary."""
    from app.pipeline.states import JobStage

    meeting_id = api.services.meetings.create(source="manual", title="forced").id
    api.client().post(f"/api/meetings/{meeting_id}/jobs/summarize/retry?force=true")
    for stage in (JobStage.SUMMARIZE, JobStage.RENDER, JobStage.DELIVER):
        assert api.services.queue.take_rerun(meeting_id, stage), f"{stage} must be redone too"
    assert not api.services.queue.take_rerun(meeting_id, JobStage.SUMMARIZE), "consumed once"


def test_prompt_endpoint_serves_the_prompt_in_force(api) -> None:  # type: ignore[no-untyped-def]
    """The Settings box renders nothing at all without this, and says nothing about why.

    It was deleted once by an edit that replaced a range of the file, and nothing noticed:
    no test called it, so the only symptom was an empty space on the Settings screen.
    """
    body = api.client().get("/api/llm/prompt").json()
    assert body["custom"] is False
    assert "meeting-notes editor" in body["text"], "the shipped prompt, when none is set"
    assert body["text"] == body["default"]

    api.client().put("/api/settings", json={"values": {"llm.summary_prompt": "Be terse."}})
    edited = api.client().get("/api/llm/prompt").json()
    assert edited["custom"] is True
    assert edited["text"] == "Be terse."
    assert "meeting-notes editor" in edited["default"], "the default stays available to revert to"


def test_selecting_the_cli_provider_without_it_is_refused(api) -> None:  # type: ignore[no-untyped-def]
    """Otherwise the failure lands in the summarize stage, after a whole meeting."""
    client = api.client()
    api.services.config.set("llm.claude_cli_path", "definitely-not-installed-xyz")
    response = client.put("/api/settings", json={"values": {"llm.provider": "claude-subscription"}})
    assert response.status_code == 409
    assert "not installed" in response.json()["detail"]
    assert api.services.config.get("llm.provider") != "claude-subscription"


def test_provider_switch_persists(api) -> None:  # type: ignore[no-untyped-def]
    client = api.client()
    client.put("/api/settings", json={"values": {"llm.provider": "gemini"}})
    assert client.get("/api/llm/status").json()["active"] == "gemini"
    assert client.get("/api/settings").json()["config"]["llm"]["provider"] == "gemini"
