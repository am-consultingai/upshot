"""upshot-mcp end to end: the bridge as its own process, the official MCP client, and a
real Upshot on a socket with the fake ASR (D86)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import wave
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.fixtures.api import ApiHarness, build_harness, serve
from tests.fixtures.meetings import speechish

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

CLIENTS = Path(__file__).resolve().parents[2] / "clients" / "python"


def wav(path: Path, seconds: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(speechish(seconds).tobytes())
    return path


@contextmanager
def running(h: ApiHarness, *, worker: bool = True) -> Iterator[str]:
    """Upshot on a real port, with the worker draining the queue in the background."""
    stop = threading.Event()

    def drain() -> None:
        while not stop.is_set():
            h.services.worker.drain()  # type: ignore[union-attr]
            time.sleep(0.05)

    with serve(h):
        thread = threading.Thread(target=drain, daemon=True)
        if worker:
            thread.start()
        try:
            yield f"http://127.0.0.1:{h.services.config.server_port}"
        finally:
            stop.set()
            if worker:
                thread.join(timeout=10)


def params(url: str) -> StdioServerParameters:
    env = {**os.environ, "PYTHONPATH": str(CLIENTS)}
    return StdioServerParameters(
        command=sys.executable, args=["-m", "upshot_mcp", "--url", url], env=env
    )


async def call(url: str, tool: str, arguments: dict) -> tuple[bool, str]:  # type: ignore[type-arg]
    async with stdio_client(params(url)) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool(tool, arguments)
        return bool(result.is_error), result.content[0].text  # type: ignore[union-attr]


@pytest.fixture
def h(tmp_path: Path, app_home: Path) -> ApiHarness:
    return build_harness(tmp_path)


async def test_the_official_client_sees_four_tools(h: ApiHarness) -> None:
    with running(h) as url:
        async with (
            stdio_client(params(url)) as (read, write),
            ClientSession(read, write) as session,
        ):
            init = await session.initialize()
            listed = await session.list_tools()
    assert init.server_info.name == "upshot-transcribe"
    assert sorted(t.name for t in listed.tools) == [
        "cancel_transcription",
        "get_transcription",
        "list_transcriptions",
        "transcribe_file",
    ]


async def test_a_file_is_transcribed_in_one_call(h: ApiHarness, tmp_path: Path) -> None:
    source = wav(tmp_path / "in" / "ראיון.wav", 20)
    with running(h) as url:
        error, text = await call(url, "transcribe_file", {"path": str(source), "language": "he"})
    assert not error, text
    assert text.startswith("ראיון.wav (00:20, language he")
    assert "[00:00] S1:" in text
    job = h.services.transcriptions.recent()[0]
    assert job.client == "mcp" and job.source_kind == "path"


async def test_a_long_job_returns_an_id_then_finishes_through_get(
    h: ApiHarness, tmp_path: Path
) -> None:
    source = wav(tmp_path / "clip.wav", 10)
    with running(h, worker=False) as url:
        error, text = await call(url, "transcribe_file", {"path": str(source), "wait_seconds": 1})
        assert not error
        pending = json.loads(text)
        assert pending["state"] == "pending" and "get_transcription" in pending["next"]
        h.services.worker.drain()  # type: ignore[union-attr]
        error, text = await call(url, "get_transcription", {"id": pending["id"], "format": "srt"})
    assert not error and text.startswith("1\n00:00:00,000 --> ")


async def test_save_to_writes_the_file_next_to_the_source(h: ApiHarness, tmp_path: Path) -> None:
    source = wav(tmp_path / "videos" / "talk.wav", 8)
    with running(h) as url:
        error, text = await call(
            url,
            "transcribe_file",
            {"path": str(source), "format": "srt", "save_to": str(source.parent)},
        )
    assert not error, text
    saved = source.parent / "talk.srt"
    assert saved.exists() and "-->" in saved.read_text(encoding="utf-8")
    assert "Saved the srt transcript of talk.wav" in text


async def test_list_and_cancel(h: ApiHarness, tmp_path: Path) -> None:
    source = wav(tmp_path / "clip.wav", 6)
    with running(h, worker=False) as url:
        _, text = await call(url, "transcribe_file", {"path": str(source), "wait_seconds": 0})
        job_id = json.loads(text)["id"]
        _, listed = await call(url, "list_transcriptions", {})
        assert job_id in listed and "waiting, position 1" in listed
        _, cancelled = await call(url, "cancel_transcription", {"id": job_id})
    assert "is cancelled" in cancelled


async def test_plain_words_when_things_are_wrong(h: ApiHarness, tmp_path: Path) -> None:
    with running(h) as url:
        error, text = await call(url, "transcribe_file", {"path": str(tmp_path / "missing.wav")})
        assert error and "no such file" in text
        h.services.config.set("transcription.service_enabled", False)
        error, text = await call(url, "transcribe_file", {"path": str(wav(tmp_path / "a.wav", 3))})
        assert error and "off in Upshot" in text
    error, text = await call("http://127.0.0.1:9", "list_transcriptions", {})
    assert error and "Start Upshot" in text


def test_the_bridge_exits_as_soon_as_its_input_closes() -> None:
    """A client stopping its server closes stdin; a server that lingers keeps its files
    locked, and Claude Desktop's uninstall fails (bug z8tj1he4zz)."""
    process = subprocess.Popen(
        [sys.executable, "-m", "upshot_mcp", "--url", "http://127.0.0.1:9"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": str(CLIENTS)},
    )
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write(b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n')
    process.stdin.flush()
    assert (
        json.loads(process.stdout.readline())["result"]["serverInfo"]["name"] == "upshot-transcribe"
    )
    started = time.monotonic()
    process.stdin.close()
    assert process.wait(timeout=5) == 0
    assert time.monotonic() - started < 2


def test_the_command_line_writes_subtitles(h: ApiHarness, tmp_path: Path) -> None:
    source = wav(tmp_path / "clip.wav", 8)
    out = tmp_path / "out"
    with running(h) as url:
        done = subprocess.run(
            [
                sys.executable,
                "-m",
                "upshot_mcp",
                "--url",
                url,
                "transcribe",
                str(source),
                "--format",
                "srt",
                "-o",
                str(out),
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "PYTHONPATH": str(CLIENTS)},
        )
    assert done.returncode == 0, done.stderr
    printed = json.loads(done.stdout)
    assert printed["job"]["state"] == "done" and (out / "clip.srt").exists()


def test_the_bridge_uploads_when_asked_to(h: ApiHarness, tmp_path: Path) -> None:
    sys.path.insert(0, str(CLIENTS))
    from upshot_mcp.upshot import Upshot

    source = wav(tmp_path / "clip.wav", 5)
    with running(h) as url:
        job = Upshot(url).upload(source, {"language": "auto", "diarize": True, "prompt": ""})
    assert job["source_kind"] == "upload" and job["client"] == "mcp"
