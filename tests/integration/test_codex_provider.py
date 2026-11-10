"""Codex on the user's own ChatGPT plan, end to end, with a fake ``codex`` on PATH.

Nothing here reaches OpenAI: ``tests/fixtures/codex.py`` stands in for the CLI, and the
real subprocess, PATH lookup, stdin, output files and child environment are all
exercised. See Codex 1, 2, 3 and 5 of epic z8tj1h9bnj, and D58.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from app import meta
from app.clock import parse_iso
from app.config import Config
from app.errors import ConfigError
from app.llm.client import FakeLlm
from app.pipeline.states import JobStage
from tests.fixtures.api import build_harness
from tests.fixtures.codex import install_fake_codex

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the fake CLI is a shebang script")

TRANSCRIPT = (
    "**[00:05] ME:** Let's ship the release on Thursday.\n"
    "**[00:40] THEM:** I'll send the deck before then.\n"
)


@pytest.fixture
def codex_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake ``codex`` first on PATH, logging every call; an API key in the environment."""
    install_fake_codex(tmp_path / "bin")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}")
    log = tmp_path / "codex-calls.jsonl"
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
    monkeypatch.setenv("FAKE_CODEX_MODE", "ok")
    # Present in the app's environment; must never reach the child.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-not-reach-codex")
    return log


@pytest.fixture
def api(tmp_path: Path, app_home: Path, codex_log: Path):  # type: ignore[no-untyped-def]
    harness = build_harness(tmp_path)
    harness.services.config.set("llm.provider", "codex-subscription")
    return harness


