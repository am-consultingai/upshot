"""The Antigravity provider (D79), driven through an injected runner — no subprocess.

The outputs replayed here are the real ones, recorded from ``agy`` 1.2.8 and 1.2.12 on
2026-09-28 (this machine, and machine B signed out and then signed in).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.config import default_config
from app.errors import PermanentError, QuotaExhausted, RecoverableError, SignInRequired
from app.llm import antigravity_cli
from app.llm.antigravity_cli import (
    AntigravityCliClient,
    install_plan,
    result_event,
    update_command,
    user_event,
)
from app.llm.client import make_client, system_blocks

VALID: dict[str, Any] = {"title": "Weekly sync", "summary_html": "<p>we shipped</p>"}
BLOCKS = system_blocks("SYSTEM", "GLOSSARY")
MODELS = "Fetching available models...\ngemini-3.8-flash-high\tGemini 3.8 Flash (High)\n"
SIGNED_OUT_MODELS = (
    "Fetching available models...\n"
    "Error: Please sign in to view available models. Launch the CLI without arguments to "
    "sign in.\n"
)
#: What a signed-out headless run printed on machine B, after its 60 s wait.
SIGNED_OUT_RUN = (
    '{"event":"result","result":{"conversation_id":"","status":"ERROR","response":"",'
    '"error":"authentication failed or timed out","duration_seconds":0,"num_turns":0}}\n'
)
SIGNED_OUT_ERR = (
    "Authentication required. Please visit the URL to log in:\n"
    "  https://accounts.google.com/o/oauth2/auth?access_type=offline&client_id=X\n\n"
    "Waiting for authentication (timeout 60s)...\n"
    "Or, paste the authorization code here and press Enter:\n"
    "Error: authentication timed out.\n"
)


def stream(result: dict[str, Any]) -> str:
    """A run's stdout: the init event, a step, and the final result, as agy prints them."""
    events = [
        {"event": "init", "conversation_id": "c1", "init": {"model": "gemini-3.8-flash-low"}},
        {"event": "step_update", "step_update": {"state": "DONE", "text_delta": "thinking"}},
        {"event": "result", "result": {"conversation_id": "c1", **result}},
    ]
    return "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events)


def success(shaped: Any = VALID) -> str:
    return stream({"status": "SUCCESS", "response": "done\n", "structured_output": shaped})


class Runner:
    """Answers ``--version`` and ``models`` as a signed-in agy does; runs from ``script``."""

    def __init__(
        self, script: list[tuple[int, str, str]], *, models: tuple[int, str] = (0, MODELS)
    ) -> None:
        self.script = script
        self.models = models
        self.calls: list[dict[str, Any]] = []
        self.runs = 0

    def __call__(self, args, stdin, timeout):  # type: ignore[no-untyped-def]
        args = list(args)
        call: dict[str, Any] = {"args": args, "stdin": stdin, "timeout": timeout}
        self.calls.append(call)
        if args[1:] == ["--version"]:
            return 0, "1.2.12\n", ""
        if args[1:] == ["models"]:
            code, out = self.models
            return code, out, ""
        if "--json-schema" in args:
            schema_file = Path(args[args.index("--json-schema") + 1])
            call["schema"] = json.loads(schema_file.read_text(encoding="utf-8"))
        answer = self.script[min(self.runs, len(self.script) - 1)]
        self.runs += 1
        return answer

    def run_calls(self) -> list[dict[str, Any]]:
        return [c for c in self.calls if "--json-schema" in c["args"]]


def client_with(tmp_path: Path, runner: Runner) -> AntigravityCliClient:
    (tmp_path / "agy").write_text("#!/bin/sh\n")
    config = default_config(llm__provider="antigravity-subscription")
    config.set("llm.antigravity_cli_path", str(tmp_path / "agy"))
    return AntigravityCliClient(config, runner=runner)


# ------------------------------------------------------------------ the call


def test_the_argument_list_is_exactly_what_is_intended(tmp_path: Path) -> None:
    runner = Runner([(0, success(), "")])
    client = client_with(tmp_path, runner)
    result = client.complete_json(system_blocks=BLOCKS, user="**[00:12] ME:** hello")
    assert result.data == VALID
    assert result.model == "antigravity:antigravity-subscription"
    call = runner.run_calls()[0]
    args = call["args"]
    schema_file = args[args.index("--json-schema") + 1]
    assert args == [
        str(tmp_path / "agy"),
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--json-schema",
        schema_file,
        "--disable-slash-commands",
        "--sandbox",
        "--agent",
        "upshot",
    ]
    assert "--dangerously-skip-permissions" not in args
    assert "-p" not in args, "the prompt is never an argument: Windows would cut it short"
    assert "hello" not in " ".join(args)


