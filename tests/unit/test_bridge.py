"""The upshot-mcp bridge's own logic: paths, the stdio protocol, paging, discovery (D86)."""

from __future__ import annotations

import io
import json
import sys
import threading
from pathlib import Path
from typing import ClassVar

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "clients" / "python"))

from upshot_mcp import paths, tools, upshot
from upshot_mcp.protocol import Call, Server, Tool, ToolError

# ----------------------------------------------------------------------- paths


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("/mnt/c/Users/someone/clip.mp4", "C:\\Users\\someone\\clip.mp4"),
        ("/mnt/d", "D:\\"),
        ("/home/someone/clip.wav", "\\\\wsl.localhost\\Ubuntu\\home\\someone\\clip.wav"),
        ("C:\\Users\\someone\\clip.mp4", "C:\\Users\\someone\\clip.mp4"),
    ],
)
def test_wsl_paths_become_windows_paths(given: str, expected: str) -> None:
    assert paths.to_windows(given, "Ubuntu") == expected


def test_without_wsl_a_path_is_passed_as_given() -> None:
    assert paths.to_windows("/home/someone/clip.wav", None) == "/home/someone/clip.wav"
    with pytest.raises(paths.PathError):
        paths.to_windows("  ", "Ubuntu")


def test_absolute_and_stem() -> None:
    assert paths.is_absolute("C:\\x.mp4") and paths.is_absolute("\\\\wsl.localhost\\U\\x")
    assert paths.is_absolute("/home/x") and not paths.is_absolute("clip.mp4")
    assert paths.stem("C:\\a\\ראיון.final.mp4") == "ראיון.final"
    assert paths.stem("noext") == "noext"


# ----------------------------------------------------------------------- protocol


def run(lines: list[dict], tool_list: list[Tool] | None = None) -> list[dict]:  # type: ignore[type-arg]
    out = io.StringIO()
    server = Server("t", "0", "hi", tool_list or [], stdin=io.StringIO(), stdout=out)
    for line in lines:
        server.handle_line(json.dumps(line))
    for thread in threading.enumerate():
        if thread is not threading.current_thread() and thread.daemon:
            thread.join(timeout=5)
    return [json.loads(line) for line in out.getvalue().splitlines()]


def test_initialize_negotiates_the_version() -> None:
    answers = run(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26"},
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "initialize",
                "params": {"protocolVersion": "1999-01-01"},
            },
        ]
    )
    assert answers[0]["result"]["protocolVersion"] == "2025-03-26"
    assert answers[1]["result"]["protocolVersion"] == "2025-06-18"
    assert answers[0]["result"]["capabilities"] == {"tools": {"listChanged": False}}


def test_ping_lists_and_unknown_methods() -> None:
    answers = run(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "id": 2, "method": "resources/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "nonsense"},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        ]
    )
    assert answers[0]["result"] == {} and answers[1]["result"] == {"resources": []}
    assert answers[2]["error"]["code"] == -32601
    assert len(answers) == 3, "a notification gets no answer"


def test_a_tool_error_is_a_result_the_model_reads() -> None:
    def fails(call: Call) -> str:
        raise ToolError("Upshot isn't running")

    answers = run(
        [
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {"name": "x", "arguments": {}},
            }
        ],
        [Tool("x", "d", {"type": "object"}, fails)],
    )
    assert answers == [
        {
            "jsonrpc": "2.0",
            "id": 7,
            "result": {
                "content": [{"type": "text", "text": "Upshot isn't running"}],
                "isError": True,
            },
        }
    ]


def test_progress_is_sent_when_asked_for_and_a_cancel_stops_the_answer() -> None:
    started, release = threading.Event(), threading.Event()

    def slow(call: Call) -> str:
        call.progress(10, 100, "working")
        started.set()
        release.wait(5)
        call.check()
        return "done"

    out = io.StringIO()
    server = Server(
        "t", "0", "", [Tool("slow", "d", {"type": "object"}, slow)], stdin=io.StringIO(), stdout=out
    )
    server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "slow", "_meta": {"progressToken": "p"}},
            }
        )
    )
    assert started.wait(5)
    server.handle_line(
        json.dumps(
            {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}}
        )
    )
    release.set()
    for thread in threading.enumerate():
        if thread is not threading.current_thread() and thread.daemon:
            thread.join(timeout=5)
    sent = [json.loads(line) for line in out.getvalue().splitlines()]
    assert sent == [
        {
            "jsonrpc": "2.0",
            "method": "notifications/progress",
            "params": {"progressToken": "p", "progress": 10, "total": 100, "message": "working"},
        }
    ]


