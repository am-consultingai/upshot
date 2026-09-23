"""The alternative summarization providers, driven through injected transports."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

from app.config import Config, default_config
from app.errors import PermanentError, RecoverableError
from app.llm.claude_cli import (
    ClaudeCliClient,
    install_candidates,
    install_plan,
    update_command,
    workdir,
)
from app.llm.client import make_client, system_blocks
from app.llm.gemini_client import GeminiClient
from app.llm.openai_client import OpenAiClient
from app.llm.repair import complete_with_repair, extract_json, schema_instruction, strip_fence
from app.llm.schema import FREE_SCHEMA

#: The only shape still imposed anywhere: a JSON envelope round one free-form document.
VALID: dict[str, Any] = {
    "title": "Weekly sync",
    "summary_html": "<h1>Weekly sync</h1><p>we shipped, nothing broke</p>",
}
BLOCKS = system_blocks("SYSTEM", "GLOSSARY")


# ------------------------------------------------------------------ repair loop


def test_strip_fence_and_extract() -> None:
    assert strip_fence('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json('```\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! {"a": 1} hope that helps') == {"a": 1}
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_repair_loop_feeds_the_error_back() -> None:
    replies = ["not json at all", json.dumps({"title": "x"}), json.dumps(VALID)]
    seen: list[str] = []

    def send(message: str) -> str:
        seen.append(message)
        return replies.pop(0)

    data, attempts = complete_with_repair(send, "TRANSCRIPT", FREE_SCHEMA, provider="test")
    assert data["title"] == "Weekly sync"
    assert attempts == 3
    assert "rejected" in seen[1] and "rejected" in seen[2]
    assert seen[0] == "TRANSCRIPT"


def test_repair_loop_gives_up_permanently() -> None:
    with pytest.raises(PermanentError, match="schema-valid JSON"):
        complete_with_repair(lambda m: "nope", "x", FREE_SCHEMA, max_repairs=1, provider="test")


def test_schema_instruction_carries_the_schema() -> None:
    text = schema_instruction(FREE_SCHEMA)
    assert "JSON Schema" in text and '"summary_html"' in text


# ------------------------------------------------------------------ claude CLI


def fake_runner(script: list[tuple[int, str, str]]):  # type: ignore[no-untyped-def]
    calls: list[dict[str, Any]] = []

    def run(args, stdin, timeout):  # type: ignore[no-untyped-def]
        calls.append({"args": list(args), "stdin": stdin, "timeout": timeout})
        return script[min(len(calls) - 1, len(script) - 1)]

    run.calls = calls  # type: ignore[attr-defined]
    return run


def envelope(payload: Any) -> str:
    return json.dumps({"type": "result", "is_error": False, "result": json.dumps(payload)})


def test_claude_cli_summarizes_through_the_subprocess(tmp_path) -> None:  # type: ignore[no-untyped-def]
    runner = fake_runner([(0, envelope(VALID), "")])
    config = default_config(llm__provider="claude-subscription")
    config.set("llm.claude_cli_path", str(tmp_path / "claude"))
    (tmp_path / "claude").write_text("#!/bin/sh\n")
    client = ClaudeCliClient(config, runner=runner)

    result = client.complete_json(system_blocks=BLOCKS, user="**[00:12] ME:** hello")
    assert result.data["title"] == "Weekly sync"
    call = runner.calls[0]  # type: ignore[attr-defined]
    assert "-p" in call["args"] and "--output-format" in call["args"]
    assert call["args"][call["args"].index("--output-format") + 1] == "json"
    # the transcript is piped, never passed as an argument
    assert call["stdin"] == "**[00:12] ME:** hello"
    assert "**[00:12] ME:** hello" not in " ".join(call["args"])


def test_claude_cli_disables_tools_by_default(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Summarizing a transcript has no business reading or writing the user's disk."""
    (tmp_path / "claude").write_text("#!/bin/sh\n")
    config = default_config()
    config.set("llm.claude_cli_path", str(tmp_path / "claude"))
    args = ClaudeCliClient(config).build_args(str(tmp_path / "claude"))
    assert "--disallowed-tools" in args
    denied = args[args.index("--disallowed-tools") + 1]
    for tool in ("Bash", "Read", "Write", "Edit", "WebFetch"):
        assert tool in denied
    assert "--dangerously-skip-permissions" not in args


