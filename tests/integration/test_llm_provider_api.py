from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
    assert ids == {
        "none",
        "anthropic",
        "gemini",
        "openai",
        "claude-subscription",
        "codex-subscription",
        "antigravity-subscription",
        "ollama",
    }
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


def test_the_row_says_when_its_console_was_closed(api) -> None:  # type: ignore[no-untyped-def]
    """Closing the sign-in window must end the wait, not leave Sign in spinning.

    Found 2026-09-25: the user closed the window a login had opened (the browser it chose
    was the wrong one) and the button stayed busy, unpressable, until the page reloaded.
    """
    from app.api import routes

    class Window:
        def __init__(self) -> None:
            self.code: int | None = None

        def poll(self) -> int | None:
            return self.code

    def row() -> dict[str, Any]:
        providers = api.client().get("/api/llm/status").json()["providers"]
        by_id: dict[str, dict[str, Any]] = {p["id"]: p for p in providers}
        return by_id["codex-subscription"]

    window = Window()
    try:
        assert row()["console_open"] is False, "nothing launched yet"
        # Windows only for the launch itself: the status route has Windows paths of its own.
        with pytest.MonkeyPatch.context() as monkey:
            monkey.setattr("sys.platform", "win32")
            monkey.setattr("subprocess.Popen", lambda *_args, **_kwargs: window)
            routes.launch_console(
                ["powershell.exe", "-Command", "exit"],
                "could not start",
                provider="codex-subscription",
            )
        assert row()["console_open"] is True
        window.code = 0xC000013A  # what closing a console window leaves as the exit code
        assert row()["console_open"] is False
    finally:
        routes._CONSOLES.clear()