def test_tools_are_listed_with_schemas_and_annotations() -> None:
    listed = {t.name: t.describe() for t in tools.tools(tools.Bridge())}
    assert set(listed) == {
        "transcribe_file",
        "get_transcription",
        "list_transcriptions",
        "cancel_transcription",
    }
    assert listed["transcribe_file"]["inputSchema"]["required"] == ["path"]
    assert listed["transcribe_file"]["annotations"]["readOnlyHint"] is False
    assert listed["list_transcriptions"]["annotations"]["readOnlyHint"] is True
    # save_to writes a file, so this is not read-only (the PR #1 review)
    assert listed["get_transcription"]["annotations"]["readOnlyHint"] is False


# ----------------------------------------------------------------------- paging


def test_text_comes_in_parts_that_say_where_to_go_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "PART_CHARS", 200)
    result = {
        "language": "he",
        "speakers": ["S1"],
        "segments": [{"start": i * 10.0, "speaker": "S1", "text": "שלום " * 6} for i in range(20)],
    }
    job = {"id": "tr_a", "source_name": "a.mp4", "duration_s": 200}
    first = tools._text_part(job, result, 0, 0)
    assert "[00:00] S1:" in first and "from_s=" in first
    next_from = float(first.rsplit("from_s=", 1)[1].rstrip(".]"))
    second = tools._text_part(job, result, next_from, 0)
    assert f"[{tools._clock(next_from)}]" in second
    assert "[01:10] S1:" in tools._text_part(job, result, 70, 80)
    assert tools._clock(3725) == "1:02:05"


def test_no_speech_says_so() -> None:
    job = {"id": "tr_a", "source_name": "a.mp4", "duration_s": 3}
    assert "no speech" in tools._text_part(job, {"segments": []}, 0, 0)


def test_a_path_the_app_refuses_is_uploaded_when_this_process_can_read_it(tmp_path: Path) -> None:
    source = tmp_path / "clip.wav"
    source.write_bytes(b"RIFF")
    calls: list[str] = []

    class App:
        def submit_path(self, path: str, options: dict) -> dict:  # type: ignore[type-arg]
            calls.append("path")
            raise upshot.UpshotError(400, "path: network paths are not accepted")

        def upload(self, path: Path, options: dict) -> dict:  # type: ignore[type-arg]
            calls.append(f"upload {path.name}")
            return {"id": "tr_u"}

    assert tools.Bridge()._submit(App(), str(source), {}) == {"id": "tr_u"}  # type: ignore[arg-type]
    assert calls == ["path", "upload clip.wav"]
    with pytest.raises(ToolError, match="network paths"):
        tools.Bridge()._submit(App(), str(tmp_path / "missing.wav"), {})  # type: ignore[arg-type]
    with pytest.raises(ToolError, match="full path"):
        tools.Bridge()._submit(App(), "clip.wav", {})  # type: ignore[arg-type]


# ----------------------------------------------------------------------- discovery


def test_discovery_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UPSHOT_URL", raising=False)
    monkeypatch.setenv("UP_HOME", str(tmp_path))
    assert [b for b, _ in upshot.candidates()][:2] == [
        "http://127.0.0.1:8000",
        "http://127.0.0.1:8010",
    ]
    (tmp_path / "server.port").write_text("8023", encoding="utf-8")
    assert next(upshot.candidates()) == ("http://127.0.0.1:8023", upshot.RECORDED_TIMEOUT_S)
    monkeypatch.setenv("UPSHOT_URL", "http://127.0.0.1:9999/")
    assert [b for b, _ in upshot.candidates()] == ["http://127.0.0.1:9999"]
    assert [b for b, _ in upshot.candidates("http://127.0.0.1:7777")] == ["http://127.0.0.1:7777"]


def test_only_an_answer_naming_upshot_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = {
        "http://127.0.0.1:8000": {"app": "something-else"},
        "http://127.0.0.1:8010": {"app": "upshot", "enabled": True},
    }

    def fake(url: str, timeout: float) -> dict:  # type: ignore[type-arg]
        base = url.removesuffix("/api/v1/info")
        if base not in answers:
            raise OSError("refused")
        return answers[base]

    monkeypatch.setattr(upshot, "_get_json", fake)
    monkeypatch.setattr(
        upshot,
        "candidates",
        lambda url=None: iter([("http://127.0.0.1:8000", 2.0), ("http://127.0.0.1:8010", 2.0)]),
    )
    assert upshot.discover().base == "http://127.0.0.1:8010"
    monkeypatch.setattr(
        upshot, "candidates", lambda url=None: iter([("http://127.0.0.1:8000", 2.0)])
    )
    with pytest.raises(upshot.NotRunning, match="Start Upshot"):
        upshot.discover()


def test_a_slow_upshot_is_busy_not_gone() -> None:
    """Machine B, 2026-10-05: under a CPU-bound transcription Upshot answered more slowly
    than the bridge waited, and Claude was told it was not running."""
    import socket
    import threading
    import time

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    port = server.getsockname()[1]
    accepted: list[socket.socket] = []

    def accept() -> None:
        connection, _ = server.accept()  # accepts, never answers
        accepted.append(connection)

    threading.Thread(target=accept, daemon=True).start()
    app = upshot.Upshot(f"http://127.0.0.1:{port}", timeout=0.5)
    started = time.monotonic()
    with pytest.raises(upshot.Busy, match="busy"):
        app.get("tr_x")
    assert time.monotonic() - started < 5
    server.close()
    for connection in accepted:
        connection.close()