def test_claude_cli_reports_a_missing_install() -> None:
    config = default_config()
    config.set("llm.claude_cli_path", "definitely-not-installed-xyz")
    client = ClaudeCliClient(config)
    assert client.status().installed is False
    with pytest.raises(PermanentError, match="not installed"):
        client.complete_json(system_blocks=BLOCKS, user="x")


def test_claude_cli_maps_not_signed_in(tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "claude").write_text("#!/bin/sh\n")
    config = default_config()
    config.set("llm.claude_cli_path", str(tmp_path / "claude"))
    client = ClaudeCliClient(
        config, runner=fake_runner([(1, "", "Not logged in. Please run /login")])
    )
    with pytest.raises(PermanentError, match="not signed in"):
        client.complete_json(system_blocks=BLOCKS, user="x")


def test_claude_cli_maps_rate_limit(tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "claude").write_text("#!/bin/sh\n")
    config = default_config()
    config.set("llm.claude_cli_path", str(tmp_path / "claude"))
    client = ClaudeCliClient(config, runner=fake_runner([(1, "", "Rate limit exceeded (429)")]))
    with pytest.raises(RecoverableError, match="rate limited"):
        client.complete_json(system_blocks=BLOCKS, user="x")


def test_claude_cli_knows_a_spent_plan_from_a_rate_limit(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The plan's allowance waits for its reset (or the fallback); a 429 just backs off."""
    from app.errors import QuotaExhausted

    (tmp_path / "claude").write_text("#!/bin/sh\n")
    config = default_config()
    config.set("llm.claude_cli_path", str(tmp_path / "claude"))
    spent = ClaudeCliClient(
        config, runner=fake_runner([(1, "", "Claude AI usage limit reached|1790000000")])
    )
    with pytest.raises(QuotaExhausted) as raised:
        spent.complete_json(system_blocks=BLOCKS, user="x")
    assert raised.value.provider == "claude-subscription"
    assert raised.value.retry_at is not None and raised.value.retry_at.timestamp() == 1790000000
    assert "allowance is used up" in str(raised.value)
    assert "1790000000" not in str(raised.value), "plain words, not the CLI's"


def test_claude_cli_status_reports_the_version(tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "claude").write_text("#!/bin/sh\n")
    config = default_config()
    config.set("llm.claude_cli_path", str(tmp_path / "claude"))
    client = ClaudeCliClient(config, runner=fake_runner([(0, "2.1.211 (Claude Code)\n", "")]))
    status = client.status()
    assert status.installed is True
    assert status.version == "2.1.211 (Claude Code)"
    assert status.as_dict()["installed"] is True


# The 2.1.4 build that shipped without `auth` is reproduced here rather than kept as a
# 230 MB binary: these two stubs are the whole difference between the CLI generations.
OLD_CLI = """#!/bin/sh
case "$1" in
  --version) echo "2.1.4 (Claude Code)"; exit 0;;
  auth) echo "error: unknown command 'auth'" >&2; exit 1;;
esac
echo '{"type":"result","is_error":false,"result":"{}"}'
"""

NEW_CLI = """#!/bin/sh
case "$1" in
  --version) echo "2.1.260 (Claude Code)"; exit 0;;
  auth) echo '{"loggedIn":true,"email":"you@example.com","subscriptionType":"max"}'; exit 0;;
esac
echo '{"type":"result","is_error":false,"result":"{}"}'
"""


def stub_cli(tmp_path, script: str):  # type: ignore[no-untyped-def]
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "claude"
    path.write_text(script)
    path.chmod(0o755)
    config = default_config()
    config.set("llm.claude_cli_path", str(path))
    return ClaudeCliClient(config), str(path)


def test_claude_cli_spawns_outside_the_working_directory() -> None:
    """The EISDIR crash: Claude Code watches `.claude/`, and cannot over a UNC path."""
    where = workdir()
    assert where != os.getcwd()
    assert not where.startswith("\\\\"), "a UNC cwd is what broke the watcher"
    assert Path(where).is_dir()


def test_claude_cli_reports_sign_in_on_a_modern_build(tmp_path) -> None:  # type: ignore[no-untyped-def]
    client, _ = stub_cli(tmp_path, NEW_CLI)
    status = client.status()
    assert status.signed_in is True
    assert "you@example.com" in status.account and "max" in status.account


def test_claude_cli_old_build_is_unknown_not_signed_out(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """`None` and `False` must not collapse: only one of them is worth nagging about."""
    client, _ = stub_cli(tmp_path, OLD_CLI)
    status = client.status()
    assert status.installed is True
    assert status.signed_in is None, "a build that cannot answer has not answered 'no'"


def test_claude_cli_login_command_follows_the_build(tmp_path) -> None:  # type: ignore[no-untyped-def]
    modern, modern_path = stub_cli(tmp_path / "new", NEW_CLI)
    assert modern.login_command() == [modern_path, "auth", "login"]
    old, old_path = stub_cli(tmp_path / "old", OLD_CLI)
    assert old.login_command() == [old_path], "no `auth` subcommand to fall back from"


def test_claude_cli_prefers_a_real_executable_over_a_shim(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """An npm install leaves claude.cmd, and batch shims route argv through cmd.exe."""
    from app.llm import claude_cli

    exe, cmd = tmp_path / "claude.exe", tmp_path / "claude.cmd"
    for candidate in (cmd, exe):  # shim first, so order alone cannot pass this
        candidate.write_text("#!/bin/sh\n")
    monkeypatch.setattr(claude_cli, "install_candidates", lambda: [cmd, exe])
    monkeypatch.setattr(claude_cli.shutil, "which", lambda _name: None)
    assert ClaudeCliClient(default_config()).resolve() == str(exe)


def test_winget_portable_install_is_found_off_the_path(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """winget installs this package portable, and often puts nothing on PATH at all.

    The symlink in ``WinGet\\Links`` is only created where the machine permits symlinks,
    so on an ordinary non-admin install the binary exists under ``Packages`` and no
    lookup by name can reach it. A successful install then reads as "not installed".
    """
    packages = tmp_path / "Microsoft" / "WinGet" / "Packages"
    installed = packages / "Anthropic.ClaudeCode_Microsoft.Winget.Source_8wekyb3d8bbwe"
    installed.mkdir(parents=True)
    (installed / "claude.exe").write_text("#!/bin/sh\n")
    (tmp_path / "Microsoft" / "WinGet" / "Links").mkdir(parents=True)  # present but empty
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert installed / "claude.exe" in install_candidates()


def test_claude_cli_maps_an_unknown_option_to_update_not_retry(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Retrying an argument the build cannot parse burns a meeting to learn nothing."""
    (tmp_path / "claude").write_text("#!/bin/sh\n")
    config = default_config()
    config.set("llm.claude_cli_path", str(tmp_path / "claude"))
    client = ClaudeCliClient(
        config, runner=fake_runner([(1, "", "error: unknown option '--disallowed-tools'")])
    )
    with pytest.raises(PermanentError, match="Update it"):
        client.complete_json(system_blocks=BLOCKS, user="x")


def test_install_plan_never_runs_anything_undisclosed(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The line shown beside the button must be the line that actually runs."""
    from app.llm import claude_cli

    monkeypatch.setattr(claude_cli.sys, "platform", "win32")
    monkeypatch.setattr(claude_cli, "powershell_language_mode", lambda: "FullLanguage")
    plan = install_plan()
    assert plan is not None
    assert plan.display in " ".join(plan.argv), "the shown line must be the run line"
    assert "-NoExit" in plan.argv, "a failure must stay readable after the run"
    assert "Bypass" not in " ".join(plan.argv), "-Command needs no policy change"
    assert '"' not in plan.argv[-1], "double quotes do not survive the Windows command line"


def test_install_plan_falls_back_where_iex_cannot_run(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Constrained Language Mode has no Invoke-Expression, so `irm | iex` cannot work."""
    from app.llm import claude_cli

    monkeypatch.setattr(claude_cli.sys, "platform", "win32")
    monkeypatch.setattr(claude_cli, "powershell_language_mode", lambda: "ConstrainedLanguage")

    monkeypatch.setattr(claude_cli, "winget_works", lambda: True)
    plan = install_plan()
    assert plan is not None and plan.method == "winget"

    monkeypatch.setattr(claude_cli, "winget_works", lambda: False)
    assert install_plan() is None, "no winget either: the UI must offer the guide, not a run"


def test_install_never_captures_the_installers_streams(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A regression with an expensive lesson: the installer's streams stay untouched.

    Wrapping it in ``Start-Job`` to drive a progress bar captured them. PowerShell turns a
    native command's stderr into error records once captured, and ``install.ps1`` sets
    ``$ErrorActionPreference = 'Stop'`` — so the first thing ``claude.exe install`` wrote
    to stderr aborted the install. It downloaded 220 MB, verified the checksum, then left
    a zero-byte version stub and no launcher.

    Progress, if it ever comes back, belongs in the application's UI — see the next test
    for why not even a sibling process can paint it.
    """
    from app.llm import claude_cli

    monkeypatch.setattr(claude_cli.sys, "platform", "win32")
    monkeypatch.setattr(claude_cli, "powershell_language_mode", lambda: "FullLanguage")
    script = install_plan().argv[-1]  # type: ignore[union-attr]
    for forbidden in ("Start-Job", "Receive-Job", "2>&1", "| Out-String"):
        assert forbidden not in script, f"{forbidden} captures the installer's streams"


def test_install_command_line_nests_no_powershell(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Antivirus reads this command line before anything in it runs.

    A spinner used to run beside the installer as a nested
    ``Start-Process powershell -Command '...while($true)...'``. Combined with
    ``irm <url> | iex`` that is the shape of a fileless dropper, and Microsoft Defender
    scored the whole line as ``Trojan:Win32/Commando.A!ml`` and refused to create the
    process: ``WinError 5``, surfacing as "could not start the native install". Removing
    the nesting was enough to make the identical install start.

    Progress belongs in the application's UI, not in a command line something else has to
    judge.
    """
    from app.llm import claude_cli

    monkeypatch.setattr(claude_cli.sys, "platform", "win32")
    monkeypatch.setattr(claude_cli, "powershell_language_mode", lambda: "FullLanguage")
    script = install_plan().argv[-1]  # type: ignore[union-attr]
    assert "Start-Process powershell" not in script, "a nested shell is what got it blocked"
    assert "while($true)" not in script and "while ($true)" not in script
    # Still one flat argument on a Windows command line: no quoting it would eat.
    assert '"' not in script and "`" not in script


def test_language_mode_is_probed_once(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The settings page asks while polling; policy cannot change under a running app."""
    from app.llm import claude_cli

    calls: list[int] = []

    class Completed:
        returncode = 0
        stdout = "FullLanguage"

    monkeypatch.setattr(claude_cli.sys, "platform", "win32")
    monkeypatch.setattr(claude_cli, "_LANGUAGE_MODE", None)
    monkeypatch.setattr(
        claude_cli.subprocess, "run", lambda *a, **k: (calls.append(1), Completed())[1]
    )
    assert claude_cli.powershell_language_mode() == "FullLanguage"
    assert claude_cli.powershell_language_mode() == "FullLanguage"
    assert len(calls) == 1, "one spawn, however often the row is polled"


def test_install_signs_in_without_a_second_trip(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Installing alone would send the user back to the app to discover step two."""
    from app.llm import claude_cli

    monkeypatch.setattr(claude_cli.sys, "platform", "win32")
    monkeypatch.setattr(claude_cli, "powershell_language_mode", lambda: "FullLanguage")
    script = install_plan().argv[-1]  # type: ignore[union-attr]
    assert "auth login" in script, "the same window must carry on into the login"
    # The installer edits the *user's* PATH, which this running shell cannot yet see.
    assert "GetEnvironmentVariable('Path','User')" in script
    assert "WinGet\\Packages" in script, "a winget install is on no PATH at all"


def test_update_command_is_pinned_to_the_resolved_binary() -> None:
    """A bare `claude update` upgrades whichever install PATH happens to favour."""
    assert update_command(r"C:\Users\am\.local\bin\claude.exe").startswith('"C:')
    assert "winget upgrade" in update_command(
        r"C:\Users\am\AppData\Local\Microsoft\WinGet\Links\claude.exe"
    )


def test_login_console_survives_powershell_quoting() -> None:
    """Paths have spaces, and a message split across Python lines can grow a quote.

    Splitting a single-quoted PowerShell string across two Python literals produces '',
    which PowerShell reads as an escaped quote and prints back at the user mid-sentence.
    """
    from app.llm.claude_cli import login_console

    script = login_console([r"C:\Users\am\App Data\claude.exe", "auth", "login"])[-1]
    assert "'C:\\Users\\am\\App Data\\claude.exe'" in script, "a path with a space must be quoted"
    # `Write-Host ''` is a legitimate empty line. The hazard is an escaped quote *inside*
    # prose, which is what a Python line split through a quoted string produces.
    assert re.search(r"[A-Za-z]''|''[A-Za-z]", script) is None, "a stray quote mid-sentence"
    assert '"' not in script, "double quotes do not survive the Windows command line"


def test_every_spawn_declares_utf8(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Hebrew has to survive the pipe. Python's text mode does not guarantee that.

    Without an explicit encoding, text mode uses the locale's — cp1252 on Windows — and
    a Hebrew transcript cannot be encoded into the child's stdin at all. It surfaced as
    "'charmap' codec can't encode characters in position 18-23", retried five times, and
    never reached the model. The suite cannot catch this by running the real thing:
    POSIX defaults to UTF-8, so it only ever failed on the machine it shipped to.
    """
    from app.llm import claude_cli

    seen: list[dict[str, Any]] = []

    class Completed:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def spy(*_args, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(kwargs)
        return Completed()

    monkeypatch.setattr(claude_cli.subprocess, "run", spy)
    claude_cli.subprocess_runner(["claude", "-p"], "שלום", 30.0)
    monkeypatch.setattr(claude_cli.sys, "platform", "win32")
    monkeypatch.setattr(claude_cli, "_LANGUAGE_MODE", None)
    claude_cli.powershell_language_mode()

    assert seen, "nothing was spawned"
    for kwargs in seen:
        assert kwargs.get("encoding") == "utf-8", f"a spawn without UTF-8: {kwargs.keys()}"


def test_claude_cli_unwraps_the_envelope() -> None:
    assert json.loads(ClaudeCliClient._payload(envelope(VALID)))["title"] == "Weekly sync"
    assert ClaudeCliClient._payload('{"a": 1}') == '{"a": 1}'  # not an envelope
    assert ClaudeCliClient._payload("plain text") == "plain text"
    with pytest.raises(RecoverableError):
        ClaudeCliClient._payload(json.dumps({"is_error": True, "result": "boom"}))
    with pytest.raises(RecoverableError):
        ClaudeCliClient._payload("   ")


def test_claude_cli_never_sees_a_credential(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The whole point: the app holds no token, it just runs the signed-in CLI."""
    import inspect

    from app.llm import claude_cli

    source = inspect.getsource(claude_cli)
    for marker in ("keyring", "api_key", "Authorization", "session_token", ".credentials"):
        assert marker not in source, f"{marker} must not appear in the subscription provider"
    assert "ANTHROPIC_API_KEY" in source, "the child env must have the key stripped"


# ------------------------------------------------------------------ openai


class StubOpenAI:
    def __init__(self, contents: list[str], finish: str = "stop") -> None:
        self.contents = contents
        self.finish = finish
        self.requests: list[dict[str, Any]] = []
        outer = self

        class Completions:
            def create(self, **kwargs: Any) -> Any:
                outer.requests.append(kwargs)
                message = type("M", (), {"content": outer.contents.pop(0)})()
                choice = type("C", (), {"message": message, "finish_reason": outer.finish})()
                usage = type(
                    "U", (), {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
                )()
                return type("R", (), {"choices": [choice], "usage": usage})()

        self.chat = type("Chat", (), {"completions": Completions()})()


def test_openai_requests_json_and_validates() -> None:
    stub = StubOpenAI([json.dumps(VALID)])
    client = OpenAiClient(default_config(), client=stub)
    result = client.complete_json(system_blocks=BLOCKS, user="TRANSCRIPT")
    assert result.data["title"] == "Weekly sync"
    request = stub.requests[0]
    assert request["response_format"] == {"type": "json_object"}
    assert "JSON Schema" in request["messages"][0]["content"]
    assert request["messages"][1]["content"] == "TRANSCRIPT"
    assert result.usage["total_tokens"] == 15


def test_openai_repairs_then_succeeds() -> None:
    stub = StubOpenAI(["{oops", json.dumps(VALID)])
    result = OpenAiClient(default_config(), client=stub).complete_json(
        system_blocks=BLOCKS, user="x"
    )
    assert result.attempts == 2


def test_openai_content_filter_is_permanent() -> None:
    stub = StubOpenAI([json.dumps(VALID)], finish="content_filter")
    with pytest.raises(PermanentError, match="content filter"):
        OpenAiClient(default_config(), client=stub).complete_json(system_blocks=BLOCKS, user="x")


def test_openai_without_a_key_is_actionable() -> None:
    # The message differs when the optional SDK is absent, and CI installs no extras —
    # so assert the key error only where it is the one that can occur.
    pytest.importorskip("openai", reason="the openai extra is not installed")
    config = default_config(secrets__backend="memory")
    with pytest.raises(PermanentError, match="no OpenAI API key"):
        OpenAiClient(config).complete_json(system_blocks=BLOCKS, user="x")


def test_openai_without_the_extra_says_so() -> None:
    """The other half: a clear setup error rather than an ImportError traceback."""
    import importlib.util

    if importlib.util.find_spec("openai") is not None:
        pytest.skip("the openai extra is installed, so this path cannot trigger")
    config = default_config(secrets__backend="memory")
    with pytest.raises(PermanentError, match="--extra openai"):
        OpenAiClient(config).client()


# ------------------------------------------------------------------ gemini


def gemini_transport(texts: list[str], finish: str = "STOP"):  # type: ignore[no-untyped-def]
    seen: list[tuple[str, dict[str, Any]]] = []

    def transport(path: str, payload: dict[str, Any]) -> dict[str, Any]:
        seen.append((path, payload))
        if path.endswith(":countTokens"):
            return {"totalTokens": 1234}
        return {
            "candidates": [
                {"content": {"parts": [{"text": texts.pop(0)}]}, "finishReason": finish}
            ],
            "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20},
        }

    transport.seen = seen  # type: ignore[attr-defined]
    return transport


def test_gemini_request_shape_and_validation() -> None:
    transport = gemini_transport([json.dumps(VALID)])
    client = GeminiClient(default_config(), transport=transport)
    result = client.complete_json(system_blocks=BLOCKS, user="TRANSCRIPT")
    assert result.data["title"] == "Weekly sync"
    path, payload = transport.seen[0]  # type: ignore[attr-defined]
    assert path.endswith(":generateContent")
    assert payload["generationConfig"]["responseMimeType"] == "application/json"
    assert payload["contents"][0]["parts"][0]["text"] == "TRANSCRIPT"
    assert "JSON Schema" in payload["systemInstruction"]["parts"][0]["text"]
    assert result.usage["promptTokenCount"] == 100


def test_gemini_uses_the_real_token_counter() -> None:
    transport = gemini_transport([])
    assert GeminiClient(default_config(), transport=transport).count_tokens("שלום") == 1234


def test_gemini_token_counter_falls_back() -> None:
    def broken(path: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("offline")

    assert GeminiClient(default_config(), transport=broken).count_tokens("x" * 32) == 11


def test_gemini_safety_block_is_permanent() -> None:
    def blocked(path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"promptFeedback": {"blockReason": "SAFETY"}}

    with pytest.raises(PermanentError, match="declined"):
        GeminiClient(default_config(), transport=blocked).complete_json(
            system_blocks=BLOCKS, user="x"
        )


def test_gemini_without_a_key_is_actionable() -> None:
    config = default_config(secrets__backend="memory")
    with pytest.raises(PermanentError, match=r"aistudio\.google\.com"):
        GeminiClient(config).key()


# ------------------------------------------------------------------ selection


@pytest.mark.parametrize(
    ("provider", "name"),
    [
        ("anthropic", "anthropic"),
        ("openai", "openai"),
        ("gemini", "gemini"),
        ("claude-subscription", "claude-subscription"),
        ("ollama", "ollama"),
        ("fake", "fake"),
    ],
)
def test_provider_is_selected_by_config(provider: str, name: str) -> None:
    assert make_client(default_config(llm__provider=provider)).name == name


def test_sensitive_meetings_still_force_local() -> None:
    for provider in ("anthropic", "openai", "gemini", "claude-subscription"):
        client = make_client(default_config(llm__provider=provider), sensitive=True)
        assert client.name == "ollama", "a sensitive meeting never leaves the machine"


def test_default_is_still_the_anthropic_api() -> None:
    assert default_config().get("llm.provider") == "anthropic"
    from app.errors import ConfigError

    with pytest.raises(ConfigError):
        Config({"llm": {"provider": "not-a-provider"}}).validate()
