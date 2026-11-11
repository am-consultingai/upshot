"""What a crash report says about a failure beyond its stack (owner, 2026-11-11: as much as
is relevant): a fixed list of facts, scrubbed, and nothing a model wrote."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

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


def test_a_fallbacks_failure_never_borrows_the_first_providers_facts() -> None:
    """Claude signed out, Codex the fallback failing: the report is Codex's alone."""
    try:
        try:
            raise describe(
                RecoverableError("signed out"),
                provider="claude-subscription",
                cli_message="Not logged in",
                model_reached=False,
                cli_version="2.1.263",
            )
        except RecoverableError:
            # As summarize's fallback does: inside the except, so Claude's is its context.
            raise describe(  # noqa: B904
                RecoverableError("Codex exited 1"), provider="codex-subscription", exit_code=1
            )
    except RecoverableError as outer:
        facts = scrub.facts(outer)
    assert facts == {"provider": "codex-subscription", "exit_code": 1}


def test_job_facts_go_only_when_a_provider_was_involved() -> None:
    """Privacy Policy §6: language and length band only for an AI provider's failure."""
    transcription = describe(RuntimeError("x"), stage="transcribe", language="he", attempt=5)
    assert scrub.facts(transcription) == {}
    summary = describe(
        RuntimeError("x"),
        provider="gemini",
        stage="summarize",
        language="he",
        meeting_minutes="<30",
    )
    assert scrub.facts(summary) == {
        "provider": "gemini",
        "stage": "summarize",
        "language": "he",
        "meeting_minutes": "<30",
    }


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
        provider="claude-subscription",
        cli_message=(
            'Your organization Acme Ltd has disabled Claude Code for account "dana@acme.io"'
        ),
    )
    message = scrub.facts(error)["cli_message"]
    assert "Acme" not in message and "dana" not in message
    assert message.startswith("Your organization … has disabled Claude Code")


def test_codex_never_keeps_a_line_of_the_prompt_it_echoed() -> None:
    """A transcript window can start mid-sentence with "errors…" or even "Error: ": the
    echoed prompt is not Codex's own words, whatever it looks like."""
    prompt = (
        "Summarize.\n\nTranscript:\nerrors in the payroll export for Dana Cohen\nError: the board"
    )
    stderr = prompt + "\nERROR: stream disconnected before completion\n"
    assert own_error(stderr, prompt) == "ERROR: stream disconnected before completion"
    assert own_error(prompt, prompt) == ""


def test_once_reached_not_even_the_error_stream_is_the_message() -> None:
    envelope = json.loads(EXPIRED_ENVELOPE)
    envelope.update(duration_api_ms=900)
    try:
        ClaudeCliClient._raise_for(1, json.dumps(envelope), "[debug] prompt: Thursday's launch")
    except RecoverableError as exc:
        assert "cli_message" not in scrub.facts(exc)


def test_a_hook_that_blocked_the_prompt_is_never_quoted() -> None:
    """The model never reached, but the reason is a hook's, which can quote the prompt:
    only the structured fields go."""
    envelope = json.loads(EXPIRED_ENVELOPE)
    envelope.update(
        terminal_reason="hook_blocked",
        result="Blocked: prompt mentions Project Falcon and Dana Cohen",
    )
    try:
        ClaudeCliClient._raise_for(1, json.dumps(envelope), "warning: something on stderr")
    except RecoverableError as exc:
        facts = scrub.facts(exc)
    assert facts["model_reached"] is False and facts["terminal_reason"] == "hook_blocked"
    assert "cli_message" not in facts


def test_claude_api_errors_keep_their_reason() -> None:
    error = describe(
        RuntimeError("x"),
        provider="claude-subscription",
        cli_message=(
            'API Error: 401 {"type":"error","error":{"type":"authentication_error",'
            '"message":"OAuth token has expired."},"request_id":"req_1"}'
        ),
    )
    assert scrub.facts(error)["cli_message"] == (
        "API Error: 401 authentication_error: OAuth token has expired."
    )


@pytest.mark.parametrize(
    ("count", "band"),
    [(0, "1"), (1, "1"), (2, "2-3"), (3, "2-3"), (4, "4-8"), (8, "4-8"), (9, "9+")],
)
def test_summary_windows_go_in_bands(count: int, band: str) -> None:
    from app.pipeline.stages.summarize import windows_band

    assert windows_band(count) == band


@pytest.mark.parametrize(
    ("seconds", "band"),
    [(None, None), (0, None), (60, "<15"), (15 * 60, "<30"), (120 * 60, "120+")],
)
def test_meeting_length_goes_in_bands(seconds: int | None, band: str | None) -> None:
    from app.pipeline.worker import _minutes_bucket

    assert _minutes_bucket(seconds) == band


def test_the_cli_version_is_asked_again_after_an_update(tmp_path: Path) -> None:
    import os

    from app.config import default_config
    from app.llm import claude_cli

    program = tmp_path / "claude"
    program.write_text("#!/bin/sh\n")
    answers = iter(["2.1.263 (Claude Code)", "2.1.300 (Claude Code)"])
    asked: list[str] = []

    def runner(args: list[str], _stdin: str, _timeout: float) -> tuple[int, str, str]:
        asked.append(args[-1])
        return 0, next(answers), ""

    client = ClaudeCliClient(default_config(), runner=runner)
    claude_cli._VERSIONS.clear()
    assert client._version(str(program)) == "2.1.263 (Claude Code)"
    assert client._version(str(program)) == "2.1.263 (Claude Code)", "kept"
    stat = program.stat()
    os.utime(program, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000))  # updated
    assert client._version(str(program)) == "2.1.300 (Claude Code)"
    assert len(asked) == 2