def test_the_bridge_keeps_the_upshot_it_found(monkeypatch: pytest.MonkeyPatch) -> None:
    found: list[str] = []

    class App:
        info: ClassVar[dict[str, bool]] = {"enabled": True}

        def refresh(self) -> None:
            found.append("refresh")

    def discover(url: str | None = None) -> App:
        found.append("discover")
        return App()

    monkeypatch.setattr(tools, "discover", discover)
    bridge = tools.Bridge()
    bridge.upshot()
    bridge.upshot()
    bridge.upshot()
    assert found == ["discover", "refresh", "refresh"]


def test_a_busy_upshot_is_said_plainly(monkeypatch: pytest.MonkeyPatch) -> None:
    class App:
        info: ClassVar[dict[str, bool]] = {"enabled": True}

        def refresh(self) -> None:
            raise upshot.Busy(upshot.BUSY)

    monkeypatch.setattr(tools, "discover", lambda url=None: App())
    bridge = tools.Bridge()
    bridge._app = App()  # type: ignore[assignment]
    with pytest.raises(ToolError, match="busy") as caught:
        bridge.upshot()
    assert "isn't running" not in str(caught.value)


# ----------------------------------------------------------------------- the path rule


@pytest.mark.parametrize(
    "path",
    [
        "\\\\attacker\\share\\a.wav",
        "//attacker/share/a.wav",
        "\\\\?\\C:\\a.wav",
        "\\\\.\\pipe\\x",
        "clip.wav",
        "C:clip.wav",
    ],
)
def test_the_bridge_refuses_what_the_app_refuses(path: str) -> None:
    with pytest.raises(paths.PathError):
        paths.check_local(path)


def test_the_wsl_shares_and_drives_pass() -> None:
    for path in ("C:\\x.wav", "d:/x.wav", "\\\\wsl.localhost\\Ubuntu\\x", "\\\\wsl$\\U\\x"):
        paths.check_local(path)


def test_a_network_path_is_never_opened_by_the_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    """The PR #1 review: on the app's 400 the bridge checked is_file() and uploaded, so it
    signed in to any host itself. Now nothing touches the disk or the app."""
    touched: list[str] = []
    monkeypatch.setattr(tools.Path, "is_file", lambda self: touched.append("is_file") or True)

    class App:
        def submit_path(self, path: str, options: dict) -> dict:  # type: ignore[type-arg]
            touched.append("submit")
            raise upshot.UpshotError(400, "path: network paths are not accepted")

        def upload(self, path: Path, options: dict) -> dict:  # type: ignore[type-arg]
            touched.append("upload")
            return {}

    with pytest.raises(ToolError, match="network paths"):
        tools.Bridge()._submit(App(), "\\\\attacker\\share\\a.wav", {})  # type: ignore[arg-type]
    assert touched == []


# ----------------------------------------------------------------------- save_to


class _Done:
    def result(self, job_id: str, fmt: str, **params: object) -> str:
        return f"the {fmt}"


_JOB = {"id": "tr_1", "state": "done", "source_name": "talk.mp4", "language": "en"}


def test_save_to_never_overwrites(tmp_path: Path) -> None:
    kept = tmp_path / "talk.srt"
    kept.write_text("mine", encoding="utf-8")
    bridge = tools.Bridge()
    said = bridge._save(_Done(), dict(_JOB), "srt", str(tmp_path))  # type: ignore[arg-type]
    assert kept.read_text(encoding="utf-8") == "mine"
    assert (tmp_path / "talk (2).srt").read_text(encoding="utf-8") == "the srt"
    assert "talk (2).srt" in said
    bridge._save(_Done(), dict(_JOB), "srt", str(kept))  # type: ignore[arg-type]
    assert kept.read_text(encoding="utf-8") == "mine"
    assert (tmp_path / "talk (3).srt").exists()


def test_save_to_refuses_a_network_path_and_a_wrong_name(tmp_path: Path) -> None:
    bridge = tools.Bridge()
    with pytest.raises(ToolError, match="network paths"):
        bridge._save(_Done(), dict(_JOB), "srt", "\\\\attacker\\share")  # type: ignore[arg-type]
    with pytest.raises(ToolError, match=r"ending in \.srt"):
        bridge._save(_Done(), dict(_JOB), "srt", str(tmp_path / "notes.docx"))  # type: ignore[arg-type]
    assert not (tmp_path / "notes.docx").exists()
    bridge._save(_Done(), dict(_JOB), "srt", str(tmp_path / "new folder"))  # type: ignore[arg-type]
    assert (tmp_path / "new folder" / "talk.srt").exists()
