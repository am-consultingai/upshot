"""The Codex provider (Codex 1, 2, 3), driven through an injected runner — no subprocess."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from app.config import default_config
from app.errors import PermanentError, QuotaExhausted, RecoverableError, SignInRequired
from app.llm import codex_cli
from app.llm.client import make_client, system_blocks
from app.llm.codex_cli import (
    CodexCliClient,
    drop_nulls,
    install_plan,
    reset_time,
    strict_schema,
    update_command,
)
from app.llm.schema import FREE_SCHEMA, validate

VALID: dict[str, Any] = {"title": "Weekly sync", "summary_html": "<p>we shipped</p>"}
BLOCKS = system_blocks("SYSTEM", "GLOSSARY")
QUOTA = (
    "ERROR: You\u2019ve hit your usage limit. Visit https://chatgpt.com/codex/settings/usage "
    "to purchase more credits or try again at Sep 24, 2026 4:15 PM."
)


class Runner:
    """Answers like `codex exec`: writes the answer file it was given, prints to stdout."""

    def __init__(self, script: list[tuple[int, str, str]], *, answer: Any = VALID) -> None:
        self.script = script
        self.answer = answer
        self.calls: list[dict[str, Any]] = []

    def __call__(self, args, stdin, timeout):  # type: ignore[no-untyped-def]
        args = list(args)
        call: dict[str, Any] = {"args": args, "stdin": stdin, "timeout": timeout}
        if "--output-schema" in args:
            schema_file = Path(args[args.index("--output-schema") + 1])
            call["schema_file"] = schema_file
            call["schema"] = json.loads(schema_file.read_text(encoding="utf-8"))
        code, out, err = self.script[min(len(self.calls), len(self.script) - 1)]
        self.calls.append(call)
        if code == 0 and "--output-last-message" in args and self.answer is not None:
            answer_file = Path(args[args.index("--output-last-message") + 1])
            answer_file.write_text(json.dumps(self.answer), encoding="utf-8")
        return code, out, err


def client_with(tmp_path: Path, runner: Runner) -> CodexCliClient:
    (tmp_path / "codex").write_text("#!/bin/sh\n")
    config = default_config(llm__provider="codex-subscription")
    config.set("llm.codex_cli_path", str(tmp_path / "codex"))
    return CodexCliClient(config, runner=runner)


# ------------------------------------------------------------------ the call


def test_the_argument_list_is_exactly_what_is_intended(tmp_path: Path) -> None:
    runner = Runner([(0, "", "")])
    client = client_with(tmp_path, runner)
    result = client.complete_json(system_blocks=BLOCKS, user="**[00:12] ME:** hello")
    assert result.data == VALID
    args = runner.calls[0]["args"]
    schema_file = str(runner.calls[0]["schema_file"])
    answer_file = args[args.index("--output-last-message") + 1]
    assert args == [
        str(tmp_path / "codex"),
        "exec",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--ephemeral",
        "--ignore-user-config",
        "--color",
        "never",
        "--cd",
        codex_cli.workdir(),
        "--output-schema",
        schema_file,
        "--output-last-message",
        answer_file,
        "-",
    ]
    for dangerous in ("--dangerously-bypass-approvals-and-sandbox", "danger-full-access"):
        assert dangerous not in args
    # The whole prompt goes on stdin; the transcript is never an argument.
    assert runner.calls[0]["stdin"].startswith("SYSTEM")
    assert runner.calls[0]["stdin"].endswith("**[00:12] ME:** hello")
    assert "hello" not in " ".join(args)


def test_the_schema_file_is_written_strict_and_cleaned_up(tmp_path: Path) -> None:
    runner = Runner([(0, "", "")])
    client_with(tmp_path, runner).complete_json(system_blocks=BLOCKS, user="x")
    schema = runner.calls[0]["schema"]
    assert schema["required"] == list(FREE_SCHEMA["properties"]), "strict: every key required"
    assert schema["additionalProperties"] is False
    assert schema["properties"]["title"]["type"] == ["string", "null"], "optional → nullable"
    assert "minLength" not in json.dumps(schema), "strict mode refuses minLength"
    item = schema["properties"]["action_items"]["items"]
    assert set(item["required"]) == set(item["properties"])
    schema_file: Path = runner.calls[0]["schema_file"]
    assert not schema_file.exists(), "the scratch folder is removed afterwards"
    assert Path(codex_cli.workdir()) in schema_file.parents, "and it was ours, not the user's"


def test_strict_nulls_are_stripped_before_validation() -> None:
    answer = {
        "summary_html": "<p>x</p>",
        "title": None,
        "action_items": [{"who": "ME", "what": "y", "due": None, "at_ms": None}],
        "chapters": None,
    }
    relaxed = drop_nulls(answer, FREE_SCHEMA)
    assert "title" not in relaxed and "chapters" not in relaxed
    assert relaxed["action_items"][0]["due"] is None, "nullable in the real schema: kept"
    validate(relaxed, FREE_SCHEMA)
    assert strict_schema({"type": "object", "properties": {}})["required"] == []


def test_stdout_is_the_answer_when_the_file_is_missing(tmp_path: Path) -> None:
    runner = Runner([(0, json.dumps(VALID) + "\n", "progress on stderr")], answer=None)
    assert client_with(tmp_path, runner).complete_json(system_blocks=BLOCKS, user="x").data == VALID


def test_a_malformed_answer_is_repaired_then_permanent(tmp_path: Path) -> None:
    runner = Runner([(0, "", "")], answer="I would rather chat about it")
    with pytest.raises(PermanentError) as raised:
        client_with(tmp_path, runner).complete_json(system_blocks=BLOCKS, user="x")
    assert raised.value.category == "schema"
    assert len(runner.calls) == 3, "the repair loop gets its retries first"


def test_the_api_key_never_reaches_the_child(monkeypatch: pytest.MonkeyPatch) -> None:
    """With it set the CLI may bill the API instead of the plan — silently."""
    for name in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.setenv(name, "sk-should-not-pass")
    monkeypatch.setenv("KEEP_ME", "1")
    env = codex_cli.child_env()
    assert "OPENAI_API_KEY" not in env and "CODEX_API_KEY" not in env
    assert "OPENAI_BASE_URL" not in env
    assert env["KEEP_ME"] == "1"


# ------------------------------------------------------------------ the error taxonomy


@pytest.mark.parametrize(
    ("stderr", "code", "expected"),
    [
        (QUOTA, 1, QuotaExhausted),
        (
            "You\u2019ve hit your usage limit for gpt-5.5-codex. Switch to another model now",
            1,
            QuotaExhausted,
        ),
        ("You hit your spend cap. Try again later.", 1, QuotaExhausted),
        ("Error: Not logged in. Run `codex login` first.", 1, SignInRequired),
        ("stream error: 401 Unauthorized: refresh token was revoked", 1, SignInRequired),
        ("exceeded retry limit, last status: 429 Too Many Requests", 1, RecoverableError),
        ("stream disconnected before completion", 1, RecoverableError),
        ("something nobody anticipated", 3, RecoverableError),
        ("", 0, RecoverableError),  # exit 0 and no answer at all
    ],
)
def test_each_failure_maps_to_the_right_error(
    tmp_path: Path, stderr: str, code: int, expected: type[Exception]
) -> None:
    runner = Runner([(code, "", stderr)], answer=None)
    with pytest.raises(expected) as raised:
        client_with(tmp_path, runner).complete_json(system_blocks=BLOCKS, user="x")
    if expected is RecoverableError:
        assert not isinstance(raised.value, QuotaExhausted | SignInRequired)


@pytest.mark.parametrize(
    ("stderr", "category"),
    [
        ("error: unexpected argument '--ephemeral' found", "setup"),
        ("Your request was flagged as potentially violating our usage policies", "refusal"),
    ],
)
def test_what_retrying_cannot_fix_is_permanent(tmp_path: Path, stderr: str, category: str) -> None:
    runner = Runner([(2, "", stderr)], answer=None)
    with pytest.raises(PermanentError) as raised:
        client_with(tmp_path, runner).complete_json(system_blocks=BLOCKS, user="x")
    assert raised.value.category == category
    assert len(runner.calls) == 1, "and it is not retried inside the call either"


def test_a_timeout_is_recoverable(tmp_path: Path) -> None:
    import subprocess

    def slow(args, stdin, timeout):  # type: ignore[no-untyped-def]
        raise subprocess.TimeoutExpired(args, timeout)

    client = client_with(tmp_path, Runner([(0, "", "")]))
    client.runner = slow
    with pytest.raises(RecoverableError, match="timed out"):
        client.complete_json(system_blocks=BLOCKS, user="x")


def test_the_quota_message_is_plain_words_with_the_reset(tmp_path: Path) -> None:
    runner = Runner([(1, "", QUOTA)], answer=None)
    with pytest.raises(QuotaExhausted) as raised:
        client_with(tmp_path, runner).complete_json(system_blocks=BLOCKS, user="x")
    message = str(raised.value)
    assert message.startswith("Your ChatGPT plan's Codex allowance is used up")
    assert "it resets at Sep 24, 2026 4:15 PM" in message
    assert "chatgpt.com" not in message, "the CLI's own text is not what the user reads"
    assert raised.value.provider == "codex-subscription"
    assert raised.value.retry_at is not None
    assert (raised.value.retry_at.month, raised.value.retry_at.day) == (9, 24)
    assert raised.value.retry_at.hour == 16


def test_reset_times_in_each_format() -> None:
    now = datetime.fromisoformat("2026-09-23T14:00:00+03:00")
    later, _ = reset_time("Try again at 4:15 PM.", now)
    assert later is not None and later.isoformat() == "2026-09-23T16:15:00+03:00"
    tomorrow, _ = reset_time("Try again at 9:00 AM.", now)
    assert tomorrow is not None and tomorrow.day == 24, "a time already past is tomorrow's"
    ordinal, _ = reset_time("or try again at Sep 24th, 2026 5:47 AM.", now)
    assert ordinal is not None and ordinal.isoformat() == "2026-09-24T05:47:00+03:00"
    comma, _ = reset_time("try again at Aug 20, 2026, 7:38 AM", now)
    assert comma is not None and (comma.month, comma.day, comma.hour) == (8, 20, 7)
    assert reset_time("You've hit your usage limit. Try again later.", now) == (None, "")
    unknown, words = reset_time("try again at the end of the week.", now)
    assert unknown is None and words == "the end of the week", "kept for the message"
    assert reset_time("no reset mentioned", now) == (None, "")


# ------------------------------------------------------------------ status (Codex 2)


def test_status_when_signed_in_through_chatgpt(tmp_path: Path) -> None:
    runner = Runner([(0, "codex-cli 0.156.1\n", ""), (0, "", "Logged in using ChatGPT\n")])
    status = client_with(tmp_path, runner).status()
    assert status.installed and status.version == "codex-cli 0.156.1"
    assert status.signed_in is True and status.account == "ChatGPT account"
    assert runner.calls[1]["args"][1:] == ["login", "status"]


def test_status_when_signed_out(tmp_path: Path) -> None:
    runner = Runner([(0, "codex-cli 0.156.1\n", ""), (1, "", "Not logged in\n")])
    assert client_with(tmp_path, runner).status().signed_in is False


def test_an_old_build_is_unknown_not_signed_out(tmp_path: Path) -> None:
    runner = Runner(
        [(0, "codex-cli 0.1.0\n", ""), (2, "", "error: unrecognized subcommand 'status'")]
    )
    status = client_with(tmp_path, runner).status()
    assert status.installed is True
    assert status.signed_in is None, "unknown is not signed out"


def test_an_api_key_sign_in_is_said_and_the_key_is_not(tmp_path: Path) -> None:
    runner = Runner(
        [(0, "codex-cli 0.156.1\n", ""), (0, "", "Logged in using an API key - sk-proj-***ABCD")]
    )
    status = client_with(tmp_path, runner).status()
    assert status.signed_in is True
    assert "billed to the API" in status.account
    assert "ABCD" not in json.dumps(status.as_dict()), "no part of the key is repeated"


def test_status_when_missing() -> None:
    config = default_config()
    config.set("llm.codex_cli_path", "definitely-not-installed-xyz")
    client = CodexCliClient(config)
    assert client.status().installed is False
    with pytest.raises(PermanentError, match="not installed"):
        client.complete_json(system_blocks=BLOCKS, user="x")


def test_login_is_codex_login_and_nothing_else(tmp_path: Path) -> None:
    client = client_with(tmp_path, Runner([(0, "", "")]))
    assert client.login_command() == [str(tmp_path / "codex"), "login"]
    script = codex_cli.login_console([r"C:\Users\am\App Data\codex.exe", "login"])[-1]
    assert "'C:\\Users\\am\\App Data\\codex.exe'" in script, "a path with a space is quoted"


# ------------------------------------------------------------------ install (Codex 2)


def test_install_uses_openais_installer_where_powershell_can(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(codex_cli.sys, "platform", "win32")
    monkeypatch.setattr(codex_cli, "powershell_language_mode", lambda: "FullLanguage")
    plan = install_plan()
    assert plan is not None and plan.method == "native"
    assert "https://chatgpt.com/codex/install.ps1" in plan.display
    assert "CODEX_NON_INTERACTIVE=1" in plan.display, "no 'Start Codex now?' in our window"
    script = plan.argv[-1]
    assert plan.display in script, "what runs is what was shown"
    assert "Start-Job" not in script, "the installer's streams stay on the console"
    assert "login" in script, "and it signs in without a second trip"


def test_install_falls_back_to_npm_under_constrained_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(codex_cli.sys, "platform", "win32")
    monkeypatch.setattr(codex_cli, "powershell_language_mode", lambda: "ConstrainedLanguage")
    monkeypatch.setattr(codex_cli.shutil, "which", lambda name: "C:/npm.cmd")
    plan = install_plan()
    assert plan is not None and plan.method == "npm"
    assert plan.display == "npm install -g @openai/codex"
    assert "irm" not in plan.argv[-1], "iex cannot run here"
    monkeypatch.setattr(codex_cli.shutil, "which", lambda name: None)
    monkeypatch.setattr(codex_cli, "winget_works", lambda: True)
    plan = install_plan()
    assert plan is not None and plan.method == "winget", "winget only after npm"
    assert "--id OpenAI.Codex --exact" in plan.display
    monkeypatch.setattr(codex_cli, "winget_works", lambda: False)
    assert install_plan() is None, "nothing that can run: say so rather than pretend"


def test_install_is_windows_only() -> None:
    import sys

    if sys.platform != "win32":
        assert install_plan() is None


def test_update_is_pinned_to_the_install() -> None:
    assert update_command(r"C:\Users\am\AppData\Roaming\npm\codex.cmd").startswith("npm install")
    native = r"C:\Users\am\AppData\Local\Programs\OpenAI\Codex\bin\codex.exe"
    assert update_command(native) == f'"{native}" update'
    links = r"C:\Users\am\AppData\Local\Microsoft\WinGet\Links\codex.exe"
    assert update_command(links) == "winget upgrade --id OpenAI.Codex --exact"


# ------------------------------------------------------------------ the credential


def test_codex_cli_never_sees_a_credential() -> None:
    """The whole point: the app holds no token, it just runs the signed-in CLI."""
    import inspect

    source = inspect.getsource(codex_cli)
    for marker in (
        "keyring",
        "api_key",
        "Authorization",
        "auth.json",
        "session_token",
        ".credentials",
    ):
        assert marker not in source, f"{marker} must not appear in the subscription provider"
    assert "OPENAI_API_KEY" in source, "the child env must have the key stripped"


def test_selected_by_config_and_never_for_a_sensitive_meeting() -> None:
    config = default_config(llm__provider="codex-subscription")
    assert make_client(config).name == "codex-subscription"
    assert make_client(config, sensitive=True).name == "ollama"
    assert default_config().get("llm.provider") != "codex-subscription", "never the default"
