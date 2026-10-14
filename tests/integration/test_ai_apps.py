"""The activity log and Settings → AI apps (D98)."""

from __future__ import annotations

import json
import shutil
import wave
from pathlib import Path
from typing import Any

import pytest

from app.access_log import MAX_ROWS, AccessLog, agent_of
from app.db.dao import Turn
from app.pipeline.states import MeetingState
from tests.fixtures.api import ApiHarness, build_harness, serve
from tests.fixtures.meetings import speechish

ACCEPT = {"Accept": "application/json, text/event-stream"}


@pytest.fixture
def h(tmp_path: Path, app_home: Path) -> ApiHarness:
    harness = build_harness(tmp_path)
    folder = harness.services.config.data_root / "m-budget"
    folder.mkdir(parents=True)
    harness.services.dao.insert_meeting(
        meeting_id="m-budget",
        folder=folder,
        source="manual",
        state=MeetingState.RECORDING,
        profile="cpu-deferred",
        title="Q4 budget review",
        started_at="2026-09-20T09:30:00Z",
    )
    harness.services.dao.index_turns("m-budget", [Turn(0, "THEM", 4000, "The budget is cut.")])
    return harness


def tool(client: Any, h: ApiHarness, name: str, agent: str = "", **arguments: Any) -> Any:
    headers = {**ACCEPT, "Authorization": f"Bearer {h.services.auth.access_key}"}
    if agent:
        headers["X-Upshot-Agent"] = agent
    return client.post(
        "/mcp/ext/",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers=headers,
    )


def test_every_meeting_tool_call_is_logged_with_the_meeting_title(h: ApiHarness) -> None:
    with serve(h) as client:
        tool(client, h, "get_transcript", agent="claude-code", meeting_id="m-budget")
        tool(client, h, "search", agent="codex", query="budget")
        entries = client.get("/api/ai-apps/activity").json()
    first, second = entries["entries"][1], entries["entries"][0]
    assert (first["channel"], first["agent"], first["action"]) == (
        "mcp",
        "claude-code",
        "get_transcript",
    )
    assert first["target"] == "Q4 budget review" and first["outcome"] == "ok" and first["size"] > 0
    assert (second["agent"], second["action"], second["target"]) == ("codex", "search", "budget")
    assert entries["agents"] == ["codex", "claude-code"]


def test_a_call_with_meeting_access_off_is_logged_as_refused(h: ApiHarness) -> None:
    h.services.config.set("mcp.meetings_enabled", False)
    with serve(h) as client:
        tool(client, h, "search", query="budget")
    assert h.services.access_log.recent()[0].outcome == "refused"


def wav(path: Path) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(speechish(2).tobytes())
    return path


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_program_requests_to_the_api_are_logged_and_the_page_is_not(
    h: ApiHarness, tmp_path: Path
) -> None:
    program = h.client(authorized=False)
    key = {"Authorization": f"Bearer {h.services.auth.access_key}", "X-Upshot-Client": "api"}
    source = wav(tmp_path / "talk.wav")
    created = program.post("/api/v1/transcriptions", json={"path": str(source)}, headers=key)
    assert created.status_code == 202, created.text
    program.cookies.clear()
    job = created.json()["id"]
    program.get(f"/api/v1/transcriptions/{job}", headers=key)
    program.cookies.clear()
    program.get(f"/api/v1/transcriptions/{job}/wait?timeout=0", headers=key)
    program.cookies.clear()
    program.get("/api/v1/transcriptions", headers={"User-Agent": "curl/8.5"})
    program.cookies.clear()
    h.client().get("/api/v1/transcriptions")  # the page
    rows = [
        (e.channel, e.agent, e.action, e.target, e.outcome) for e in h.services.access_log.recent()
    ]
    assert rows == [
        ("api", "curl/8.5", "list", "", "refused"),
        ("api", "api", "get", "talk.wav", "ok"),
        ("api", "api", "transcribe", "talk.wav", "ok"),
    ], "no line for the long-poll, nor for the page"


def test_the_log_is_capped_and_swept_only_when_asked(h: ApiHarness) -> None:
    log = AccessLog(h.services.conn, h.services.clock)
    for i in range(MAX_ROWS + 5):
        log.record("mcp", "a", "search", str(i))
    kept = log.recent(1000)
    assert kept[0].target == str(MAX_ROWS + 4)
    assert h.services.conn.execute("SELECT COUNT(*) FROM access_log").fetchone()[0] == MAX_ROWS
    assert log.sweep(None) == 0, "kept unless a number of days is set (D92)"
    h.services.clock.advance(3 * 86400)  # type: ignore[attr-defined]
    log.record("mcp", "a", "search", "new")
    # The new line took the oldest one's place under the cap.
    assert log.sweep(2) == MAX_ROWS - 1 and [e.target for e in log.recent()] == ["new"]


def test_text_is_cleaned_and_cut() -> None:
    assert agent_of({"x-upshot-agent": "evil\r\nname" + "x" * 100}).startswith("evil name")
    assert len(agent_of({"user-agent": "y" * 300})) == 64
    assert agent_of(None) == "" and agent_of({}) == ""