def calls(log: Path) -> list[dict[str, Any]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def ready_meeting(api) -> Any:  # type: ignore[no-untyped-def]
    """A meeting whose transcript is on disk, with its summarize job queued."""
    meeting = api.services.meetings.create(source="manual", title="Release sync")
    meeting.path.mkdir(parents=True, exist_ok=True)
    (meeting.path / "transcript.md").write_text(TRANSCRIPT, encoding="utf-8")
    api.services.queue.enqueue(meeting.id, JobStage.SUMMARIZE)
    return meeting


def run_summarize(api) -> Any:  # type: ignore[no-untyped-def]
    worker = api.services.worker
    job = api.services.queue.claim_next()
    assert job is not None and job.stage == str(JobStage.SUMMARIZE)
    worker.execute(job)
    return api.services.queue.require(job.id)


# ------------------------------------------------------------------ Codex 1


def test_a_fake_codex_on_path_drives_a_full_summarize_stage(api, codex_log: Path) -> None:  # type: ignore[no-untyped-def]
    meeting = ready_meeting(api)
    job = run_summarize(api)
    assert job.state == "done", job.last_error

    notes = json.loads((meeting.path / "notes.json").read_text(encoding="utf-8"))
    assert notes["summary_html"] == "<p>Summarized by the fake Codex.</p>"
    assert notes["action_items"][0]["what"] == "send the deck"
    assert "chapters" in notes and notes["chapters"] == [], "strict nulls were stripped"
    assert meta.read(meeting.path)["llm"]["name"] == "codex-subscription"

    execs = [call for call in calls(codex_log) if call["args"][:1] == ["exec"]]
    assert len(execs) == 1
    sent = execs[0]
    assert "OPENAI_API_KEY" not in sent["env"], "the plan, never an API key"
    assert TRANSCRIPT.strip() in sent["prompt"], "the transcript went on stdin"
    assert TRANSCRIPT.strip() not in " ".join(sent["args"])
    assert Path(sent["cwd"]).name == "codex-cli", "spawned in our own folder"
    assert str(meeting.path) not in json.dumps(sent), "nothing points into the recordings"
    assert sent["schema"]["additionalProperties"] is False
    leftovers = [p for p in meeting.path.iterdir() if p.suffix == ".json" and "schema" in p.name]
    assert leftovers == []


def test_the_test_button_works_through_codex(api) -> None:  # type: ignore[no-untyped-def]
    body = api.client().post("/api/llm/test", json={"provider": "codex-subscription"}).json()
    assert body == {
        "provider": "codex-subscription",
        "ok": True,
        "model": "codex:codex-subscription",
    }


# ------------------------------------------------------------------ Codex 2


def row(api) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    body = api.client().get("/api/llm/status").json()
    return {p["id"]: p for p in body["providers"]}["codex-subscription"]


def test_status_with_the_cli_present_and_signed_in(api) -> None:  # type: ignore[no-untyped-def]
    codex = row(api)
    assert codex["label"] == "Codex CLI (your own ChatGPT plan)"
    assert codex["needs"] == "cli"
    assert codex["ready"] is True
    assert codex["signed_in"] is True
    assert codex["account"] == "ChatGPT account"
    assert codex["path"].endswith("codex")
    assert codex["detail"] == "codex-cli 0.0.0-fake"
    assert codex["update_hint"].endswith(" update")
    assert codex["quota"] is None, "not polled"
    assert codex["install_docs"].startswith("https://")
    for key in ("can_install", "install_command", "install_method"):
        assert key in codex


def test_status_when_signed_out(api, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("FAKE_CODEX_MODE", "signed-out")
    assert row(api)["signed_in"] is False


def test_status_with_an_old_build_is_unknown(api, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("FAKE_CODEX_MODE", "old")
    codex = row(api)
    assert codex["ready"] is True
    assert codex["signed_in"] is None, "unknown, never guessed as signed out"


def test_status_with_the_cli_absent(api) -> None:  # type: ignore[no-untyped-def]
    api.services.config.set("llm.codex_cli_path", "definitely-not-installed-xyz")
    codex = row(api)
    assert codex["ready"] is False
    assert codex["signed_in"] is None
    assert codex["update_hint"] == ""


def test_status_names_the_fallback(api) -> None:  # type: ignore[no-untyped-def]
    assert api.client().get("/api/llm/status").json()["fallback"] == ""
    api.client().put("/api/settings", json={"values": {"llm.fallback_provider": "gemini"}})
    assert api.client().get("/api/llm/status").json()["fallback"] == "gemini"


def test_selecting_codex_without_it_is_refused(api) -> None:  # type: ignore[no-untyped-def]
    api.services.config.set("llm.provider", "fake")
    api.services.config.set("llm.codex_cli_path", "definitely-not-installed-xyz")
    response = api.client().put(
        "/api/settings", json={"values": {"llm.provider": "codex-subscription"}}
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "Codex is not installed" in detail and "Install it first" in detail
    assert api.services.config.get("llm.provider") == "fake"


def test_a_fallback_that_cannot_run_is_refused_too(api) -> None:  # type: ignore[no-untyped-def]
    api.services.config.set("llm.codex_cli_path", "definitely-not-installed-xyz")
    response = api.client().put(
        "/api/settings", json={"values": {"llm.fallback_provider": "codex-subscription"}}
    )
    assert response.status_code == 409


def test_signin_install_and_update_take_the_provider(  # type: ignore[no-untyped-def]
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.llm import codex_cli

    monkeypatch.setattr(codex_cli, "SIGNIN_CONSOLE", True)  # the window, where it is kept
    client = api.client()
    signin = client.post("/api/llm/signin", json={"provider": "codex-subscription"}).json()
    # Off Windows nothing is launched; the command shown is what would have run.
    assert signin["launched"] is False
    # The resolved binary's own login, inside the window's transcript (console_log.py).
    assert "/codex' 'login'; Stop-Transcript" in signin["command"]
    assert signin["log"].endswith("codex-signin.log")
    install = client.post("/api/llm/install", json={"provider": "codex-subscription"}).json()
    assert install["docs"].startswith("https://developers.openai.com/codex")
    update = client.post("/api/llm/update", json={"provider": "codex-subscription"}).json()
    assert update["command"].endswith(" update")
    # Claude stays the default for callers that send nothing.
    claude = client.post("/api/llm/install", json={}).json()
    assert "claude" in claude["docs"]
    refused = client.post("/api/llm/signin", json={"provider": "gemini"})
    assert refused.status_code == 400


@pytest.fixture
def hidden(api):  # type: ignore[no-untyped-def]
    """Sign in with no window, as shipped; every login stopped when the test ends."""
    from app.api import routes
    from app.llm import codex_cli

    assert codex_cli.SIGNIN_CONSOLE is False, "the shipped setting hides the window"
    yield api
    for login in routes._LOGINS.values():
        login.stop()
    routes._LOGINS.clear()


def codex_row(api) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    providers = api.client().get("/api/llm/status").json()["providers"]
    by_id: dict[str, dict[str, Any]] = {p["id"]: p for p in providers}
    return by_id["codex-subscription"]


def test_a_hidden_signin_hands_the_page_its_link(hidden, codex_log: Path) -> None:  # type: ignore[no-untyped-def]
    """No window: the link ``codex login`` prints is what the page offers instead."""
    body = hidden.client().post("/api/llm/signin", json={"provider": "codex-subscription"}).json()
    assert body["launched"] is True
    assert body["url"].startswith("https://auth.openai.com/oauth/authorize?")
    assert "localhost%3A1455" in body["url"], "the link carries this machine's callback"
    row = codex_row(hidden)
    assert row["console_open"] is True
    assert row["signin_url"] == body["url"]
    # The window's transcript is gone; the same file records the output instead.
    assert "Starting local login server" in Path(body["log"]).read_text(encoding="utf-8")
    assert "OPENAI_API_KEY" not in calls(codex_log)[-1]["env"], "the login gets no key either"


def test_cancel_stops_a_hidden_signin(hidden) -> None:  # type: ignore[no-untyped-def]
    client = hidden.client()
    client.post("/api/llm/signin", json={"provider": "codex-subscription"})
    stopped = client.post("/api/llm/signin/cancel", json={"provider": "codex-subscription"})
    assert stopped.json() == {"stopped": True}
    row = codex_row(hidden)
    assert row["console_open"] is False, "the row stops waiting"
    assert row["signin_url"] == ""


def test_a_second_signin_replaces_the_first(  # type: ignore[no-untyped-def]
    hidden, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The old login holds the callback port; pressing Sign in again means it is abandoned."""
    from app.api import routes
    from app.llm import signin

    monkeypatch.setattr(signin, "JOIN_S", 0.0)  # pressed again later, not twice at once
    client = hidden.client()
    client.post("/api/llm/signin", json={"provider": "codex-subscription"})
    first = routes._LOGINS["codex-subscription"]
    client.post("/api/llm/signin", json={"provider": "codex-subscription"})
    assert not first.running()
    assert routes._LOGINS["codex-subscription"].running()


def test_the_same_press_twice_joins_the_running_signin(hidden) -> None:  # type: ignore[no-untyped-def]
    """Two Sign ins a moment apart are one press: the second must not kill the first, whose
    exit the page then read as a failed sign-in (machine B, 2026-10-12)."""
    from app.api import routes

    client = hidden.client()
    one = client.post("/api/llm/signin", json={"provider": "codex-subscription"}).json()
    first = routes._LOGINS["codex-subscription"]
    two = client.post("/api/llm/signin", json={"provider": "codex-subscription"}).json()
    assert routes._LOGINS["codex-subscription"] is first
    assert first.running()
    assert two["url"] == one["url"]


def test_the_row_keeps_waiting_while_a_signin_is_replaced(hidden) -> None:  # type: ignore[no-untyped-def]
    """No process runs between stopping the old login and starting the new one; the row
    must not read as "the sign-in ended" in that gap."""
    from app.api import routes

    routes._STARTING.add("codex-subscription")
    try:
        assert codex_row(hidden)["console_open"] is True
    finally:
        routes._STARTING.discard("codex-subscription")
    assert codex_row(hidden)["console_open"] is False


def test_a_hidden_signin_that_dies_says_why(  # type: ignore[no-untyped-def]
    hidden, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODEX_MODE", "login-fails")
    response = hidden.client().post("/api/llm/signin", json={"provider": "codex-subscription"})
    assert response.status_code == 500
    assert "address in use" in response.json()["detail"]
    assert codex_row(hidden)["console_open"] is False


def test_sign_out_flips_the_row_back_to_sign_in(api) -> None:  # type: ignore[no-untyped-def]
    client = api.client()
    assert codex_row(api)["signed_in"] is True
    response = client.post("/api/llm/signout", json={"provider": "codex-subscription"})
    assert response.json() == {"signed_out": True}
    assert codex_row(api)["signed_in"] is False, "the row now offers Sign in"


def test_sign_out_also_ends_a_waiting_signin(hidden) -> None:  # type: ignore[no-untyped-def]
    from app.api import routes

    client = hidden.client()
    client.post("/api/llm/signin", json={"provider": "codex-subscription"})
    login = routes._LOGINS["codex-subscription"]
    client.post("/api/llm/signout", json={"provider": "codex-subscription"})
    assert not login.running(), "it would otherwise go on holding the callback port"


def test_signin_without_codex_is_actionable(api) -> None:  # type: ignore[no-untyped-def]
    api.services.config.set("llm.codex_cli_path", "definitely-not-installed-xyz")
    response = api.client().post("/api/llm/signin", json={"provider": "codex-subscription"})
    assert response.status_code == 409
    assert "Codex is not installed" in response.json()["detail"]


# ------------------------------------------------------------------ Codex 3


def test_a_spent_allowance_requeues_the_job_and_says_why(  # type: ignore[no-untyped-def]
    api, codex_log: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODEX_MODE", "quota")
    from app.pipeline.stages import summarize

    def nobody_else(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("no fallback is set, so no other provider may be built")

    monkeypatch.setattr(summarize, "make_client", nobody_else)
    monkeypatch.setattr(summarize, "client_for", lambda ctx: _codex(api))
    meeting = ready_meeting(api)
    job = run_summarize(api)

    assert job.state == "pending", "back in the queue, not failed"
    assert job.attempts == 0, "waiting for a reset is not a failed attempt"
    assert job.last_error.startswith("Your ChatGPT plan's Codex allowance is used up")
    assert "Aug 29, 2026 4:15 PM" in job.last_error
    # The CLI prints the reset in this machine's local time, so that is how it is read.
    expected = datetime(2026, 8, 29, 16, 15).astimezone()
    assert job.not_before is not None and parse_iso(job.not_before) == expected
    assert api.services.dao.require_meeting(meeting.id).state != "failed"
    assert not (meeting.path / "notes.json").exists(), "nothing was written by anyone"
    assert len([c for c in calls(codex_log) if c["args"][:1] == ["exec"]]) == 1

    attention = api.client().get("/api/attention").json()["items"]
    assert attention == [
        {
            "meeting_id": meeting.id,
            "title": "Release sync",
            "stage": "summarize",
            "state": "waiting",
            "message": job.last_error,
            "retry_at": job.not_before,
            "waiting_for": None,
        }
    ]
    page = api.client().get(f"/api/meetings/{meeting.id}").json()
    summarizing = next(j for j in page["jobs"] if j["stage"] == "summarize")
    assert summarizing["last_error"] == job.last_error, "the meeting page can say it too"


def test_with_a_fallback_the_meeting_is_summarized_and_says_by_whom(  # type: ignore[no-untyped-def]
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODEX_MODE", "quota")
    from app.pipeline.stages import summarize

    built: list[str | None] = []
    stand_in = FakeLlm()
    stand_in.name = "gemini"

    def make(
        config: Config, *, sensitive: bool = False, provider: str | None = None, **_: Any
    ) -> Any:
        built.append(provider)
        return stand_in if provider == "gemini" else _codex(api)

    monkeypatch.setattr(summarize, "make_client", make)
    api.client().put("/api/settings", json={"values": {"llm.fallback_provider": "gemini"}})
    meeting = ready_meeting(api)
    job = run_summarize(api)

    assert job.state == "done", job.last_error
    assert built == [None, "gemini"], "the configured provider first, then the chosen fallback"
    assert (meeting.path / "notes.json").exists()
    written = meta.read(meeting.path)["llm"]
    assert written["name"] == "gemini"
    assert written["fallback_for"] == "codex-subscription"
    assert "allowance is used up" in written["fallback_reason"]
    page = api.client().get(f"/api/meetings/{meeting.id}").json()
    assert page["summarized_by"]["provider"] == "gemini"
    assert page["summarized_by"]["fallback_for"] == "codex-subscription"


def test_a_sensitive_meeting_never_falls_back(api, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    from app.errors import QuotaExhausted
    from app.pipeline.stages import summarize

    api.services.config.set("llm.fallback_provider", "gemini")
    meeting = ready_meeting(api)
    api.services.dao.update_meeting(meeting.id, sensitive=1)
    ctx = type("Ctx", (), {})()
    ctx.services, ctx.config = api.services, api.services.config
    ctx.meeting = api.services.dao.require_meeting(meeting.id)
    spent = QuotaExhausted("spent", provider="codex-subscription")
    assert summarize.fallback_client(ctx, spent) is None  # type: ignore[arg-type]


def test_a_signed_out_cli_waits_in_the_queue(api, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """A Settings problem, surfaced in Settings — not a failed meeting."""
    monkeypatch.setenv("FAKE_CODEX_MODE", "signed-out")
    from app.pipeline.stages import summarize

    monkeypatch.setattr(summarize, "client_for", lambda ctx: _codex(api))
    ready_meeting(api)
    job = run_summarize(api)
    assert job.state == "pending" and job.attempts == 0
    assert "not signed in" in job.last_error
    assert job.not_before is not None


def test_a_signed_out_cli_goes_to_the_fallback_the_user_chose(  # type: ignore[no-untyped-def]
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Signed out is answered like a spent allowance: the fallback, when one is set."""
    monkeypatch.setenv("FAKE_CODEX_MODE", "signed-out")
    from app.pipeline.stages import summarize

    stand_in = FakeLlm()
    stand_in.name = "gemini"

    def make(config: Config, *, provider: str | None = None, **_: Any) -> Any:
        return stand_in if provider == "gemini" else _codex(api)

    monkeypatch.setattr(summarize, "make_client", make)
    api.services.config.set("llm.fallback_provider", "gemini")
    meeting = ready_meeting(api)
    job = run_summarize(api)

    assert job.state == "done", job.last_error
    written = meta.read(meeting.path)["llm"]
    assert written["name"] == "gemini"
    assert written["fallback_for"] == "codex-subscription"
    assert "not signed in" in written["fallback_reason"]


def test_when_the_fallback_cannot_run_either_the_job_waits_on_the_usual_one(  # type: ignore[no-untyped-def]
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODEX_MODE", "signed-out")
    from app.errors import SignInRequired
    from app.pipeline.stages import summarize

    class SignedOutToo(FakeLlm):
        name = "antigravity-subscription"

        def complete_json(self, **_: Any) -> Any:
            raise SignInRequired("Antigravity is not signed in.", provider=self.name)

    def make(config: Config, *, provider: str | None = None, **_: Any) -> Any:
        return SignedOutToo() if provider == "antigravity-subscription" else _codex(api)

    monkeypatch.setattr(summarize, "make_client", make)
    api.services.config.set("llm.fallback_provider", "antigravity-subscription")
    ready_meeting(api)
    job = run_summarize(api)

    assert job.state == "pending" and job.attempts == 0
    assert job.last_error.startswith("Codex is not signed in"), "the one the user can act on"


def test_allowance_spent_and_the_fallback_signed_out_waits_for_the_sign_in(  # type: ignore[no-untyped-def]
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reset may be days away; signing in the fallback is what can be done now."""
    monkeypatch.setenv("FAKE_CODEX_MODE", "quota")
    from app.errors import SignInRequired
    from app.pipeline.stages import summarize

    class SignedOut(FakeLlm):
        name = "antigravity-subscription"

        def complete_json(self, **_: Any) -> Any:
            raise SignInRequired("Antigravity is not signed in.", provider=self.name)

    monkeypatch.setattr(
        summarize,
        "make_client",
        lambda config, *, provider=None, **_: (
            SignedOut() if provider == "antigravity-subscription" else _codex(api)
        ),
    )
    api.services.config.set("llm.fallback_provider", "antigravity-subscription")
    ready_meeting(api)
    job = run_summarize(api)
    assert job.state == "pending"
    assert job.waiting_for == "antigravity-subscription"
    assert job.last_error.startswith("Antigravity is not signed in")


def test_a_signed_out_fallback_is_not_offered_from_its_held_status(  # type: ignore[no-untyped-def]
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODEX_MODE", "signed-out")
    from app.errors import SignInRequired
    from app.pipeline.stages import summarize

    class SignedOut(FakeLlm):
        name = "antigravity-subscription"

        def complete_json(self, **_: Any) -> Any:
            raise SignInRequired("Antigravity is not signed in.", provider=self.name)

    monkeypatch.setattr(
        summarize,
        "make_client",
        lambda config, *, provider=None, **_: (
            SignedOut() if provider == "antigravity-subscription" else _codex(api)
        ),
    )
    forgotten: list[str] = []
    monkeypatch.setattr(api.services.cli_status, "forget", lambda *p: forgotten.extend(p))
    api.services.config.set("llm.fallback_provider", "antigravity-subscription")
    ready_meeting(api)
    assert run_summarize(api).waiting_for == "codex-subscription"
    assert set(forgotten) == {"antigravity-subscription", "codex-subscription"}


@pytest.mark.parametrize(("usual_h", "fallback_h", "waits_h"), [(120, 2, 2), (2, 120, 2)])
def test_both_allowances_spent_waits_for_the_one_back_first(  # type: ignore[no-untyped-def]
    api, monkeypatch: pytest.MonkeyPatch, usual_h: int, fallback_h: int, waits_h: int
) -> None:
    from datetime import timedelta

    from app.errors import QuotaExhausted
    from app.pipeline.stages import summarize

    now = api.services.clock.now()

    def spent(name: str, hours: int) -> Any:
        class Spent(FakeLlm):
            def complete_json(self, **_: Any) -> Any:
                raise QuotaExhausted(
                    f"{name} spent", provider=name, retry_at=now + timedelta(hours=hours)
                )

        client = Spent()
        client.name = name
        return client

    monkeypatch.setattr(summarize, "client_for", lambda ctx: spent("codex-subscription", usual_h))
    monkeypatch.setattr(
        summarize,
        "make_client",
        lambda config, *, provider=None, **_: spent("antigravity-subscription", fallback_h),
    )
    api.services.config.set("llm.fallback_provider", "antigravity-subscription")
    ready_meeting(api)
    job = run_summarize(api)
    assert job.state == "pending" and job.waiting_for is None
    assert parse_iso(job.not_before) == now + timedelta(hours=waits_h)


def test_usual_signed_out_and_fallback_spent_is_written_after_the_reset(  # type: ignore[no-untyped-def]
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Better than before: the fallback is tried again at each retry, and writes the
    summary once its allowance is back, if the usual provider is still signed out."""
    monkeypatch.setenv("FAKE_CODEX_MODE", "signed-out")
    from app.errors import QuotaExhausted
    from app.pipeline.stages import summarize

    spent = [True]
    stand_in = FakeLlm()

    class SpentOnce(FakeLlm):
        name = "gemini"

        def complete_json(self, **kwargs: Any) -> Any:
            if spent and spent.pop():
                raise QuotaExhausted("Gemini spent", provider="gemini")
            return stand_in.complete_json(**kwargs)

    monkeypatch.setattr(
        summarize,
        "make_client",
        lambda config, *, provider=None, **_: SpentOnce() if provider == "gemini" else _codex(api),
    )
    api.services.config.set("llm.fallback_provider", "gemini")
    meeting = ready_meeting(api)
    first = run_summarize(api)
    assert first.state == "pending" and first.waiting_for == "codex-subscription"
    api.services.clock.advance(16 * 60)
    assert run_summarize(api).state == "done"
    assert meta.read(meeting.path)["llm"]["fallback_for"] == "codex-subscription"


def _row(api, provider: str) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    rows = api.client().get("/api/llm/status").json()["providers"]
    return dict(next(row for row in rows if row["id"] == provider))


def test_a_fallback_whose_allowance_is_spent_is_not_offered(  # type: ignore[no-untyped-def]
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Usual provider signed out, fallback spent: the page must not offer the fallback,
    whose status reads signed in; written by a provider again, it is offered again."""
    monkeypatch.setenv("FAKE_CODEX_MODE", "signed-out")
    from app.errors import QuotaExhausted
    from app.pipeline.stages import summarize

    spent = [True]
    stand_in = FakeLlm()

    class SpentOnce(FakeLlm):
        name = "antigravity-subscription"

        def complete_json(self, **kwargs: Any) -> Any:
            if spent and spent.pop():
                raise QuotaExhausted("Antigravity spent", provider=self.name)
            return stand_in.complete_json(**kwargs)

    monkeypatch.setattr(
        summarize,
        "make_client",
        lambda config, *, provider=None, **_: (
            SpentOnce() if provider == "antigravity-subscription" else _codex(api)
        ),
    )
    api.services.config.set("llm.fallback_provider", "antigravity-subscription")
    ready_meeting(api)
    assert run_summarize(api).waiting_for == "codex-subscription"
    assert _row(api, "antigravity-subscription")["spent_until"] is not None
    assert _row(api, "codex-subscription")["spent_until"] is None

    api.services.clock.advance(16 * 60)
    assert run_summarize(api).state == "done", "the fallback writes it once it can"
    assert _row(api, "antigravity-subscription")["spent_until"] is None


def test_a_picked_provider_whose_allowance_is_spent_is_marked(  # type: ignore[no-untyped-def]
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import timedelta

    from app.errors import QuotaExhausted
    from app.pipeline.stages import summarize

    reset = api.services.clock.now() + timedelta(days=3)

    class Spent(FakeLlm):
        name = "antigravity-subscription"

        def complete_json(self, **_: Any) -> Any:
            raise QuotaExhausted("spent", provider=self.name, retry_at=reset)

    monkeypatch.setattr(summarize, "make_client", lambda config, *, provider=None, **_: Spent())
    meeting = ready_meeting(api)
    api.client().post(
        f"/api/meetings/{meeting.id}/jobs/summarize/retry",
        params={"provider": "antigravity-subscription"},
    )
    job = run_summarize(api)
    assert job.state == "pending" and job.waiting_for is None
    assert meta.read(meeting.path)["summary_provider"] == "antigravity-subscription"
    assert parse_iso(_row(api, "antigravity-subscription")["spent_until"]) == reset


def test_signed_out_tells_the_user_once_and_refreshes_the_status(  # type: ignore[no-untyped-def]
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One toast per sign-out: not one per waiting meeting, nor one per retry."""
    monkeypatch.setenv("FAKE_CODEX_MODE", "signed-out")
    from app.pipeline.stages import summarize

    monkeypatch.setattr(summarize, "client_for", lambda ctx: _codex(api))
    forgotten: list[str] = []
    monkeypatch.setattr(api.services.cli_status, "forget", lambda *p: forgotten.extend(p))
    first = ready_meeting(api)
    job = run_summarize(api)
    assert job.state == "pending" and job.waiting_for == "codex-subscription"
    assert forgotten == ["codex-subscription"], "the page sees the sign-out at once"
    second = api.services.meetings.create(source="manual", title="Backlog")
    second.path.mkdir(parents=True, exist_ok=True)
    (second.path / "transcript.md").write_text(TRANSCRIPT, encoding="utf-8")
    api.services.queue.enqueue(second.id, JobStage.SUMMARIZE)
    assert run_summarize(api).waiting_for == "codex-subscription"

    notifier = api.services.notifier
    titles = [t.title for t in notifier.shown if t.key.startswith("signedout:")]
    assert titles == ["Codex is signed out — the summary is waiting"], "two meetings, one toast"
    assert first.id != second.id

    attention = api.client().get("/api/attention").json()["items"]
    assert {item["waiting_for"] for item in attention} == {"codex-subscription"}


def test_a_signed_in_run_clears_what_the_job_was_waiting_for(  # type: ignore[no-untyped-def]
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODEX_MODE", "signed-out")
    from app.pipeline.stages import summarize

    monkeypatch.setattr(summarize, "client_for", lambda ctx: _codex(api))
    meeting = ready_meeting(api)
    job = run_summarize(api)
    assert job.waiting_for == "codex-subscription"
    monkeypatch.setenv("FAKE_CODEX_MODE", "ok")
    api.services.queue.retry(meeting.id, JobStage.SUMMARIZE)
    done = run_summarize(api)
    assert done.state == "done" and done.waiting_for is None


def test_a_chosen_provider_that_fails_is_not_used_again_by_itself(  # type: ignore[no-untyped-def]
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pick is one run's, however it ends: a later automatic run uses Settings."""
    from app.errors import PermanentError
    from app.pipeline.stages import summarize

    class Refuses(FakeLlm):
        name = "anthropic"

        def complete_json(self, **_: Any) -> Any:
            raise PermanentError("Anthropic rejected the key", category="auth")

    built: list[str | None] = []

    def make(config: Config, *, provider: str | None = None, **_: Any) -> Any:
        built.append(provider)
        return Refuses() if provider == "anthropic" else _codex(api)

    monkeypatch.setattr(summarize, "make_client", make)
    meeting = ready_meeting(api)
    picked = api.client().post(
        f"/api/meetings/{meeting.id}/jobs/summarize/retry", params={"provider": "anthropic"}
    )
    assert picked.status_code == 200
    assert run_summarize(api).state == "failed"
    assert meta.read(meeting.path).get("summary_provider") is None, "taken, not left behind"

    # Later, something re-summarizes by itself (here: a retry with no provider named).
    api.services.queue.retry(meeting.id, JobStage.SUMMARIZE)
    assert run_summarize(api).state == "done"
    assert built[-1] is None, "the provider in Settings, not the one picked once"


def test_summarize_with_another_provider_for_one_meeting(  # type: ignore[no-untyped-def]
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "Summarize with …" on the meeting page: that run, that provider, and it says so."""
    monkeypatch.setenv("FAKE_CODEX_MODE", "signed-out")
    from app.pipeline.stages import summarize

    built: list[str | None] = []
    stand_in = FakeLlm()
    stand_in.name = "antigravity-subscription"

    def make(config: Config, *, provider: str | None = None, **_: Any) -> Any:
        built.append(provider)
        return stand_in if provider == "antigravity-subscription" else _codex(api)

    monkeypatch.setattr(summarize, "make_client", make)
    meeting = ready_meeting(api)
    assert run_summarize(api).state == "pending", "signed out: waiting"

    client = api.client()
    picked = client.post(
        f"/api/meetings/{meeting.id}/jobs/summarize/retry",
        params={"provider": "antigravity-subscription"},
    )
    assert picked.status_code == 200, picked.text
    job = run_summarize(api)
    assert job.state == "done", job.last_error
    assert built[-1] == "antigravity-subscription"
    written = meta.read(meeting.path)
    assert written["llm"]["name"] == "antigravity-subscription"
    assert written["llm"]["fallback_for"] == "codex-subscription"
    assert written.get("summary_provider") is None, "one run only; Settings decides the next"
    assert job.waiting_for is None


def test_a_picked_run_that_is_preempted_keeps_the_pick(  # type: ignore[no-untyped-def]
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recording that starts mid-run must not undo the user's choice."""
    from app.errors import Preempted
    from app.pipeline.stages import summarize

    stand_in = FakeLlm()
    stand_in.name = "antigravity-subscription"
    yields = [True]

    class YieldsOnce(FakeLlm):
        name = "antigravity-subscription"

        def complete_json(self, **kwargs: Any) -> Any:
            if yields and yields.pop():
                raise Preempted("a recording started")
            return stand_in.complete_json(**kwargs)

    built: list[str | None] = []

    def make(config: Config, *, provider: str | None = None, **_: Any) -> Any:
        built.append(provider)
        return YieldsOnce() if provider == "antigravity-subscription" else _codex(api)

    monkeypatch.setattr(summarize, "make_client", make)
    meeting = ready_meeting(api)
    api.client().post(
        f"/api/meetings/{meeting.id}/jobs/summarize/retry",
        params={"provider": "antigravity-subscription"},
    )
    assert run_summarize(api).state == "pending", "released, not failed"
    assert meta.read(meeting.path)["summary_provider"] == "antigravity-subscription"
    assert run_summarize(api).state == "done"
    assert built == ["antigravity-subscription", "antigravity-subscription"]
    assert meta.read(meeting.path)["summary_provider"] is None


def test_a_picked_provider_signed_out_too_is_what_the_job_waits_for(  # type: ignore[no-untyped-def]
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page and the queue agree: it waits on the provider the user picked."""
    from app.errors import SignInRequired
    from app.pipeline.stages import summarize

    class SignedOut(FakeLlm):
        name = "antigravity-subscription"

        def complete_json(self, **_: Any) -> Any:
            raise SignInRequired("Antigravity is not signed in.", provider=self.name)

    built: list[str | None] = []

    def make(config: Config, *, provider: str | None = None, **_: Any) -> Any:
        built.append(provider)
        return SignedOut() if provider == "antigravity-subscription" else _codex(api)

    monkeypatch.setattr(summarize, "make_client", make)
    meeting = ready_meeting(api)
    api.client().post(
        f"/api/meetings/{meeting.id}/jobs/summarize/retry",
        params={"provider": "antigravity-subscription"},
    )
    job = run_summarize(api)
    assert job.state == "pending" and job.waiting_for == "antigravity-subscription"
    assert meta.read(meeting.path)["summary_provider"] == "antigravity-subscription"
    assert built == ["antigravity-subscription"]


def test_settings_offers_sign_in_while_a_summary_waits_and_wakes_it(  # type: ignore[no-untyped-def]
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sign in is offered even if the CLI reads signed in; a fresh "signed in" lets the
    waiting summary try now, but not more than once a minute."""
    monkeypatch.setenv("FAKE_CODEX_MODE", "signed-out")
    from app.pipeline.stages import summarize

    monkeypatch.setattr(summarize, "client_for", lambda ctx: _codex(api))
    ready_meeting(api)
    job = run_summarize(api)
    assert job.not_before is not None

    monkeypatch.setenv("FAKE_CODEX_MODE", "ok")  # signed in again, in its own window
    client = api.client()
    row = next(
        p
        for p in client.get("/api/llm/status?fresh=true").json()["providers"]
        if p["id"] == "codex-subscription"
    )
    assert row["waiting"] is True and row["signed_in"] is True
    assert api.services.queue.require(job.id).not_before is not None, "parked under a minute"

    api.services.clock.advance(61)
    client.get("/api/llm/status?fresh=true")
    assert api.services.queue.require(job.id).not_before is None, "tries now"
    assert run_summarize(api).state == "done"
    row = next(
        p
        for p in client.get("/api/llm/status").json()["providers"]
        if p["id"] == "codex-subscription"
    )
    assert row["waiting"] is False


def summarized_meeting(api) -> Any:  # type: ignore[no-untyped-def]
    """A meeting whose summary is already written and current (by the fake Codex)."""
    meeting = ready_meeting(api)
    assert run_summarize(api).state == "done"
    assert (meeting.path / "notes.json").exists()
    return meeting


def test_a_pick_on_a_summarized_meeting_survives_a_preemption(  # type: ignore[no-untyped-def]
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With notes already current, the second attempt must still redo them, by the pick."""
    from app.errors import Preempted
    from app.pipeline.stages import summarize

    meeting = summarized_meeting(api)
    stand_in = FakeLlm()
    yields = [True]

    class YieldsOnce(FakeLlm):
        name = "antigravity-subscription"

        def complete_json(self, **kwargs: Any) -> Any:
            if yields and yields.pop():
                raise Preempted("a recording started")
            return stand_in.complete_json(**kwargs)

    monkeypatch.setattr(
        summarize,
        "make_client",
        lambda config, *, provider=None, **_: (
            YieldsOnce() if provider == "antigravity-subscription" else _codex(api)
        ),
    )
    api.client().post(
        f"/api/meetings/{meeting.id}/jobs/summarize/retry",
        params={"provider": "antigravity-subscription"},
    )
    assert run_summarize(api).state == "pending"
    assert run_summarize(api).state == "done"
    assert meta.read(meeting.path)["llm"]["name"] == "antigravity-subscription"
    assert meta.read(meeting.path)["summary_provider"] is None


def test_a_forced_summary_deferred_then_woken_is_redone_not_skipped(  # type: ignore[no-untyped-def]
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Summarize pressed on a summarized meeting while signed out: after the sign-in the
    summary is written again, not skipped as current."""
    from app.pipeline.stages import summarize

    meeting = summarized_meeting(api)
    before = (meeting.path / "notes.json").stat().st_mtime_ns
    monkeypatch.setattr(summarize, "client_for", lambda ctx: _codex(api))
    monkeypatch.setenv("FAKE_CODEX_MODE", "signed-out")
    api.client().post(f"/api/meetings/{meeting.id}/jobs/summarize/retry?force=true")
    job = run_summarize(api)
    assert job.state == "pending" and job.waiting_for == "codex-subscription"

    monkeypatch.setenv("FAKE_CODEX_MODE", "ok")
    api.services.clock.advance(16 * 60)
    done = run_summarize(api)
    assert done.state == "done"
    assert (meeting.path / "notes.json").stat().st_mtime_ns != before, "written again"
    assert api.services.queue.take_rerun(meeting.id, "summarize") is False, "and only once"


def test_a_pick_made_while_a_run_is_in_flight_is_kept_for_the_next(  # type: ignore[no-untyped-def]
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.pipeline.stages import summarize

    meeting = ready_meeting(api)
    real = _codex(api)

    class PicksMidRun(FakeLlm):
        name = "codex-subscription"

        def count_tokens(self, text: str) -> int:
            return real.count_tokens(text)

        def complete_json(self, **kwargs: Any) -> Any:
            # The user presses "Summarize with Antigravity" while this run is going.
            meta.update(meeting.path, summary_provider="antigravity-subscription")
            return real.complete_json(**kwargs)

    monkeypatch.setattr(summarize, "client_for", lambda ctx: PicksMidRun())
    assert run_summarize(api).state == "done"
    assert meta.read(meeting.path)["summary_provider"] == "antigravity-subscription"


def test_a_claimed_job_no_longer_says_it_waits_for_a_sign_in(  # type: ignore[no-untyped-def]
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.errors import Preempted
    from app.pipeline.stages import summarize

    monkeypatch.setenv("FAKE_CODEX_MODE", "signed-out")
    monkeypatch.setattr(summarize, "client_for", lambda ctx: _codex(api))
    ready_meeting(api)
    job = run_summarize(api)
    assert job.waiting_for == "codex-subscription"

    class Yields(FakeLlm):
        def complete_json(self, **_: Any) -> Any:
            raise Preempted("a recording started")

    monkeypatch.setattr(summarize, "client_for", lambda ctx: Yields())
    api.services.clock.advance(16 * 60)
    released = run_summarize(api)
    assert released.state == "pending" and released.waiting_for is None


def test_a_pick_pressed_while_the_summary_runs_is_refused_not_lost(  # type: ignore[no-untyped-def]
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.pipeline.stages import summarize

    meeting = ready_meeting(api)
    real = _codex(api)
    answers: list[Any] = []

    class PressedMidRun(FakeLlm):
        name = "codex-subscription"

        def count_tokens(self, text: str) -> int:
            return real.count_tokens(text)

        def complete_json(self, **kwargs: Any) -> Any:
            answers.append(
                api.client().post(
                    f"/api/meetings/{meeting.id}/jobs/summarize/retry",
                    params={"provider": "antigravity-subscription"},
                )
            )
            return real.complete_json(**kwargs)

    monkeypatch.setattr(summarize, "client_for", lambda ctx: PressedMidRun())
    assert run_summarize(api).state == "done"
    assert [a.status_code for a in answers] == [409]
    assert meta.read(meeting.path).get("summary_provider") is None, "nothing left behind"


def test_re_enqueued_summary_no_longer_says_it_waits_for_a_sign_in(  # type: ignore[no-untyped-def]
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODEX_MODE", "signed-out")
    from app.pipeline.stages import summarize

    monkeypatch.setattr(summarize, "client_for", lambda ctx: _codex(api))
    meeting = ready_meeting(api)
    assert run_summarize(api).waiting_for == "codex-subscription"
    again = api.services.queue.enqueue(meeting.id, JobStage.SUMMARIZE)
    assert again.waiting_for is None and again.not_before is None


@pytest.mark.parametrize(
    ("stage", "provider", "why"),
    [
        ("transcribe", "gemini", "summarize stage only"),
        ("summarize", "fake", "not a provider"),
        ("summarize", "none", "not a provider"),
        ("summarize", "made-up", "not a provider"),
    ],
)
def test_a_provider_for_one_run_is_checked(api, stage: str, provider: str, why: str) -> None:  # type: ignore[no-untyped-def]
    meeting = ready_meeting(api)
    refused = api.client().post(
        f"/api/meetings/{meeting.id}/jobs/{stage}/retry", params={"provider": provider}
    )
    assert refused.status_code == 422 and why in refused.text


def test_a_sensitive_meeting_cannot_be_sent_elsewhere_by_hand(api) -> None:  # type: ignore[no-untyped-def]
    meeting = ready_meeting(api)
    api.services.dao.update_meeting(meeting.id, sensitive=1)
    refused = api.client().post(
        f"/api/meetings/{meeting.id}/jobs/summarize/retry", params={"provider": "gemini"}
    )
    assert refused.status_code == 422 and "sensitive" in refused.text
    assert meta.read(meeting.path).get("summary_provider") is None


def _codex(api) -> Any:  # type: ignore[no-untyped-def]
    from app.llm.codex_cli import CodexCliClient

    return CodexCliClient(api.services.config)


# ------------------------------------------------------------------ Codex 5


def test_removing_codex_is_one_line_and_its_users_fall_back(
    tmp_path: Path, app_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The day OpenAI says no: delete it from LLM_PROVIDERS and ship.

    Simulated here by removing it from the tuple the enums are built from; everything
    else — the settings row, the fallback choices, a config naming it — must follow.
    """
    from app import config as config_module
    from app.api import routes

    remaining = tuple(p for p in config_module.LLM_PROVIDERS if p != "codex-subscription")
    enums = dict(config_module._ENUMS)
    enums["llm.provider"] = remaining
    enums["llm.fallback_provider"] = ("", *(p for p in remaining if p != "fake"))
    monkeypatch.setattr(config_module, "LLM_PROVIDERS", remaining)
    monkeypatch.setattr(config_module, "_ENUMS", enums)
    monkeypatch.setattr(routes, "LLM_PROVIDERS", remaining)
    monkeypatch.setattr(routes, "CLI_PROVIDERS", {"claude-subscription": "Claude Code"})

    saved = tmp_path / "app_config.json"
    saved.write_text(
        json.dumps(
            {"llm": {"provider": "codex-subscription", "fallback_provider": "codex-subscription"}}
        ),
        encoding="utf-8",
    )
    config = Config.load(file=saved, environ={})
    assert config.get("llm.provider") == "none", "the default takes over: transcripts only (D63)"
    assert config.get("llm.fallback_provider") == ""
    notices = config.warnings()
    assert any("'codex-subscription' is no longer available" in n for n in notices)
    assert any("no fallback now" in n for n in notices)

    harness = build_harness(tmp_path / "api")
    body = harness.client().get("/api/llm/status").json()
    assert "codex-subscription" not in {p["id"] for p in body["providers"]}, "gone from Settings"
    refused = harness.client().put(
        "/api/settings", json={"values": {"llm.provider": "codex-subscription"}}
    )
    assert refused.status_code >= 400, "and it cannot be chosen again"

    with pytest.raises(ConfigError):
        config.set("llm.provider", "codex-subscription")
        config.validate()


def test_fallback_is_a_real_provider_or_none() -> None:
    from app.config import default_config

    for allowed in ("", "gemini", "ollama", "claude-subscription", "codex-subscription"):
        default_config(llm__fallback_provider=allowed)
    with pytest.raises(ConfigError):
        default_config(llm__fallback_provider="fake")
    assert default_config().get("llm.fallback_provider") == "", "none, unless chosen"