def test_the_prompt_is_one_stream_json_event_on_stdin(tmp_path: Path) -> None:
    runner = Runner([(0, success(), "")])
    client_with(tmp_path, runner).complete_json(system_blocks=BLOCKS, user="**[00:12] ME:** שלום")
    stdin = runner.run_calls()[0]["stdin"]
    assert stdin.endswith("\n") and stdin.count("\n") == 1, "exactly one NDJSON line"
    event = json.loads(stdin)
    assert event["event"] == "user"
    content = event["message"]["content"]
    assert content.startswith("SYSTEM")
    assert content.endswith("**[00:12] ME:** שלום"), "Hebrew goes through as itself"
    assert "שלום" in stdin, "not escaped to \\u sequences"


def test_the_schema_goes_as_it_is_and_is_cleaned_up(tmp_path: Path) -> None:
    """No strict-mode rewriting: agy took optional properties as they are (2026-09-28)."""
    from app.llm.schema import FREE_SCHEMA

    runner = Runner([(0, success(), "")])
    client_with(tmp_path, runner).complete_json(system_blocks=BLOCKS, user="x")
    call = runner.run_calls()[0]
    assert call["schema"] == FREE_SCHEMA
    schema_file = Path(call["args"][call["args"].index("--json-schema") + 1])
    assert not schema_file.exists(), "the run's folder is removed afterwards"


def test_the_model_is_agys_own_unless_one_is_chosen(tmp_path: Path) -> None:
    runner = Runner([(0, success(), "")])
    client = client_with(tmp_path, runner)
    client.model = "gemini-3.8-flash-high"
    client.complete_json(system_blocks=BLOCKS, user="x")
    args = runner.run_calls()[0]["args"]
    assert args[args.index("--model") + 1] == "gemini-3.8-flash-high"


def test_a_plain_text_answer_goes_to_the_repair_loop(tmp_path: Path) -> None:
    """No structured_output: the response text is judged, and repaired if it fails."""
    text = stream({"status": "SUCCESS", "response": json.dumps(VALID)})
    runner = Runner([(0, text, "")])
    assert client_with(tmp_path, runner).complete_json(system_blocks=BLOCKS, user="x").data == VALID
    prose = stream({"status": "SUCCESS", "response": "Here is your summary, in words."})
    runner = Runner([(0, prose, "")])
    with pytest.raises(PermanentError, match="schema-valid JSON"):
        client_with(tmp_path, runner).complete_json(system_blocks=BLOCKS, user="x")
    assert runner.runs == 3, "the repair loop gets its retries first"


def test_the_result_event_is_found_among_the_others() -> None:
    assert result_event(success())["status"] == "SUCCESS"  # type: ignore[index]
    assert result_event("not json\n{broken\n") is None
    assert result_event("") is None


def test_user_event_round_trips() -> None:
    assert json.loads(user_event("a\nb")) == {"event": "user", "message": {"content": "a\nb"}}


def test_every_run_is_the_toolless_upshot_agent_in_our_own_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """agy applies the user's own permission rules, which can allow every command: our
    runs select an agent with no tool but finish, kept in our run folder (D79)."""
    monkeypatch.setenv("UP_HOME", str(tmp_path / "home"))
    folder = Path(antigravity_cli.workdir())
    agent = folder / ".agents" / "agents" / "upshot.md"
    assert agent.read_text(encoding="utf-8") == antigravity_cli.AGENT
    assert "tools: [finish]" in antigravity_cli.AGENT
    assert 'commandExecutionPolicy: "off"' in antigravity_cli.AGENT
    agent.write_text("---\nname: upshot\ntools: [run_command]\n---\n", encoding="utf-8")
    antigravity_cli.workdir()
    assert agent.read_text(encoding="utf-8") == antigravity_cli.AGENT, "a changed one is put back"


def test_other_credentials_never_reach_the_child(monkeypatch: pytest.MonkeyPatch) -> None:
    """A key or Cloud credentials would bill something other than the plan — silently."""
    for name in antigravity_cli.STRIPPED_ENV:
        monkeypatch.setenv(name, "must-not-pass")
    monkeypatch.setenv("KEEP_ME", "1")
    env = antigravity_cli.child_env()
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "AGY_ADC_AUTH", "AGY_GATEWAY_URL"):
        assert name not in env
    assert env["KEEP_ME"] == "1"


# ------------------------------------------------------------------ the error taxonomy