def test_clear_needs_the_page(h: ApiHarness) -> None:
    h.services.access_log.record("mcp", "a", "search", "x")
    program = h.client(authorized=False)
    assert program.post("/api/ai-apps/activity/clear").status_code == 403
    assert h.client().post("/api/ai-apps/activity/clear").json() == {"cleared": 1}
    assert h.services.access_log.recent() == []


def test_settings_show_the_urls_commands_and_a_moved_port(h: ApiHarness) -> None:
    overview = h.client().get("/api/ai-apps").json()
    port = h.services.config.server_port
    assert overview["meetings_enabled"] is True
    assert overview["urls"] == {
        "local": f"http://127.0.0.1:{port}/mcp/ext/",
        "docker": f"http://host.docker.internal:{port}/mcp/ext/",
    }
    assert overview["port_moved"] is (port != 43217)
    assert "key" not in json.dumps(overview).lower().replace("key_", ""), (
        "the key is never in a GET"
    )


def test_the_key_is_shown_and_reset_only_on_the_page(h: ApiHarness) -> None:
    program = h.client(authorized=False)
    assert program.post("/api/ai-apps/key").status_code == 403
    page = h.client()
    assert page.post("/api/ai-apps/key").json() == {"key": h.services.auth.access_key}
    old = h.services.auth.access_key
    new = page.post("/api/ai-apps/key/reset").json()["key"]
    assert new != old and h.services.auth.access_key == new


