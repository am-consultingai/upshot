from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.api.security import CSRF_HEADER, SESSION_COOKIE
from app.pipeline.states import MeetingState
from tests.fixtures.api import build_harness, serve


@pytest.fixture
def api(tmp_path: Path, app_home: Path):  # type: ignore[no-untyped-def]
    return build_harness(tmp_path)


# ------------------------------------------------------------------ security


def test_host_header_rejected(api) -> None:  # type: ignore[no-untyped-def]
    """The DNS-rebinding defense runs before routing: the handler is never entered."""
    from fastapi.routing import APIRoute

    entered: list[str] = []

    def spy() -> dict[str, bool]:
        entered.append("yes")
        return {"ok": True}

    # Inserted ahead of the SPA catch-all, but behind the same middleware stack.
    api.app.router.routes.insert(0, APIRoute("/api/spy", spy, methods=["GET"]))

    client = api.client()
    response = client.get("/api/spy", headers={"Host": "evil.com"})
    assert response.status_code == 421
    assert "not allowed" in response.json()["detail"]
    assert entered == [], "the route handler must never run for a rejected Host"
    assert client.get("/api/spy").status_code == 200
    assert entered == ["yes"]


def test_host_header_allowed(api) -> None:  # type: ignore[no-untyped-def]
    client = api.client()
    port = api.services.config.server_port
    for host in (f"localhost:{port}", f"127.0.0.1:{port}", "127.0.0.1", "localhost"):
        assert client.get("/api/status", headers={"Host": host}).status_code == 200


def test_requires_cookie(api) -> None:  # type: ignore[no-untyped-def]
    client = api.client(authorized=False)
    response = client.get("/api/status")
    assert response.status_code == 401
    assert "tray" in response.json()["detail"]
    assert "location" not in {key.lower() for key in response.headers}


def test_token_exchange_once(api) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    token = api.services.auth.issue_token()
    client = TestClient(api.app, base_url=api.base_url)
    first = client.get(f"/?k={token}")
    assert first.status_code == 200
    assert SESSION_COOKIE in first.cookies or SESSION_COOKIE in client.cookies

    other = TestClient(api.app, base_url=api.base_url)
    second = other.get(f"/?k={token}")
    assert second.status_code == 401
    assert "already been used" in second.json()["detail"]


def test_spent_token_does_not_lock_out_an_authorized_browser(api) -> None:  # type: ignore[no-untyped-def]
    """The launcher opens the link, then the user clicks the printed one. Same browser,
    already has the cookie: the second visit must work, not 401."""
    from fastapi.testclient import TestClient

    token = api.services.auth.issue_token()
    client = TestClient(api.app, base_url=api.base_url)
    assert client.get(f"/?k={token}").status_code == 200
    assert client.get(f"/?k={token}").status_code == 200  # spent token, valid cookie
    assert client.get("/").status_code == 200  # and without the token at all


def test_csrf_required_on_mutations(api) -> None:  # type: ignore[no-untyped-def]
    client = api.client()
    del client.headers[CSRF_HEADER]
    assert client.post("/api/detector/ignore", json={"process": "x.exe"}).status_code == 403
    client.headers[CSRF_HEADER] = api.services.auth.csrf_secret
    assert client.post("/api/detector/ignore", json={"process": "x.exe"}).status_code == 200


def test_no_cors_headers(api) -> None:  # type: ignore[no-untyped-def]
    client = api.client()
    for path in ("/api/status", "/api/meetings", "/"):
        response = client.get(path)
        assert "access-control-allow-origin" not in {k.lower() for k in response.headers}


def test_bind_address(tmp_path: Path, app_home: Path) -> None:
    from app.server import LocalServer

    harness = build_harness(tmp_path)
    server = LocalServer(harness.app, host="127.0.0.1", port=0).start()
    try:
        socket_name = server.sockets[0].getsockname()
        assert socket_name[0] == "127.0.0.1"
    finally:
        server.stop()
    with pytest.raises(ValueError, match="local-only"):
        LocalServer(harness.app, host="0.0.0.0", port=0)


