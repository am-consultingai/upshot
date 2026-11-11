"""What a crash report says about a failure beyond its stack (owner, 2026-11-11: as much as
is relevant): a fixed list of facts, scrubbed, and nothing a model wrote."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.diagnostics import scrub
from app.errors import PermanentError, RecoverableError, describe
from app.llm.claude_cli import ClaudeCliClient
from app.llm.codex_cli import own_error
from tests.unit.test_diagnostics import reporter
from tests.unit.test_llm_providers import EXPIRED_ENVELOPE


def test_only_listed_facts_go_and_strings_are_scrubbed() -> None:
    error = describe(
        RuntimeError("x"),
        provider="claude-subscription",
        exit_code=1,
        cli_message="failed at C:\\Users\\someone\\AppData\\x.json for someone@example.com",
        transcript="Let's ship on Thursday",  # not a listed fact: never sent
    )
    facts = scrub.facts(error)
    assert set(facts) == {"provider", "exit_code", "cli_message"}
    assert "someone@example.com" not in facts["cli_message"]
    assert "Thursday" not in json.dumps(facts)


def test_facts_from_the_chain_and_the_first_one_wins() -> None:
    try:
        try:
            raise describe(ValueError("inner"), provider="codex-subscription", exit_code=2)
        except ValueError as inner:
            raise describe(RecoverableError("outer"), exit_code=9) from inner
    except RecoverableError as outer:
        facts = scrub.facts(outer)
    assert facts == {"exit_code": 9, "provider": "codex-subscription"}


def test_the_expired_sign_in_reports_why_the_cli_stopped() -> None:
    """The Tableau failure: Sentry learns what the CLI said, since the model was never
    reached and so nothing a model wrote can be in it."""
    try:
        ClaudeCliClient._raise_for(1, EXPIRED_ENVELOPE, "")
    except RecoverableError as exc:
        facts = scrub.facts(exc)
    assert facts["provider"] == "claude-subscription"
    assert facts["exit_code"] == 1
    assert facts["model_reached"] is False
    assert facts["terminal_reason"] == "api_error"
    assert facts["cli_message"].startswith("Failed to authenticate")


def test_once_the_model_was_reached_its_answer_is_never_the_message() -> None:
    envelope = json.loads(EXPIRED_ENVELOPE)
    envelope.update(duration_api_ms=1200, result="Here is your summary of Thursday's launch")
    try:
        ClaudeCliClient._raise_for(1, json.dumps(envelope), "")
    except RecoverableError as exc:
        facts = scrub.facts(exc)
    assert facts["model_reached"] is True
    assert "cli_message" not in facts


def test_codex_keeps_only_its_error_lines() -> None:
    stderr = "User instructions:\nSummarize: Let's ship on Thursday\nERROR: stream disconnected\n"
    assert own_error(stderr) == "ERROR: stream disconnected"


def test_the_report_carries_the_facts_and_tags_the_provider(tmp_path: Path) -> None:
    made, _sent = reporter(tmp_path)
    error = describe(
        PermanentError("Claude Code exited 1: <secret>"),
        provider="claude-subscription",
        stage="summarize",
        attempt=5,
        meeting_minutes="<30",
    )
    event = made.report_exception(error, where="stage:summarize")
    assert event is not None
    assert event["contexts"]["failure"] == {
        "provider": "claude-subscription",
        "stage": "summarize",
        "attempt": 5,
        "meeting_minutes": "<30",
    }
    assert event["tags"]["provider"] == "claude-subscription"


def test_a_hand_sent_report_goes_alone_through_its_own_outbox(tmp_path: Path) -> None:
    """Never with a report kept automatically, and not dropped when reports are off."""
    made, _ = reporter(tmp_path, consent="off")
    made._send = None  # the real client, with an outbox per kind
    posted: list[Any] = []

    class Client:
        def __init__(self, _dsn: str, outbox: Path, **_: Any) -> None:
            self.outbox = outbox

        def send(self, _kind: str, event: dict[str, Any]) -> bool:
            posted.append((self.outbox.name, event["event_id"]))
            return True

    import app.diagnostics.client as client_module

    original = client_module.SentryClient
    client_module.SentryClient = Client  # type: ignore[assignment,misc]
    try:
        event = made.prepare_exception(RuntimeError("boom"), where="stage:summarize")
        assert event is not None
        assert made.send_prepared(event)
    finally:
        client_module.SentryClient = original  # type: ignore[misc]
    assert posted == [("outbox-approved", event["event_id"])]
    assert made.forget_kept() == 0, "switching reports off drops nothing approved"


def test_a_cli_message_loses_account_and_organisation_names() -> None:
    error = describe(
        RuntimeError("x"),
        cli_message=(
            'Your organization Acme Ltd has disabled Claude Code for account "dana@acme.io"'
        ),
    )
    message = scrub.facts(error)["cli_message"]
    assert "Acme" not in message and "dana" not in message
    assert message.startswith("Your organization … has disabled Claude Code")