def test_signed_out_is_found_before_anything_runs(tmp_path: Path) -> None:
    """A signed-out run would open Google's page in the browser, unasked, and wait a
    minute: ``agy models`` is asked first, and nothing is run."""
    runner = Runner([(0, success(), "")], models=(1, SIGNED_OUT_MODELS))
    with pytest.raises(SignInRequired, match="not signed in"):
        client_with(tmp_path, runner).complete_json(system_blocks=BLOCKS, user="x")
    assert runner.run_calls() == []


def test_a_run_that_finds_no_sign_in_waits_rather_than_fails(tmp_path: Path) -> None:
    """Signed out between the check and the run: the 60 s wait ends in this."""
    runner = Runner([(1, SIGNED_OUT_RUN, SIGNED_OUT_ERR)])
    with pytest.raises(SignInRequired):
        client_with(tmp_path, runner).complete_json(system_blocks=BLOCKS, user="x")


@pytest.mark.parametrize(
    ("error", "stderr", "code", "expected"),
    [
        ("RESOURCE_EXHAUSTED: Quota exceeded for the plan", "", 1, QuotaExhausted),
        ("", "Error: You have reached your usage limit.", 1, QuotaExhausted),
        (
            "The model API is currently overloaded and may experience intermittent errors.",
            "",
            1,
            RecoverableError,
        ),
        ("", "rpc error: code = Unavailable desc = 503", 1, RecoverableError),
        ("something nobody anticipated", "", 3, RecoverableError),
        ("", "", 0, RecoverableError),  # exit 0 and no answer at all
    ],
)
def test_each_failure_maps_to_the_right_error(
    tmp_path: Path, error: str, stderr: str, code: int, expected: type[Exception]
) -> None:
    out = stream({"status": "ERROR", "response": "", "error": error}) if error else ""
    runner = Runner([(code, out, stderr)])
    with pytest.raises(expected) as raised:
        client_with(tmp_path, runner).complete_json(system_blocks=BLOCKS, user="x")
    if expected is RecoverableError:
        assert not isinstance(raised.value, QuotaExhausted | SignInRequired)


def test_the_models_words_are_never_mistaken_for_an_error(tmp_path: Path) -> None:
    """A failed run's stream carries the model's text. A meeting about safety, or about a
    usage limit, must not read as a refusal or a spent plan."""
    events = stream({"status": "ERROR", "response": "", "error": "connection reset"})
    chatter = json.dumps(
        {
            "event": "step_update",
            "step_update": {"text_delta": "we hit the usage limit; safety review"},
        }
    )
    runner = Runner([(1, chatter + "\n" + events, "")])
    with pytest.raises(RecoverableError) as raised:
        client_with(tmp_path, runner).complete_json(system_blocks=BLOCKS, user="x")
    assert not isinstance(raised.value, QuotaExhausted)
    assert "connection reset" in str(raised.value)


@pytest.mark.parametrize(
    ("stderr", "category"),
    [
        ("flag provided but not defined: -disable-slash-commands", "setup"),
        (
            "Error: --json-schema can only be used when --output-format is 'json' or 'stream-json'",
            "setup",
        ),
        ("The response was blocked by policy: prohibited content", "refusal"),
    ],
)
def test_what_retrying_cannot_fix_is_permanent(tmp_path: Path, stderr: str, category: str) -> None:
    runner = Runner([(2, "", stderr)])
    with pytest.raises(PermanentError) as raised:
        client_with(tmp_path, runner).complete_json(system_blocks=BLOCKS, user="x")
    assert raised.value.category == category
    assert runner.runs == 1, "and it is not retried inside the call either"


def test_a_timeout_is_recoverable(tmp_path: Path) -> None:
    import subprocess

    runner = Runner([(0, success(), "")])
    client = client_with(tmp_path, runner)

    def slow(args, stdin, timeout):  # type: ignore[no-untyped-def]
        if "--json-schema" in args:
            raise subprocess.TimeoutExpired(args, timeout)
        return runner(args, stdin, timeout)

    client.runner = slow
    with pytest.raises(RecoverableError, match="timed out"):
        client.complete_json(system_blocks=BLOCKS, user="x")


def test_the_quota_message_is_plain_words(tmp_path: Path) -> None:
    out = stream({"status": "ERROR", "response": "", "error": "RESOURCE_EXHAUSTED: quota exceeded"})
    runner = Runner([(1, out, "")])
    with pytest.raises(QuotaExhausted) as raised:
        client_with(tmp_path, runner).complete_json(system_blocks=BLOCKS, user="x")
    assert str(raised.value).startswith("Your Google AI plan's Antigravity allowance is used up")
    assert "RESOURCE_EXHAUSTED" not in str(raised.value)
    assert raised.value.provider == "antigravity-subscription"