def test_secrets_never_reach_the_dom(api) -> None:  # type: ignore[no-untyped-def]
    api.services.config.set("llm.api_key", "sk-ant-SECRET")
    payload = api.client().get("/api/settings").json()
    assert "SECRET" not in json.dumps(payload)
    assert payload["config"]["llm"]["api_key"] == "***"


# ------------------------------------------------------------------ meetings


def seed(api, count: int = 3):  # type: ignore[no-untyped-def]
    ids = []
    for index in range(count):
        meeting = api.services.dao.insert_meeting(
            meeting_id=f"m{index}",
            folder=api.services.config.data_root / f"m{index}",
            source="manual",
            started_at=f"2026-08-2{index}T10:00:00+03:00",
            title=f"Meeting {index}",
        )
        ids.append(meeting.id)
    return ids


def test_meetings_filtering(api) -> None:  # type: ignore[no-untyped-def]
    ids = seed(api, 3)
    api.services.dao.set_state(ids[1], MeetingState.RECORDED)
    client = api.client()
    everything = client.get("/api/meetings").json()
    assert everything["count"] == 3
    assert [m["id"] for m in everything["meetings"]] == sorted(ids, reverse=True)

    by_state = client.get("/api/meetings", params={"state": "RECORDED"}).json()
    assert [m["id"] for m in by_state["meetings"]] == [ids[1]]

    by_date = client.get("/api/meetings", params={"from": "2026-08-21T00:00:00+03:00"}).json()
    assert {m["id"] for m in by_date["meetings"]} == {ids[1], ids[2]}

    from app.db.dao import Turn

    api.services.dao.index_turns(ids[2], [Turn(0, "ME", 0, "kubernetes migration")])
    by_query = client.get("/api/meetings", params={"q": "kubernetes"}).json()
    assert [m["id"] for m in by_query["meetings"]] == [ids[2]]


def test_meeting_detail_and_patch(api) -> None:  # type: ignore[no-untyped-def]
    ids = seed(api, 1)
    client = api.client()
    detail = client.get(f"/api/meetings/{ids[0]}").json()
    assert detail["id"] == ids[0] and detail["jobs"] == []
    patched = client.patch(f"/api/meetings/{ids[0]}", json={"title": "Renamed"}).json()
    assert patched["title"] == "Renamed" and patched["title_source"] == "user"
    assert client.get("/api/meetings/nope").status_code == 404
    discarded = client.patch(f"/api/meetings/{ids[0]}", json={"discard": True}).json()
    assert discarded["state"] == MeetingState.DISCARDED


