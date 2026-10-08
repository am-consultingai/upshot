"""/api/v1: the file transcription service over HTTP, through the real middleware (D86)."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path
from urllib.parse import unquote

import httpx
import pytest
from fastapi import HTTPException

from app.transcription import api
from tests.fixtures.api import ApiHarness, build_harness, serve
from tests.fixtures.meetings import speechish

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture
def h(tmp_path: Path, app_home: Path) -> ApiHarness:
    return build_harness(tmp_path)


def wav(path: Path, seconds: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(speechish(seconds).tobytes())
    return path


def video(path: Path, seconds: int, *, audio: bool = True) -> Path:
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-f", "lavfi", "-i", f"testsrc=size=64x48:rate=5:duration={seconds}"]  # fmt: skip
    if audio:
        command += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    command += ["-c:v", "mpeg4", *(["-c:a", "aac"] if audio else []), "-shortest", str(path)]
    subprocess.run(command, check=True)
    return path


class Program:
    """A local program: no cookie, no CSRF header. The app hands every caller the cookie
    (D57), so a program that keeps it would count as the page; this one never does."""

    def __init__(self, h: ApiHarness) -> None:
        self.client = h.client(authorized=False)

    def __getattr__(self, method: str):  # type: ignore[no-untyped-def]
        call = getattr(self.client, method)

        def fresh(*args, **kwargs):  # type: ignore[no-untyped-def]
            self.client.cookies.clear()
            return call(*args, **kwargs)

        return fresh


def upload(client, path: Path, name: str | None = None, **fields: str):  # type: ignore[no-untyped-def]
    with path.open("rb") as handle:
        return client.post(
            "/api/v1/transcriptions",
            files={"file": (name or path.name, handle, "application/octet-stream")},
            data=fields,
        )


def run_worker(h: ApiHarness) -> None:
    assert h.services.worker is not None
    h.services.worker.drain()


def leftovers(h: ApiHarness) -> list[Path]:
    incoming = h.services.transcriptions.incoming()
    return list(incoming.iterdir()) if incoming.exists() else []


# ----------------------------------------------------------------------- upload to result


def test_an_upload_is_queued_transcribed_and_downloadable(h: ApiHarness, tmp_path: Path) -> None:
    program = Program(h)
    response = upload(program, wav(tmp_path / "in.wav", 20), name="ראיון.wav", language="he")
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["state"] == "pending" and job["position"] == 1
    assert job["waiting_reason"] == "queue" and job["client"] == "api"
    assert job["links"]["result"] == f"/api/v1/transcriptions/{job['id']}/result"
    assert job["duration_s"] == pytest.approx(20, abs=0.1)
    store = h.services.transcriptions
    assert (store.folder(job["id"]) / "input" / "source.wav").exists()
    assert leftovers(h) == []

    run_worker(h)

    done = program.get(f"/api/v1/transcriptions/{job['id']}").json()
    assert done["state"] == "done" and done["progress"] == 1.0 and done["language"] == "he"
    for fmt in ("json", "txt", "md", "srt", "vtt"):
        got = program.get(f"/api/v1/transcriptions/{job['id']}/result", params={"format": fmt})
        assert got.status_code == 200, (fmt, got.text)
        disposition = got.headers["content-disposition"]
        assert unquote(disposition.split("filename*=UTF-8''")[1]) == f"ראיון.{fmt}"
        assert got.text
    assert program.get(f"/api/v1/transcriptions/{job['id']}/result").json()["segments"]


def test_a_video_is_accepted_and_one_with_no_audio_is_refused(
    h: ApiHarness, tmp_path: Path
) -> None:
    program = Program(h)
    assert upload(program, video(tmp_path / "clip.mp4", 4)).status_code == 202
    silent = upload(program, video(tmp_path / "silent.mp4", 2, audio=False))
    assert silent.status_code == 415 and "silent.mp4 has no audio" in silent.json()["detail"]
    junk = tmp_path / "notes.mp3"
    junk.write_text("not audio", encoding="utf-8")
    assert upload(program, junk).status_code == 415
    assert leftovers(h) == [], "nothing is left behind by a refused upload"
    assert len(h.services.transcriptions.recent()) == 1


def test_the_uploaded_name_never_becomes_a_path(h: ApiHarness, tmp_path: Path) -> None:
    response = upload(Program(h), wav(tmp_path / "a.wav", 3), name="..\\..\\evil name.WAV")
    job = response.json()
    assert job["source_name"] == "evil name.WAV"
    folder = h.services.transcriptions.folder(job["id"])
    assert [p.name for p in (folder / "input").iterdir()] == ["source.wav"]


def test_size_limits(h: ApiHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    program = Program(h)
    source = wav(tmp_path / "a.wav", 5)
    h.services.config.set("transcription.max_upload_mb", 0.01)
    assert upload(program, source).status_code == 413
    h.services.config.set("transcription.max_upload_mb", 4096)
    monkeypatch.setattr(api, "free_bytes", lambda svc: 1000)
    assert upload(program, source).status_code == 507
    assert leftovers(h) == []


def test_a_body_with_no_length_is_refused(h: ApiHarness) -> None:
    def body():  # type: ignore[no-untyped-def]
        yield b'--x\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n\r\n'
        yield b"data\r\n--x--\r\n"

    response = Program(h).post(
        "/api/v1/transcriptions",
        content=body(),
        headers={"content-type": "multipart/form-data; boundary=x"},
    )
    assert response.status_code == 411


def test_a_file_longer_than_max_hours_is_refused(h: ApiHarness, tmp_path: Path) -> None:
    h.services.config.set("transcription.max_hours", 0.001)  # 3.6 seconds
    response = upload(Program(h), wav(tmp_path / "a.wav", 10))
    assert response.status_code == 413 and "hours" in response.json()["detail"]


def test_max_hours_follows_the_device(h: ApiHarness) -> None:
    svc = h.services
    svc.config.set("asr.backend", "local")
    svc.extras["transcription_device"] = "cpu"
    assert api.max_hours(svc) == 2
    svc.extras["transcription_device"] = "cuda"
    assert api.max_hours(svc) == 6
    svc.config.set("transcription.max_hours", 3)
    assert api.max_hours(svc) == 3


@pytest.mark.parametrize(
    ("fields", "detail"),
    [
        ({"language": "xx"}, "unknown language"),
        ({"max_words_per_cue": "0"}, "max_words_per_cue"),
        ({"max_words_per_cue": "seven"}, "whole number"),
        ({"diarize": "maybe"}, "diarize"),
        ({"prompt": "x" * 1001}, "prompt"),
    ],
)
def test_bad_options_are_422(h: ApiHarness, tmp_path: Path, fields: dict, detail: str) -> None:  # type: ignore[type-arg]
    response = upload(Program(h), wav(tmp_path / "a.wav", 3), **fields)
    assert response.status_code == 422 and detail in response.json()["detail"]
    assert leftovers(h) == []


def test_a_form_with_no_file_is_422(h: ApiHarness) -> None:
    response = Program(h).post("/api/v1/transcriptions", files={"language": (None, "he")})
    assert response.status_code == 422


def test_other_bodies_are_415(h: ApiHarness) -> None:
    response = Program(h).post(
        "/api/v1/transcriptions", content=b"x", headers={"content-type": "text/plain"}
    )
    assert response.status_code == 415


# ----------------------------------------------------------------------- paths


def test_a_path_is_read_in_place(h: ApiHarness, tmp_path: Path) -> None:
    source = wav(tmp_path / "mine" / "clip.wav", 5)
    response = Program(h).post(
        "/api/v1/transcriptions", json={"path": str(source), "language": "en", "diarize": False}
    )
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["source_kind"] == "path" and job["options"]["diarize"] is False
    run_worker(h)
    assert h.services.transcriptions.require(job["id"]).state == "done"
    assert source.exists(), "a path source is never moved or deleted"


@pytest.mark.parametrize(
    ("path", "detail"),
    [
        ("relative/clip.wav", "full path"),
        ("\\\\fileserver\\share\\clip.wav", "network paths"),
        ("//fileserver/share/clip.wav", "network paths"),
        ("\\\\?\\C:\\clip.wav", "device paths"),
        ("\\\\.\\PhysicalDrive0", "device paths"),
        ("", "full path"),
    ],
)
def test_paths_that_are_refused(h: ApiHarness, path: str, detail: str) -> None:
    response = Program(h).post("/api/v1/transcriptions", json={"path": path})
    assert response.status_code == 400 and detail in response.json()["detail"]


def test_a_wsl_share_is_allowed_and_a_missing_or_odd_file_is_not(
    h: ApiHarness, tmp_path: Path
) -> None:
    program = Program(h)
    share = program.post(
        "/api/v1/transcriptions", json={"path": "\\\\wsl.localhost\\Ubuntu\\x.wav"}
    )
    assert "no such file" in share.json()["detail"], "the share is not refused, only absent here"
    folder = program.post("/api/v1/transcriptions", json={"path": str(tmp_path)})
    assert "not a file" in folder.json()["detail"]


def test_on_windows_a_posix_path_points_to_the_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "_posix_host", lambda: False)
    with pytest.raises(HTTPException, match="upshot-mcp bridge"):
        api.check_path("/home/someone/clip.wav")
    with pytest.raises(HTTPException, match="upshot-mcp bridge"):
        api.check_path("/mnt/c/Users/someone/clip.wav")


# ----------------------------------------------------------------------- websites, CSRF


def test_websites_are_refused(h: ApiHarness, tmp_path: Path) -> None:
    program = Program(h)
    port = h.services.config.server_port
    source = str(wav(tmp_path / "a.wav", 3))
    for headers in (
        {"origin": "https://evil.example"},
        {"origin": "null"},
        {"origin": f"http://127.0.0.1:{port + 1}"},
        {"sec-fetch-site": "cross-site"},
        {"sec-fetch-site": "same-site"},
    ):
        refused = program.post("/api/v1/transcriptions", json={"path": source}, headers=headers)
        assert refused.status_code == 403, headers
        assert program.get("/api/v1/transcriptions", headers=headers).status_code == 403
    for own in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
        allowed = program.post(
            "/api/v1/transcriptions", json={"path": source},
            headers={"origin": own, "sec-fetch-site": "same-origin"},
        )  # fmt: skip
        assert allowed.status_code == 202, own


def test_the_guard_is_only_on_the_open_api(h: ApiHarness) -> None:
    status = h.client().get("/api/status", headers={"sec-fetch-site": "cross-site"})
    assert status.status_code == 200


def test_programs_need_no_csrf_token_here_and_still_do_elsewhere(
    h: ApiHarness, tmp_path: Path
) -> None:
    program = Program(h)
    assert (
        program.post(
            "/api/v1/transcriptions", json={"path": str(wav(tmp_path / "a.wav", 3))}
        ).status_code
        == 202
    )
    assert program.post("/api/recording/start").status_code == 403


def test_the_middleware_runs_host_origin_auth_csrf(h: ApiHarness) -> None:
    names = [m.cls.__name__ for m in h.app.user_middleware]
    assert names == [
        "HostHeaderMiddleware",
        "OriginGuardMiddleware",
        "AuthMiddleware",
        "CsrfMiddleware",
    ]


# ----------------------------------------------------------------------- the switch


def test_with_the_switch_off_programs_are_refused_and_the_page_is_not(
    h: ApiHarness, tmp_path: Path
) -> None:
    program, page = Program(h), h.client()
    source = wav(tmp_path / "a.wav", 5)
    job = upload(program, source).json()
    run_worker(h)
    h.services.config.set("transcription.service_enabled", False)

    refused = upload(program, source)
    assert refused.status_code == 403 and "off in Upshot's Settings" in refused.json()["detail"]
    assert program.get("/api/v1/transcriptions").status_code == 403
    info = program.get("/api/v1/info")
    assert info.status_code == 200 and info.json()["enabled"] is False

    assert upload(page, source).status_code == 202
    assert page.get("/api/v1/transcriptions").status_code == 200
    page.headers.pop("x-csrf-token")  # a plain <a href> download: the cookie alone
    download = page.get(f"/api/v1/transcriptions/{job['id']}/result", params={"format": "srt"})
    assert download.status_code == 200


# ----------------------------------------------------------------------- changing jobs


def test_cancel_retry_delete_and_the_409s(h: ApiHarness, tmp_path: Path) -> None:
    program = Program(h)
    job = upload(program, wav(tmp_path / "a.wav", 3)).json()
    base = f"/api/v1/transcriptions/{job['id']}"
    assert program.get(f"{base}/result").status_code == 409, "not done yet"
    assert program.post(f"{base}/cancel").json()["state"] == "cancelled"
    assert program.get(base).json()["position"] is None
    retried = program.post(f"{base}/retry")
    assert retried.status_code == 202 and retried.json()["state"] == "pending"
    assert program.post(f"{base}/retry").status_code == 409, "a waiting job is not retried"
    assert program.delete(base).json() == {
        "id": job["id"],
        "deleted": True,
        "pending_delete": False,
    }
    assert program.get(base).status_code == 404
    assert program.get("/api/v1/transcriptions/tr_nope").status_code == 404


def test_a_retried_upload_whose_copy_is_gone_says_so(h: ApiHarness, tmp_path: Path) -> None:
    program = Program(h)
    job = upload(program, wav(tmp_path / "a.wav", 3)).json()
    run_worker(h)  # done, and the uploaded copy is removed (keep_input off)
    h.services.transcriptions.conn.execute(
        "UPDATE transcriptions SET state='failed' WHERE id=?", (job["id"],)
    )
    response = program.post(f"/api/v1/transcriptions/{job['id']}/retry")
    assert response.status_code == 409 and "add it again" in response.json()["detail"]


def test_a_bad_format_is_422(h: ApiHarness, tmp_path: Path) -> None:
    program = Program(h)
    job = upload(program, wav(tmp_path / "a.wav", 3)).json()
    run_worker(h)
    got = program.get(f"/api/v1/transcriptions/{job['id']}/result", params={"format": "docx"})
    assert got.status_code == 422


def test_the_list_pages_newest_first(h: ApiHarness, tmp_path: Path) -> None:
    program = Program(h)
    source = wav(tmp_path / "a.wav", 3)
    ids = []
    for _ in range(3):
        ids.append(upload(program, source).json()["id"])
        h.clock.advance(1)
    listed = program.get("/api/v1/transcriptions", params={"limit": 2}).json()["transcriptions"]
    assert [job["id"] for job in listed] == ids[:0:-1]
    rest = program.get("/api/v1/transcriptions", params={"before": listed[-1]["created_at"]})
    assert [job["id"] for job in rest.json()["transcriptions"]] == ids[:1]


def test_a_change_is_published_as_an_event(h: ApiHarness, tmp_path: Path) -> None:
    job = upload(Program(h), wav(tmp_path / "a.wav", 3)).json()
    sent = [e.payload for e in h.services.events.history if e.type == "transcription"]
    expected = {"id": job["id"], "state": "pending", "phase": None, "progress": 0.0, "eta_s": None}
    assert expected in sent


def test_info(h: ApiHarness) -> None:
    info = Program(h).get("/api/v1/info").json()
    assert info["app"] == "upshot" and info["api"] == 1 and info["enabled"] is True
    assert info["formats"] == ["json", "txt", "md", "srt", "vtt"]
    assert "he" in info["languages"] and info["languages"][0] == "auto"
    assert info["max_hours"] == 6 and info["max_upload_mb"] == 4096


# ----------------------------------------------------------------------- waiting


def test_wait_returns_at_once_when_done_and_at_the_timeout_when_not(
    h: ApiHarness, tmp_path: Path
) -> None:
    program = Program(h)
    job = upload(program, wav(tmp_path / "a.wav", 3)).json()
    started = time.monotonic()
    waited = program.get(f"/api/v1/transcriptions/{job['id']}/wait", params={"timeout": 0.3})
    assert waited.json()["state"] == "pending" and time.monotonic() - started < 5
    run_worker(h)
    assert program.get(f"/api/v1/transcriptions/{job['id']}/wait").json()["state"] == "done"
    assert (
        program.get(f"/api/v1/transcriptions/{job['id']}/wait", params={"timeout": 999}).status_code
        == 422
    )


def test_wait_wakes_when_the_worker_finishes(h: ApiHarness, tmp_path: Path) -> None:
    with serve(h) as client:
        client.headers.pop("x-csrf-token", None)
        job = upload(client, wav(tmp_path / "a.wav", 3)).json()
        threading.Timer(0.5, run_worker, args=(h,)).start()
        started = time.monotonic()
        waited = client.get(f"/api/v1/transcriptions/{job['id']}/wait", params={"timeout": 30})
        assert waited.json()["state"] == "done"
        assert time.monotonic() - started < 15


def test_fifty_waiting_clients_do_not_block_the_app(h: ApiHarness, tmp_path: Path) -> None:
    with serve(h) as client:
        job = upload(client, wav(tmp_path / "a.wav", 3)).json()
        base = f"http://127.0.0.1:{h.services.config.server_port}"

        async def scenario() -> float:
            async with httpx.AsyncClient(base_url=base, timeout=30) as http:
                waits = [
                    asyncio.create_task(
                        http.get(f"/api/v1/transcriptions/{job['id']}/wait", params={"timeout": 4})
                    )
                    for _ in range(50)
                ]
                await asyncio.sleep(0.5)
                started = time.monotonic()
                status = await http.get("/api/status")
                elapsed = time.monotonic() - started
                assert status.status_code == 200
                await asyncio.gather(*waits)
                return elapsed

        assert asyncio.run(scenario()) < 2.0