# ------------------------------------------------------------------ status


def test_status_when_signed_in(tmp_path: Path) -> None:
    runner = Runner([(0, "", "")])
    status = client_with(tmp_path, runner).status()
    assert status.installed and status.version == "1.2.12"
    assert status.signed_in is True and status.account == "Google account"
    assert [c["args"][1:] for c in runner.calls] == [["--version"], ["models"]]


def test_status_when_signed_out(tmp_path: Path) -> None:
    runner = Runner([(0, "", "")], models=(1, SIGNED_OUT_MODELS))
    assert client_with(tmp_path, runner).status().signed_in is False


def test_an_unclear_answer_is_unknown_not_signed_out(tmp_path: Path) -> None:
    """Offline, say: models cannot be fetched, and that says nothing about the sign-in."""
    runner = Runner(
        [(0, "", "")], models=(1, "Fetching available models...\nError: dial tcp: i/o timeout\n")
    )
    status = client_with(tmp_path, runner).status()
    assert status.installed is True
    assert status.signed_in is None


def test_status_when_missing() -> None:
    config = default_config()
    config.set("llm.antigravity_cli_path", "definitely-not-installed-xyz")
    client = AntigravityCliClient(config)
    assert client.status().installed is False
    with pytest.raises(PermanentError, match="not installed"):
        client.complete_json(system_blocks=BLOCKS, user="x")


def test_sign_in_is_a_tiny_prompt_and_sign_out_does_not_exist(tmp_path: Path) -> None:
    client = client_with(tmp_path, Runner([(0, "", "")]))
    assert client.login_command() == [
        str(tmp_path / "agy"),
        "-p",
        "Reply with exactly the word OK.",
        "--output-format",
        "json",
        "--agent",
        "upshot",
    ]
    assert client.logout_command() is None, "/logout is refused in print mode"
    script = antigravity_cli.login_console([r"C:\Users\someone\App Data\agy.exe", "-p", "x"])[-1]
    assert "'C:\\Users\\someone\\App Data\\agy.exe'" in script, "a path with a space is quoted"


def test_googles_login_link_is_recognised() -> None:
    match = antigravity_cli.LOGIN_URL.search(SIGNED_OUT_ERR)
    assert match and match.group(0).startswith("https://accounts.google.com/o/oauth2/auth?")


# ------------------------------------------------------------------ install


def test_install_uses_googles_installer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(antigravity_cli.sys, "platform", "win32")
    plan = install_plan()
    assert plan is not None and plan.method == "native"
    assert plan.display == "irm https://antigravity.google/cli/install.ps1 | iex"
    script = plan.argv[-1]
    assert "-NonInteractive" in script, "run where PSReadLine is never loaded"
    assert "-NoExit" in plan.argv
    assert "-p 'Reply with exactly the word OK.'" in script, "and it signs in straight after"


def test_a_background_install_does_not_sign_in_or_keep_a_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(antigravity_cli.sys, "platform", "win32")
    plan = install_plan(sign_in=False)
    assert plan is not None
    assert "-NoExit" not in plan.argv
    assert "Reply with exactly" not in plan.argv[-1]
    assert "exit 1" in plan.argv[-1]


def test_install_is_windows_only() -> None:
    import sys

    if sys.platform != "win32":
        assert install_plan() is None


def test_update_is_pinned_to_the_install() -> None:
    native = r"C:\Users\someone\AppData\Local\agy\bin\agy.exe"
    assert update_command(native) == f'"{native}" update'


# ------------------------------------------------------------------ the credential


def test_antigravity_cli_never_sees_a_credential() -> None:
    """The whole point: the app holds no token, it just runs the signed-in CLI."""
    import inspect

    source = inspect.getsource(antigravity_cli)
    for marker in (
        "keyring",
        "api_key",
        "Authorization",
        "oauth-token",
        "oauth_creds",
        "session_token",
        ".credentials",
        ".gemini",
    ):
        assert marker not in source, f"{marker} must not appear in the subscription provider"
    assert "GEMINI_API_KEY" in source, "the child env must have the key stripped"


def test_selected_by_config_and_never_for_a_sensitive_meeting() -> None:
    config = default_config(llm__provider="antigravity-subscription")
    assert make_client(config).name == "antigravity-subscription"
    assert make_client(config, sensitive=True).name == "ollama"
    assert default_config().get("llm.provider") != "antigravity-subscription", "never the default"