def test_range_request_audio(api, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    import wave

    ids = seed(api, 1)
    folder = api.services.config.data_root / ids[0] / "audio"
    folder.mkdir(parents=True)
    # A real WAV named for its track: audio is one file per track, and the route serves
    # that file. A fixture of arbitrary bytes would test a path that cannot occur.
    with wave.open(str(folder / "them.wav"), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x01\x02" * 2538)
    size = 44 + 2538 * 2  # 5120

    client = api.client()
    response = client.get(
        f"/api/meetings/{ids[0]}/audio",
        params={"track": "them"},
        headers={"Range": "bytes=0-999"},
    )
    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes 0-999/{size}"
    assert len(response.content) == 1000
    full = client.get(f"/api/meetings/{ids[0]}/audio", params={"track": "them"})
    assert full.status_code == 200 and full.headers["accept-ranges"] == "bytes"
    assert len(full.content) == size
    assert client.get(f"/api/meetings/{ids[0]}/audio", params={"track": "me"}).status_code == 404


def test_retry_endpoint_reenqueues(api) -> None:  # type: ignore[no-untyped-def]
    ids = seed(api, 1)
    job = api.services.queue.enqueue(ids[0], "transcribe")
    api.services.queue.fail(job, "boom", permanent=True)
    assert api.services.queue.get(job.id).state == "failed"  # type: ignore[union-attr]
    body = api.client().post(f"/api/meetings/{ids[0]}/jobs/transcribe/retry").json()
    assert body == {"stage": "transcribe", "state": "pending", "attempts": 0}
    assert api.client().post(f"/api/meetings/{ids[0]}/jobs/nope/retry").status_code == 404


def test_artifacts_404_before_they_exist(api) -> None:  # type: ignore[no-untyped-def]
    ids = seed(api, 1)
    client = api.client()
    for suffix in ("transcript", "notes", "summary.html"):
        assert client.get(f"/api/meetings/{ids[0]}/{suffix}").status_code == 404


def test_glossary_and_settings_roundtrip(api) -> None:  # type: ignore[no-untyped-def]
    client = api.client()
    assert client.get("/api/glossary").json() == {"terms": []}
    client.put("/api/glossary", json={"terms": [{"term": "ArgoCD", "aliases": "ארגו"}]})
    assert client.get("/api/glossary").json()["terms"][0]["term"] == "ArgoCD"

    client.put("/api/settings", json={"values": {"ui.language": "he"}})
    assert client.get("/api/settings").json()["config"]["ui"]["language"] == "he"
    bad = client.put("/api/settings", json={"values": {"ui.language": "klingon"}})
    assert bad.status_code == 500 or bad.status_code >= 400


def test_detector_events_and_ignore(api) -> None:  # type: ignore[no-untyped-def]
    api.services.dao.add_detector_event(
        peak_score=7,
        evidence=[{"code": "mic.known_app", "weight": 3}],
        outcome="shadow",
        process="Zoom.exe",
    )
    client = api.client()
    events = client.get("/api/detector/events").json()["events"]
    assert events[0]["outcome"] == "shadow" and events[0]["evidence"][0]["code"] == "mic.known_app"
    ignore = client.post("/api/detector/ignore", json={"process": "Spotify.exe"}).json()
    assert "Spotify.exe" in ignore["ignore"]


def test_status_shape(api) -> None:  # type: ignore[no-untyped-def]
    body = api.client().get("/api/status").json()
    assert body["recorder"]["active"] is False
    assert body["queue_depth"] == 0
    assert body["disk_free_bytes"] > 0
    assert "fts" in body


def test_test_seed_route_absent_by_default(api) -> None:  # type: ignore[no-untyped-def]
    assert api.client().post("/api/test/seed", json={}).status_code == 404


def test_test_seed_route_present_in_test_mode(
    tmp_path: Path, app_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MA_TEST_MODE", "1")
    harness = build_harness(tmp_path)
    client = harness.client()
    response = client.post(
        "/api/test/seed",
        json={"meetings": [{"id": "seeded-1", "title": "Seeded", "state": "RECORDING"}]},
    )
    assert response.status_code == 200
    assert response.json()["created"] == ["seeded-1"]
    assert client.get("/api/meetings").json()["count"] == 1


# ------------------------------------------------------------------ events


def test_sse_emits_state_change(api) -> None:  # type: ignore[no-untyped-def]
    """Runs against a real socket: TestClient cannot read an unbounded response."""
    import threading

    received: list[str] = []
    with serve(api) as client, client.stream("GET", "/api/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        lines = response.iter_lines()
        assert next(lines) == ": connected"

        def publish() -> None:
            api.services.events.publish("job", meeting_id="m1", stage="transcribe", state="done")

        threading.Timer(0.2, publish).start()
        for _ in range(40):
            line = next(lines)
            received.append(line)
            if line.startswith("data: "):
                break
    payload = json.loads(received[-1][len("data: ") :])
    assert payload == {
        "type": "job",
        "meeting_id": "m1",
        "stage": "transcribe",
        "state": "done",
    }


def test_openapi_snapshot(api, golden) -> None:  # type: ignore[no-untyped-def]
    document = api.app.openapi()
    golden("openapi.json", json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False))


# ------------------------------------------------------------------ recording


def test_recording_start_stop_roundtrip(api) -> None:  # type: ignore[no-untyped-def]
    client = api.client()
    started = client.post("/api/recording/start", json={"title": "Manual test"}).json()
    meeting_id = started["meeting_id"]
    assert api.services.recorder.committed is True
    assert client.get("/api/status").json()["recorder"]["active"] is True
    assert client.post("/api/recording/start", json={}).status_code == 409

    paused = client.post("/api/recording/pause").json()
    assert paused["paused"] is True
    client.post("/api/recording/pause")

    api.emit(seconds=130)  # the device produces audio; the writer thread drains it
    for _ in range(400):
        api.services.recorder.pump_once(0.0)
    stopped = client.post("/api/recording/stop").json()
    assert stopped["meeting_id"] == meeting_id
    assert stopped["chunks"] >= 1
    assert client.post("/api/recording/stop").status_code == 409

    meeting = api.services.dao.require_meeting(meeting_id)
    assert meeting.state in (MeetingState.RECORDED, MeetingState.DISCARDED)
    titles = api.services.notifier.titles()
    assert any("Recording" in title for title in titles)


def test_import_creates_a_meeting(api, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    import wave

    import numpy as np

    wav = tmp_path / "old-meeting.wav"
    payload = np.round(
        np.sin(2 * np.pi * 220 * np.arange(16000 * 130) / 16000) * 0.3 * 32767
    ).astype("<i2")
    with wave.open(str(wav), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(payload.tobytes())

    with wav.open("rb") as handle:
        response = api.client().post("/api/import", files={"file": ("old-meeting.wav", handle)})
    body = response.json()
    assert response.status_code == 200, body
    meeting = api.services.dao.require_meeting(body["meeting_id"])
    assert meeting.source == "imported"
    assert Path(body["file"]).exists()
    assert body["chunks"] >= 2 and body["duration_s"] == 130


def test_import_rejects_a_file_it_cannot_decode(api, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    bogus = tmp_path / "broken.wav"
    bogus.write_bytes(b"RIFF....WAVEfmt ")
    with bogus.open("rb") as handle:
        response = api.client().post("/api/import", files={"file": ("broken.wav", handle)})
    assert response.status_code in (415, 500)
    assert api.services.dao.list_meetings(state="DISCARDED") or response.status_code == 500


def test_foreign_token_names_the_real_cause(api) -> None:  # type: ignore[no-untyped-def]
    """A token this process never minted means another instance owns the port. Saying
    "already used" sent us hunting the wrong problem for an afternoon."""
    from fastapi.testclient import TestClient

    client = TestClient(api.app, base_url=api.base_url)
    response = client.get("/?k=aToKenFromSomeOtherProcess")
    assert response.status_code == 401
    detail = response.json()["detail"]
    assert "not issued by the app answering on this port" in detail
    assert "already been used" not in detail


def test_every_endpoint_the_ui_calls_exists() -> None:
    """A route deleted by an edit elsewhere is invisible until a screen goes blank.

    `/api/llm/prompt` was removed by a patch that replaced a range of routes.py. Nothing
    failed: the frontend got a 404, the component rendered null, and the Settings screen
    simply had no prompt editor on it. This walks the other way — from what the UI calls
    to what the server serves — so the next one is caught here instead of by eye.
    """
    import re
    from pathlib import Path

    from app.api.routes import router

    frontend = Path(__file__).resolve().parents[2] / "frontend" / "src"
    if not frontend.is_dir():  # pragma: no cover - a source-only check
        pytest.skip("frontend sources are not present")

    called = set()
    for source in frontend.rglob("*.ts*"):
        for match in re.finditer(r'"(/api/[a-zA-Z0-9_/.\-]+)', source.read_text(encoding="utf-8")):
            called.add(match.group(1).split("?")[0])

    patterns = [
        re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", route.path) + "$")
        for route in router.routes  # type: ignore[attr-defined]
    ]
    missing = sorted(path for path in called if not any(p.match(path) for p in patterns))
    assert not missing, f"the UI calls endpoints the server does not serve: {missing}"


def test_resummarize_does_not_re_transcribe(api) -> None:  # type: ignore[no-untyped-def]
    """The complaint that started this: pressing Re-summarize looked like it might be
    redoing the transcription, because it took minutes. It must not — transcription is
    the expensive half and its output has not changed."""
    ids = seed(api, 1)
    queue = api.services.queue
    for stage in ("transcribe", "assemble", "summarize", "render", "deliver"):
        queue.complete(queue.enqueue(ids[0], stage))

    body = api.client().post(f"/api/meetings/{ids[0]}/jobs/summarize/retry?force=true").json()
    assert body == {"stage": "summarize", "state": "pending", "attempts": 0}

    states = {job.stage: job.state for job in queue.for_meeting(ids[0])}
    assert states["transcribe"] == "done", "transcription was re-queued"
    assert states["assemble"] == "done", "assembly was re-queued"
    assert states["summarize"] == "pending"
    # `force` means "redo it", and that request must not reach the transcribe stage
    # either: a rerun there would discard the transcript and start the model again.
    assert queue.take_rerun(ids[0], "transcribe") is False
    assert queue.take_rerun(ids[0], "summarize") is True


def test_the_spa_shell_is_never_served_from_cache(api, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """A cached shell keeps asking for the bundle it was built against.

    The shell is the only document that names the content-hashed asset files, so a
    browser that reuses it without asking goes on loading the previous build's
    JavaScript and CSS. The application then looks completely unchanged after an
    update — which is exactly how it was found: a rebuilt interface that appeared
    not to have been rebuilt at all.
    """
    dist = tmp_path / "frontend" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>shell</title>", encoding="utf-8")

    import app.main as main

    original = main.frontend_dir
    main.frontend_dir = lambda: dist  # type: ignore[assignment]
    try:
        harness = build_harness(tmp_path / "app")
        with harness.client() as client:
            for path in ("/", "/m/some-meeting-id"):
                response = client.get(path)
                assert response.status_code == 200, path
                cache = response.headers.get("cache-control", "")
                assert "no-cache" in cache, f"{path} served as {cache!r}"
    finally:
        main.frontend_dir = original  # type: ignore[assignment]


def test_a_short_recording_can_be_kept_after_all(api) -> None:  # type: ignore[no-untyped-def]
    """The way back from a discard, which the state machine allowed and nothing offered.

    A recording under `audio.min_meeting_s` is filed as DISCARDED rather than
    transcribed — the detector wakes on a notification chime often enough that
    without that rule the library fills with eight-second meetings. Nothing is
    deleted: the audio stays and DISCARDED -> RECORDED has always been a legal
    transition. But no endpoint exposed it, so in practice a two-minute
    conversation was unreachable, which is indistinguishable from losing it.
    """
    with serve(api) as client:
        started = client.post("/api/recording/start")
        assert started.status_code == 200, started.text
        meeting_id = started.json()["meeting_id"]

        stopped = client.post("/api/recording/stop")
        assert stopped.status_code == 200, stopped.text
        # Nothing ran for two minutes, so it is filed rather than transcribed.
        assert stopped.json()["state"] == MeetingState.DISCARDED

        kept = client.post(f"/api/meetings/{meeting_id}/keep")
        assert kept.status_code == 200, kept.text
        assert kept.json()["state"] == MeetingState.RECORDED

        detail = client.get(f"/api/meetings/{meeting_id}").json()
        assert detail["state"] == MeetingState.RECORDED
        # And it is queued, or "keep" would mean "keep it and do nothing with it".
        assert any(job["stage"] == "transcribe" for job in detail["jobs"])


def test_keeping_a_meeting_that_was_never_discarded_is_refused(api) -> None:  # type: ignore[no-untyped-def]
    """A no-op that silently succeeds hides the case where it should not have run."""
    with serve(api) as client:
        started = client.post("/api/recording/start")
        meeting_id = started.json()["meeting_id"]

        refused = client.post(f"/api/meetings/{meeting_id}/keep")
        assert refused.status_code == 409
        assert "discarded" in refused.json()["detail"]
