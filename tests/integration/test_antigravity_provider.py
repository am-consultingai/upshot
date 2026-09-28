"""Antigravity on the user's own Google AI plan, end to end, with a fake ``agy`` on PATH.

Nothing here reaches Google: ``tests/fixtures/antigravity.py`` stands in for the CLI, and
the real subprocess, PATH lookup, stdin, schema file and child environment are all
exercised, as is the windowless sign-in with its pasted code. See D78.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from app import meta
from app.config import Config
from app.errors import ConfigError
from app.pipeline.states import JobStage
from tests.fixtures.antigravity import install_fake_agy
from tests.fixtures.api import build_harness

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the fake CLI is a shebang script")

PROVIDER = "antigravity-subscription"
TRANSCRIPT = (
    "**[00:05] ME:** Let's ship the release on Thursday.\n"
    "**[00:40] THEM:** אשלח את המצגת לפני כן.\n"
)


@pytest.fixture
def agy_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake ``agy`` first on PATH, logging every call; a Gemini key in the environment."""
    install_fake_agy(tmp_path / "bin")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}")
    log = tmp_path / "agy-calls.jsonl"
    monkeypatch.setenv("FAKE_AGY_LOG", str(log))
    monkeypatch.setenv("FAKE_AGY_MODE", "ok")
    # Present in the app's environment; must never reach the child.
    monkeypatch.setenv("GEMINI_API_KEY", "must-not-reach-agy")
    monkeypatch.setenv("AGY_ADC_AUTH", "1")
    return log


@pytest.fixture
def api(tmp_path: Path, app_home: Path, agy_log: Path):  # type: ignore[no-untyped-def]
    harness = build_harness(tmp_path)
    harness.services.config.set("llm.provider", PROVIDER)
    return harness