def test_commands_for_each_app(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import ai_apps

    bridge = "C:\\Users\\someone\\AppData\\Local\\Programs\\Upshot\\mcp\\upshot-mcp.exe"
    found = ai_apps.commands(bridge)
    assert found["codex"] == f'codex mcp add upshot -- "{bridge}"'
    assert found["antigravity"] == f'agy mcp add upshot "{bridge}"'
    assert found["claude_code_wsl"].startswith(
        'claude mcp add --scope user upshot -- "/mnt/c/Users/someone/'
    )
    assert json.loads(found["mcp_json"]) == {
        "mcpServers": {"upshot": {"command": bridge, "args": []}}
    }
    assert "vscode_json" not in found and "gemini" not in found
    assert ai_apps.commands(None) == {}


def test_refusals_without_the_key_collapse_to_one_line_a_minute(h: ApiHarness) -> None:
    """Anything that reaches the port can send them in a loop, under any name."""
    program = h.client(authorized=False)
    for _ in range(50):
        program.cookies.clear()
        program.get("/api/v1/transcriptions", headers={"X-Upshot-Agent": "Claude Desktop"})
    assert len(h.services.access_log.recent()) == 1
    h.services.clock.advance(61)  # type: ignore[attr-defined]
    program.cookies.clear()
    program.get("/api/v1/transcriptions")
    assert len(h.services.access_log.recent()) == 2


def test_the_found_apps_and_the_terminal_on_a_source_checkout(h: ApiHarness) -> None:
    """Off Windows, nothing is looked up and nothing opens: the page copies the command."""
    page = h.client()
    apps = page.get("/api/ai-apps/found").json()["apps"]
    assert set(apps) == {"claude_code", "codex", "antigravity"}
    assert all((where["windows"], where["wsl"]) == (False, None) for where in apps.values())
    assert (
        page.post("/api/ai-apps/terminal", json={"app": "word", "side": "windows"}).status_code
        == 400
    )
    assert (
        page.post("/api/ai-apps/terminal", json={"app": "codex", "side": "mac"}).status_code == 400
    )
    # A source checkout has no installed bridge to name.
    assert page.post("/api/ai-apps/terminal", json={"app": "codex"}).status_code == 409
    assert (
        h.client(authorized=False).post("/api/ai-apps/terminal", json={"app": "codex"}).status_code
        == 403
    )


def test_the_terminal_never_runs_text_from_the_request(
    h: ApiHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.main import test_mode  # noqa: F401  (install_dir reads it)

    monkeypatch.setenv("UP_TEST_MODE", "1")
    reply = (
        h.client()
        .post(
            "/api/ai-apps/terminal",
            json={"app": "antigravity", "side": "wsl", "command": "rm -rf ~"},
        )
        .json()
    )
    assert reply["launched"] is False
    assert reply["command"].startswith('agy mcp add upshot -- "/mnt/c/Users/someone/')


@pytest.mark.parametrize("side", ["windows", "wsl"])
def test_the_launch_runs_only_the_line_built_here(
    h: ApiHarness, monkeypatch: pytest.MonkeyPatch, side: str
) -> None:
    """The launch branch itself, as on Windows: the argv holds the line from commands()
    and nothing from the request, and the terminals open before are passed on."""
    from app import ai_apps, ai_terminal, window

    bridge = "C:\\Users\\someone\\AppData\\Local\\Programs\\Upshot\\mcp\\upshot-mcp.exe"
    launched: list[list[str]] = []
    forwarded: list[set[int]] = []
    monkeypatch.setattr(ai_apps.sys, "platform", "win32")
    monkeypatch.setattr(ai_apps, "_test_install", lambda: False)
    monkeypatch.setattr(ai_apps, "bridge_path", lambda: bridge)
    monkeypatch.setattr(ai_apps, "ai_terminal_windows", lambda title: {7})
    monkeypatch.setattr(ai_terminal, "registry_path", lambda: "C:\\bin")
    monkeypatch.setattr(
        ai_terminal.shutil, "which", lambda program, path=None: "C:\\bin\\codex.exe"
    )
    monkeypatch.setattr(ai_terminal, "find_on_windows", lambda config: {"codex": "codex.exe"})
    monkeypatch.setattr(ai_terminal, "find_in_wsl", lambda: {"codex": "Ubuntu-24.04"})
    monkeypatch.setattr(ai_terminal, "launch", lambda argv, fresh: launched.append(argv))
    monkeypatch.setattr(
        window, "bring_terminal_forward", lambda title, before: forwarded.append(before)
    )
    reply = (
        h.client()
        .post("/api/ai-apps/terminal", json={"app": "codex", "side": side, "command": "rm -rf ~"})
        .json()
    )
    expected = ai_apps.commands(bridge)["codex" if side == "windows" else "codex_wsl"]
    assert reply == {"launched": True, "command": expected}
    (argv,) = launched
    assert "rm -rf" not in " ".join(argv)
    if side == "windows":
        assert argv[:2] == ["powershell.exe", "-NoExit"] and f"Insert('{expected}')" in argv[4]
    else:
        assert argv[:3] == ["wsl.exe", "-d", "Ubuntu-24.04"] and argv[-1] == expected
    assert forwarded == [{7}]


def test_a_distro_that_stopped_is_not_replaced_by_the_default(
    h: ApiHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opening the default distro would start WSL, maybe one without the app."""
    from app import ai_apps, ai_terminal

    monkeypatch.setattr(ai_apps.sys, "platform", "win32")
    monkeypatch.setattr(ai_apps, "_test_install", lambda: False)
    monkeypatch.setattr(ai_apps, "bridge_path", lambda: "C:\\Upshot\\mcp\\upshot-mcp.exe")
    monkeypatch.setattr(ai_terminal, "registry_path", lambda: None)
    monkeypatch.setattr(ai_terminal, "find_in_wsl", lambda: {})
    monkeypatch.setattr(ai_terminal, "launch", lambda argv, fresh: pytest.fail("launched"))
    reply = h.client().post("/api/ai-apps/terminal", json={"app": "codex", "side": "wsl"}).json()
    assert reply["launched"] is False and reply["command"].startswith("codex mcp add upshot --")


def test_a_window_list_that_fails_does_not_fail_the_click(
    h: ApiHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Listing the open terminals is a nicety: a window closing mid-list must not cost the
    terminal (review round 2)."""
    from app import ai_apps, ai_terminal, window

    launched: list[list[str]] = []
    monkeypatch.setattr(ai_apps.sys, "platform", "win32")
    monkeypatch.setattr(ai_apps, "_test_install", lambda: False)
    monkeypatch.setattr(ai_apps, "bridge_path", lambda: "C:\\Upshot\\mcp\\upshot-mcp.exe")
    monkeypatch.setattr(ai_terminal, "registry_path", lambda: None)
    monkeypatch.setattr(ai_terminal, "find_on_windows", lambda config: {})
    monkeypatch.setattr(ai_terminal, "launch", lambda argv, fresh: launched.append(argv))
    monkeypatch.setattr(window, "bring_terminal_forward", lambda title, before: None)

    def win32gui_gone(title: str) -> set[int]:
        raise OSError("the window went while it was looked at")

    monkeypatch.setattr(window, "terminal_windows", win32gui_gone)
    reply = h.client().post("/api/ai-apps/terminal", json={"app": "codex"})
    assert reply.status_code == 200 and reply.json()["launched"] is True and launched


def test_found_says_which_line_the_windows_terminal_types(
    h: ApiHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import ai_apps, ai_terminal

    bridge = "C:\\Upshot\\mcp\\upshot-mcp.exe"
    monkeypatch.setattr(ai_apps, "bridge_path", lambda: bridge)
    monkeypatch.setattr(ai_terminal, "registry_path", lambda: "C:\\npm")
    monkeypatch.setattr(
        ai_terminal.shutil, "which", lambda program, path=None: "C:\\npm\\codex.cmd"
    )
    monkeypatch.setattr(
        ai_terminal,
        "find",
        lambda config, test=False: ai_terminal.Found(windows={"codex": "C:\\npm\\codex.cmd"}),
    )
    apps = h.client().get("/api/ai-apps/found").json()["apps"]
    assert apps["codex"]["windows_line"] == f'codex.cmd mcp add upshot -- "{bridge}"'
    assert apps["antigravity"]["windows"] is False