def test_claude_signs_out_with_its_own_command(  # type: ignore[no-untyped-def]
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``claude auth logout``, run with no window; the row then reads signed out."""
    from app.llm import claude_cli

    state = {"signed_in": True}
    sent: list[list[str]] = []

    def runner(args, _stdin, _timeout):  # type: ignore[no-untyped-def]
        sent.append(list(args))
        if args[1:] == ["auth", "logout"]:
            state["signed_in"] = False
            return 0, "Successfully logged out from your Anthropic account.", ""
        if args[1:3] == ["auth", "status"]:
            return 0, json.dumps({"loggedIn": state["signed_in"]}), ""
        return 0, "2.1.263 (Claude Code)", ""

    monkeypatch.setattr(claude_cli, "subprocess_runner", runner)
    monkeypatch.setattr(claude_cli.ClaudeCliClient, "resolve", lambda _self: "claude")
    client = api.client()
    response = client.post("/api/llm/signout", json={"provider": "claude-subscription"})
    assert response.json() == {"signed_out": True}
    assert ["claude", "auth", "logout"] in sent
    rows = {p["id"]: p for p in client.get("/api/llm/status").json()["providers"]}
    assert rows["claude-subscription"]["signed_in"] is False


def test_a_sign_out_that_does_not_take_says_so(  # type: ignore[no-untyped-def]
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.llm import claude_cli

    def runner(args, _stdin, _timeout):  # type: ignore[no-untyped-def]
        if args[1:] == ["auth", "logout"]:
            return 1, "", "keychain is locked"
        if args[1:3] == ["auth", "status"]:
            return 0, json.dumps({"loggedIn": True}), ""
        return 0, "2.1.263 (Claude Code)", ""

    monkeypatch.setattr(claude_cli, "subprocess_runner", runner)
    monkeypatch.setattr(claude_cli.ClaudeCliClient, "resolve", lambda _self: "claude")
    response = api.client().post("/api/llm/signout", json={"provider": "claude-subscription"})
    assert response.status_code == 500
    assert "keychain is locked" in response.json()["detail"]


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


def test_llm_test_never_changes_the_chosen_provider_while_it_probes(api, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The probe names its provider to the factory; the shared config is not touched.

    It used to set ``llm.provider`` to the provider under test and put the old value
    back afterwards, so a Test pressed while a new choice was still being saved restored
    the old choice over it (D59). Here the fake records what the config said *during*
    the call, while the configured provider is a different one.
    """
    from app.llm.client import FakeLlm

    seen: list[object] = []
    original = FakeLlm.complete_json

    def spying(self, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(api.services.config.get("llm.provider"))
        return original(self, **kwargs)

    monkeypatch.setattr(FakeLlm, "complete_json", spying)
    api.services.config.set("llm.provider", "ollama")
    body = api.client().post("/api/llm/test", json={"provider": "fake"}).json()
    assert body["ok"] is True
    assert seen == ["ollama"]
    assert api.services.config.get("llm.provider") == "ollama"


def test_claudes_code_goes_to_the_waiting_signin(api, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """D75: the page takes Claude's code and passes it to the windowless sign-in."""
    from app.api import routes

    class Waiting:
        def __init__(self) -> None:
            self.sent: list[str] = []

        def running(self) -> bool:
            return True

        def send(self, text: str) -> bool:
            self.sent.append(text)
            return True

    from app.llm.claude_cli import ClaudeCliClient, CliStatus

    client = api.client()
    signed = {"in": False}
    monkeypatch.setattr(
        ClaudeCliClient, "status", lambda self: CliStatus(True, signed_in=signed["in"])
    )
    none = client.post(
        "/api/llm/signin/code", json={"provider": "claude-subscription", "code": "abc#def"}
    )
    assert none.status_code == 409, "no sign-in is waiting for a code"
    # Claude Code can finish by itself before the code is pasted (the test machine, 2026-09-28):
    # a late code is "already signed in", never a failure over a sign-in that worked.
    signed["in"] = True
    late = client.post(
        "/api/llm/signin/code", json={"provider": "claude-subscription", "code": "abc#def"}
    )
    assert late.status_code == 200 and late.json() == {"sent": False, "signed_in": True}
    waiting = Waiting()
    routes._LOGINS["claude-subscription"] = waiting
    try:
        sent = client.post(
            "/api/llm/signin/code", json={"provider": "claude-subscription", "code": " abc#def "}
        )
        assert sent.status_code == 200, sent.text
        assert waiting.sent == [" abc#def "]
        empty = client.post(
            "/api/llm/signin/code", json={"provider": "claude-subscription", "code": "  "}
        )
        assert empty.status_code == 400
    finally:
        routes._LOGINS.pop("claude-subscription", None)


def test_a_key_with_nowhere_to_be_kept_is_refused_in_words(
    api: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Where there is no credential store (WSL), saving a key raised out of the route
    as a bare 500 (known-issues #13). It says why, and what to do instead."""
    import keyring
    import keyring.errors

    from app.config import KeyringSecrets

    def no_backend(*_: Any) -> None:
        raise keyring.errors.NoKeyringError("No recommended backend was available.")

    monkeypatch.setattr(keyring, "set_password", no_backend)
    monkeypatch.setattr(api.services.config, "_secrets", KeyringSecrets())
    response = api.client().put("/api/settings/secrets", json={"values": {"gemini": "abc"}})
    assert response.status_code == 503
    assert "no credential store" in response.json()["detail"]


def test_setup_failure_takes_a_known_provider_and_stage(api) -> None:  # type: ignore[no-untyped-def]
    """The page reports an install or sign-in it gave up on (machine A, 2026-10-12)."""
    client = api.client()
    ok = client.post(
        "/api/diagnostics/setup-failure",
        json={"provider": "antigravity-subscription", "stage": "signin", "reason": "timed-out"},
    )
    assert ok.status_code == 200 and "reported" in ok.json()
    unknown = client.post(
        "/api/diagnostics/setup-failure",
        json={"provider": "someone-else", "stage": "signin", "reason": "timed-out"},
    )
    assert unknown.status_code == 400
    free_text = client.post(
        "/api/diagnostics/setup-failure",
        json={"provider": "codex-subscription", "stage": "signin", "reason": "Dana's meeting"},
    )
    assert free_text.status_code == 422, "a reason is a slug, never free text"


def test_models_are_listed_chosen_and_checked(api, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """Settings' model choice (2026-10-12): the provider's own list, a choice it must
    offer, automatic again on empty, and the choice in force in the next client."""
    from app.llm import model_choice
    from app.llm.client import make_client

    agy = "Fetching…\ngemini-3.8-flash-medium\tFlash (Medium)\ngemini-3.8-flash-low\tFlash (Low)\n"
    monkeypatch.setattr(api.services.models, "run", lambda args, timeout: (0, agy, ""))

    class Cli:
        def resolve(self) -> str:
            return "agy"

    monkeypatch.setattr(model_choice, "_cli_client", lambda provider, cfg: Cli())
    provider = "antigravity-subscription"
    client = api.client()
    listed = client.get("/api/llm/models", params={"provider": provider}).json()
    ids = [m["id"] for m in listed["models"]]
    assert ids == ["gemini-3.8-flash-medium", "gemini-3.8-flash-low"]
    assert listed["chosen"] == {"summary": "", "assistant": ""}
    assert listed["effective"] == {
        "summary": "gemini-3.8-flash-medium",
        "assistant": "gemini-3.8-flash-low",
    }
    refused = client.put(
        "/api/llm/model", json={"provider": provider, "use": "summary", "model": "made-up"}
    )
    assert refused.status_code == 400, "only a model the provider offers"
    chose = client.put(
        "/api/llm/model",
        json={"provider": provider, "use": "summary", "model": "gemini-3.8-flash-low"},
    ).json()
    assert chose["chosen"]["summary"] == "gemini-3.8-flash-low"
    assert make_client(api.services.config, provider=provider).model == "gemini-3.8-flash-low"  # type: ignore[attr-defined]
    back = client.put("/api/llm/model", json={"provider": provider, "use": "summary", "model": ""})
    assert back.json()["chosen"]["summary"] == ""
    assert client.get("/api/llm/models", params={"provider": "none"}).status_code == 400
    flag = client.put(
        "/api/llm/model", json={"provider": provider, "use": "summary", "model": "-x"}
    )
    assert flag.status_code == 400, "never something a command line would read as a flag"
    cloud = client.put(
        "/api/llm/model",
        json={"provider": "ollama", "use": "summary", "model": "gpt-oss:120b-cloud"},
    )
    assert cloud.status_code == 400, "a sensitive meeting must never reach a cloud model"


def test_an_api_model_is_tried_before_it_is_kept(api, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """A vendor's list can name a model our client cannot drive: it is tried once."""
    from app.llm import model_choice
    from app.llm.gemini_client import GeminiClient

    api.services.config.set("secrets.backend", "memory")
    api.services.config.secrets.set("gemini", "k")
    api.services.models._lists["gemini"] = model_choice.Listing(
        models=[
            model_choice.ModelInfo("gemini-tts", "TTS"),
            model_choice.ModelInfo("gemini-3", "3"),
        ],
        fetched_at=api.services.models.now(),
    )
    tried: list[str] = []

    def answer(self, **kwargs):  # type: ignore[no-untyped-def]
        tried.append(self.model)
        if self.model == "gemini-tts":
            raise RuntimeError("this model only speaks")
        from app.llm.client import LlmResult

        return LlmResult(data={"ok": True}, model=self.model)

    monkeypatch.setattr(GeminiClient, "complete_json", answer)
    client = api.client()
    refused = client.put(
        "/api/llm/model", json={"provider": "gemini", "use": "summary", "model": "gemini-tts"}
    )
    assert refused.status_code == 400 and refused.json()["detail"] == "did-not-answer"
    kept = client.put(
        "/api/llm/model", json={"provider": "gemini", "use": "summary", "model": "gemini-3"}
    )
    assert kept.status_code == 200 and kept.json()["chosen"]["summary"] == "gemini-3"
    assert tried == ["gemini-tts", "gemini-3"]


def test_the_general_settings_cannot_set_a_bad_model(api) -> None:  # type: ignore[no-untyped-def]
    client = api.client()
    for values in (
        {"llm.antigravity_model": "--dangerously-x"},
        {"llm.local_model": "gpt-oss:120b-cloud"},
        {"llm.summary_models": {"gemini": "x"}},
    ):
        assert client.put("/api/settings", json={"values": values}).status_code == 400, values
    assert (
        client.put("/api/settings", json={"values": {"llm.gemini_model": "gemini-3"}}).status_code
        == 200
    )


def test_a_cli_model_is_not_tried_and_a_choice_is_announced(
    api, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    """A CLI runs what it lists, so no trial prompt; the choice is published for any
    other window showing it."""
    from app.llm import model_choice
    from app.llm.claude_cli import ClaudeCliClient

    def never(self, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("a CLI's model is not tried")

    monkeypatch.setattr(ClaudeCliClient, "complete_json", never)
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(
        api.services.events, "publish", lambda kind, **data: seen.append({kind: data})
    )
    api.services.models._lists["claude-subscription"] = model_choice.Listing(
        models=[model_choice.ModelInfo("haiku", "Haiku")], fetched_at=api.services.models.now()
    )
    saved = api.client().put(
        "/api/llm/model",
        json={"provider": "claude-subscription", "use": "assistant", "model": "haiku"},
    )
    assert saved.status_code == 200
    assert {"settings": {"changed": ["llm.assistant_models"]}} in seen


def test_ask_runs_the_summaries_model_for_a_provider_with_no_chat_box(
    api, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    """Ask's model: the conversation's for a provider with a chat box to choose it at,
    the summaries' for one without; a model the settings refuse is said on the page."""
    from app import ask as ask_module
    from app.errors import PermanentError
    from tests.integration.test_redesign_api import _transcribed

    _transcribed(api, "m-a", "Churn review", [(0, "ME", "Churn went up in August.")])
    meeting = api.services.dao.get_meeting("m-a")
    used: list[str] = []

    def make(config, *, sensitive=False, provider=None, use="summary", fetch=False):  # type: ignore[no-untyped-def]
        used.append(use)
        raise PermanentError("The Ollama model set is a cloud model.")

    monkeypatch.setattr(ask_module, "make_client", make)
    for provider in ("gemini", "antigravity-subscription"):
        api.services.config.set("llm.provider", provider)
        with pytest.raises(ask_module.AskError, match="cloud model"):
            ask_module.ask(api.services.dao, api.services.config, meeting, "What happened?")
    assert used == ["summary", "assistant"]


def test_no_conversation_model_for_a_provider_with_no_chat_box(api) -> None:  # type: ignore[no-untyped-def]
    """Its Ask and Test run the summaries' model; a conversation model would never run."""
    put = api.client().put(
        "/api/llm/model", json={"provider": "gemini", "use": "assistant", "model": "gemini-3"}
    )
    assert put.status_code == 400 and put.json()["detail"] == "no-chat-box"