def calls(log: Path) -> list[dict[str, Any]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def runs(log: Path) -> list[dict[str, Any]]:
    return [call for call in calls(log) if "--input-format" in call["args"]]


def ready_meeting(api) -> Any:  # type: ignore[no-untyped-def]
    meeting = api.services.meetings.create(source="manual", title="Release sync")
    meeting.path.mkdir(parents=True, exist_ok=True)
    (meeting.path / "transcript.md").write_text(TRANSCRIPT, encoding="utf-8")
    api.services.queue.enqueue(meeting.id, JobStage.SUMMARIZE)
    return meeting


def run_summarize(api) -> Any:  # type: ignore[no-untyped-def]
    job = api.services.queue.claim_next()
    assert job is not None and job.stage == str(JobStage.SUMMARIZE)
    api.services.worker.execute(job)
    return api.services.queue.require(job.id)


def row(api) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    body = api.client().get("/api/llm/status").json()
    return {p["id"]: p for p in body["providers"]}[PROVIDER]


# ------------------------------------------------------------------ summarizing


def test_a_fake_agy_on_path_drives_a_full_summarize_stage(api, agy_log: Path) -> None:  # type: ignore[no-untyped-def]
    meeting = ready_meeting(api)
    job = run_summarize(api)
    assert job.state == "done", job.last_error

    notes = json.loads((meeting.path / "notes.json").read_text(encoding="utf-8"))
    assert notes["summary_html"] == "<p>Summarized by the fake Antigravity.</p>"
    assert notes["action_items"][0]["what"] == "send the deck"
    assert meta.read(meeting.path)["llm"]["name"] == PROVIDER

    sent = runs(agy_log)
    assert len(sent) == 1
    run = sent[0]
    assert run["events"] == 1, "one user event"
    assert "GEMINI_API_KEY" not in run["env"] and "AGY_ADC_AUTH" not in run["env"]
    assert TRANSCRIPT.strip() in run["prompt"], "the transcript went on stdin, Hebrew and all"
    assert TRANSCRIPT.strip() not in " ".join(run["args"])
    assert Path(run["cwd"]).name == "antigravity-cli", "spawned in our own folder"
    assert str(meeting.path) not in json.dumps(run), "nothing points into the recordings"
    models = [call for call in calls(agy_log) if call["args"] == ["models"]]
    assert models, "the sign-in was checked before the run"


def test_the_test_button_works_through_antigravity(api) -> None:  # type: ignore[no-untyped-def]
    body = api.client().post("/api/llm/test", json={"provider": PROVIDER}).json()
    assert body == {
        "provider": PROVIDER,
        "ok": True,
        "model": "antigravity:antigravity-subscription",
    }


def test_a_spent_allowance_requeues_the_job_and_says_why(  # type: ignore[no-untyped-def]
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_AGY_MODE", "quota")
    meeting = ready_meeting(api)
    job = run_summarize(api)
    assert job.state == "pending", "back in the queue, not failed"
    assert job.attempts == 0, "waiting for a reset is not a failed attempt"
    assert job.last_error.startswith("Your Google AI plan's Antigravity allowance is used up")
    assert job.not_before is not None, "no reset time is given, so it waits the default"
    assert api.services.dao.require_meeting(meeting.id).state != "failed"


def test_a_signed_out_cli_waits_in_the_queue_and_runs_nothing(  # type: ignore[no-untyped-def]
    api, agy_log: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Settings problem, surfaced in Settings — and no browser opened behind anyone's back."""
    monkeypatch.setenv("FAKE_AGY_MODE", "signed-out")
    ready_meeting(api)
    job = run_summarize(api)
    assert job.state == "pending" and job.attempts == 0
    assert "not signed in" in job.last_error
    assert runs(agy_log) == [], "a signed-out run would have opened Google's sign-in page"


# ------------------------------------------------------------------ Settings


def test_status_with_the_cli_present_and_signed_in(api) -> None:  # type: ignore[no-untyped-def]
    agy = row(api)
    assert agy["label"] == "Antigravity CLI (your own Google AI plan)"
    assert agy["needs"] == "cli"
    assert agy["ready"] is True
    assert agy["signed_in"] is True
    assert agy["account"] == "Google account"
    assert agy["detail"] == "1.2.12"
    assert agy["path"].endswith("agy")
    assert agy["update_hint"].endswith(" update")
    assert agy["install_docs"].startswith("https://antigravity.google/")
    # Sign out is offered, in a window where /logout is typed: agy refuses it headless.
    assert agy["can_sign_out"] is True
    assert agy["signout_in_window"] is True


def test_the_other_clis_sign_out_without_a_window(api) -> None:  # type: ignore[no-untyped-def]
    body = api.client().get("/api/llm/status").json()
    rows = {p["id"]: p for p in body["providers"]}
    for provider in ("claude-subscription", "codex-subscription"):
        assert rows[provider]["can_sign_out"] is True
        assert rows[provider]["signout_in_window"] is False


def test_status_when_signed_out(api, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("FAKE_AGY_MODE", "signed-out")
    assert row(api)["signed_in"] is False


def test_status_with_the_cli_absent(api) -> None:  # type: ignore[no-untyped-def]
    api.services.config.set("llm.antigravity_cli_path", "definitely-not-installed-xyz")
    agy = row(api)
    assert agy["ready"] is False
    assert agy["signed_in"] is None


def test_selecting_antigravity_without_it_is_refused(api) -> None:  # type: ignore[no-untyped-def]
    api.services.config.set("llm.provider", "fake")
    api.services.config.set("llm.antigravity_cli_path", "definitely-not-installed-xyz")
    response = api.client().put("/api/settings", json={"values": {"llm.provider": PROVIDER}})
    assert response.status_code == 409
    assert "Antigravity CLI is not installed" in response.json()["detail"]
    assert api.services.config.get("llm.provider") == "fake"


def test_sign_out_opens_agy_where_logout_is_typed(api) -> None:  # type: ignore[no-untyped-def]
    """/logout is refused headless and ignored as ``-i /logout`` (machine B, 2026-09-28):
    the window runs agy and says what to type."""
    body = api.client().post("/api/llm/signout", json={"provider": PROVIDER}).json()
    assert body["signed_out"] is False, "not yet: that happens in the window"
    assert body["launched"] is False, "off Windows nothing is launched"
    assert "Type /logout and press Enter" in body["command"]
    assert body["log"].endswith("antigravity-signout.log")


def test_install_and_update_take_the_provider(api) -> None:  # type: ignore[no-untyped-def]
    client = api.client()
    install = client.post("/api/llm/install", json={"provider": PROVIDER}).json()
    assert install["launched"] is False, "off Windows nothing is launched"
    assert install["docs"].startswith("https://antigravity.google/")
    update = client.post("/api/llm/update", json={"provider": PROVIDER}).json()
    assert update["command"].endswith(" update")


# ------------------------------------------------------------------ the windowless sign-in


@pytest.fixture
def signed_out(api, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Signed out, with every sign-in stopped when the test ends."""
    from app.api import routes

    monkeypatch.setenv("FAKE_AGY_MODE", "signed-out")
    yield api
    for login in routes._LOGINS.values():
        login.stop()
    routes._LOGINS.clear()


def wait_until(check, timeout: float = 10.0) -> bool:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.05)
    return False


def test_setup_signs_in_with_the_code_pasted_in_the_page(signed_out, agy_log: Path) -> None:  # type: ignore[no-untyped-def]
    """Setup's sign-in (D75): no window; Google's link goes to the page, the code comes
    back through it, and the row turns signed in."""
    client = signed_out.client()
    body = client.post("/api/llm/signin", json={"provider": PROVIDER, "background": True}).json()
    assert body["launched"] is True
    assert body["url"].startswith("https://accounts.google.com/o/oauth2/auth?")
    assert row(signed_out)["console_open"] is True
    assert row(signed_out)["signin_url"] == body["url"]

    sent = client.post("/api/llm/signin/code", json={"provider": PROVIDER, "code": "GOOD-CODE"})
    assert sent.json() == {"sent": True}
    assert wait_until(lambda: row(signed_out)["console_open"] is False), (
        "the run ends once signed in"
    )
    assert row(signed_out)["signed_in"] is True
    signin = [call for call in calls(agy_log) if call["args"][:1] == ["-p"]]
    assert signin and "GEMINI_API_KEY" not in signin[0]["env"], "the sign-in gets no key either"


def test_a_wrong_code_leaves_it_signed_out(signed_out) -> None:  # type: ignore[no-untyped-def]
    client = signed_out.client()
    client.post("/api/llm/signin", json={"provider": PROVIDER, "background": True})
    client.post("/api/llm/signin/code", json={"provider": PROVIDER, "code": "WRONG"})
    assert wait_until(lambda: row(signed_out)["console_open"] is False)
    assert row(signed_out)["signed_in"] is False


def test_cancel_stops_a_waiting_signin(signed_out) -> None:  # type: ignore[no-untyped-def]
    client = signed_out.client()
    client.post("/api/llm/signin", json={"provider": PROVIDER, "background": True})
    stopped = client.post("/api/llm/signin/cancel", json={"provider": PROVIDER})
    assert stopped.json() == {"stopped": True}
    assert row(signed_out)["console_open"] is False


def test_settings_signs_in_in_a_window(api) -> None:  # type: ignore[no-untyped-def]
    """Settings keeps the window, as Claude's does: the code is pasted there."""
    body = api.client().post("/api/llm/signin", json={"provider": PROVIDER}).json()
    assert body["launched"] is False, "off Windows nothing is launched"
    assert "'-p' 'Reply with exactly the word OK.'" in body["command"]
    assert body["log"].endswith("antigravity-signin.log")


# ------------------------------------------------------------------ removal


def test_removing_antigravity_is_one_line_and_its_users_fall_back(
    tmp_path: Path, app_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The day Google says no: delete it from LLM_PROVIDERS and ship."""
    from app import config as config_module
    from app.api import routes

    remaining = tuple(p for p in config_module.LLM_PROVIDERS if p != PROVIDER)
    enums = dict(config_module._ENUMS)
    enums["llm.provider"] = remaining
    enums["llm.fallback_provider"] = ("", *(p for p in remaining if p not in ("fake", "none")))
    monkeypatch.setattr(config_module, "LLM_PROVIDERS", remaining)
    monkeypatch.setattr(config_module, "_ENUMS", enums)
    monkeypatch.setattr(routes, "LLM_PROVIDERS", remaining)
    monkeypatch.setattr(
        routes,
        "CLI_PROVIDERS",
        {k: v for k, v in routes.CLI_PROVIDERS.items() if k != PROVIDER},
    )

    saved = tmp_path / "app_config.json"
    saved.write_text(
        json.dumps({"llm": {"provider": PROVIDER, "fallback_provider": PROVIDER}}), encoding="utf-8"
    )
    config = Config.load(file=saved, environ={})
    assert config.get("llm.provider") == "none"
    assert config.get("llm.fallback_provider") == ""
    assert any(f"'{PROVIDER}' is no longer available" in n for n in config.warnings())

    harness = build_harness(tmp_path / "api")
    body = harness.client().get("/api/llm/status").json()
    assert PROVIDER not in {p["id"] for p in body["providers"]}, "gone from Settings"
    refused = harness.client().put("/api/settings", json={"values": {"llm.provider": PROVIDER}})
    assert refused.status_code >= 400

    with pytest.raises(ConfigError):
        config.set("llm.provider", PROVIDER)
        config.validate()
