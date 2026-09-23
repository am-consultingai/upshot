"""The Settings screen's microphone picker and its live meter.

The meter is SSE, so the level tests run against a real socket: ``TestClient`` cannot
read an unbounded stream (DECISIONS.md D20).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tests.fixtures.api import build_harness, serve


@pytest.fixture
def api(tmp_path: Path, app_home: Path):  # type: ignore[no-untyped-def]
    return build_harness(tmp_path)


def test_devices_never_fails_without_an_audio_stack(api) -> None:  # type: ignore[no-untyped-def]
    """Settings must open on a machine with no WASAPI at all — CI, WSL, a headless box."""
    body = api.client().get("/api/audio/devices").json()
    assert isinstance(body["devices"], list)
    assert all("name" in device for device in body["devices"])
    assert body["selected"] is None


def _read_levels(client, wanted: int = 4, track: str = "me") -> list[dict]:  # type: ignore[no-untyped-def]
    events: list[dict] = []
    with client.stream("GET", f"/api/audio/level?track={track}") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))
            if len(events) >= wanted:
                break
    return events


def test_meter_reports_signal_from_a_live_source(tmp_path: Path, app_home: Path) -> None:
    harness = build_harness(
        tmp_path, audio__synthetic_pattern="tone", audio__synthetic_realtime=False
    )
    with serve(harness) as client:
        events = _read_levels(client)
    assert events, "the meter produced no readings"
    assert any(event.get("peak", 0.0) > 0.05 for event in events), events
    assert all(event.get("source") == "monitor" for event in events)


def test_meter_reports_silence_as_silence(tmp_path: Path, app_home: Path) -> None:
    """The whole point of the feature: a dead microphone has to look dead."""
    harness = build_harness(
        tmp_path, audio__synthetic_pattern="silence", audio__synthetic_realtime=False
    )
    with serve(harness) as client:
        events = _read_levels(client)
    assert events
    assert all(event.get("peak", 0.0) == 0.0 for event in events), events


def test_selected_device_round_trips(api) -> None:  # type: ignore[no-untyped-def]
    client = api.client()
    client.put("/api/settings", json={"values": {"audio.input_device": 3}})
    assert client.get("/api/audio/devices").json()["selected"] == 3
    assert api.services.config.get("audio.input_device") == 3


def test_open_sse_stream_cannot_block_shutdown(tmp_path: Path, app_home: Path) -> None:
    """A held-open SSE stream used to make the process release its port and then live
    forever, so a stale instance shadowed later ones. Shutdown must be bounded."""
    import threading
    import time

    import httpx

    from app.server import LocalServer

    harness = build_harness(tmp_path)
    port = harness.services.config.server_port
    server = LocalServer(harness.app, host="127.0.0.1", port=port).start()
    opened = threading.Event()

    def hold_a_stream() -> None:
        token = harness.services.auth.issue_token()
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30.0) as client:
                client.get("/", params={"k": token})
                with client.stream("GET", "/api/events") as response:
                    for _ in response.iter_lines():
                        opened.set()
                        time.sleep(30)  # never finishes on its own
        except Exception:
            # Being cut off is the whole point of the test. Left to propagate, this
            # surfaces as PytestUnhandledThreadExceptionWarning attributed to whichever
            # test happens to be running when the thread dies — an intermittent failure
            # that appears to wander between unrelated tests.
            pass

    reader = threading.Thread(target=hold_a_stream, daemon=True)
    reader.start()
    assert opened.wait(10), "the SSE stream never opened"

    started = time.monotonic()
    server.stop(timeout=15)
    elapsed = time.monotonic() - started
    assert elapsed < 12, f"shutdown took {elapsed:.1f}s with an SSE stream open"
    assert server._thread is None
    # Join before returning, so the thread cannot outlive this test either way.
    reader.join(timeout=5)


def test_recording_takes_the_microphone_back_from_the_meter(api) -> None:  # type: ignore[no-untyped-def]
    """WASAPI allows one capture stream per endpoint. The Settings meter held the
    microphone open, so pressing Record failed with -9999 on a real machine."""
    from app.audio import monitor as meter

    client = api.client()
    meter.acquire(api.services.config, None, "me")
    meter.acquire(api.services.config, None, "them")
    assert meter.active("me") is not None and meter.active("them") is not None

    response = client.post("/api/recording/start", json={})
    assert response.status_code == 200, response.text
    # Both endpoints, not just the microphone: the recorder needs the loopback too.
    assert meter.active("me") is None, "the preview stream was still holding the microphone"
    assert meter.active("them") is None, "the preview stream was still holding the loopback"
    client.post("/api/recording/stop")


def test_meters_on_one_device_share_one_stream(api) -> None:  # type: ignore[no-untyped-def]
    """Two tabs on the setup screen must not open two streams — nor close each other's.

    Each used to replace the other's stream as it opened its own, and each SSE client
    then reopened when it found its stream gone: 477 opens in 22 s on machine B (job
    013), and no level ever reached either meter.
    """
    from app.audio import monitor as meter

    opens = meter.acquisitions()
    first = meter.acquire(api.services.config, None)
    second = meter.acquire(api.services.config, None)
    assert first is second
    assert meter.acquisitions() == opens + 1
    meter.release("me", first)
    assert meter.active() is second, "one tab closing must not close the other's meter"
    meter.release("me", second)
    assert meter.active() is None, "the last meter to go closes the stream"


def test_a_meter_on_another_device_replaces_the_stream(api) -> None:  # type: ignore[no-untyped-def]
    from app.audio import monitor as meter

    first = meter.acquire(api.services.config, None)
    second = meter.acquire(api.services.config, 7)
    assert first is not second
    assert meter.active() is second
    meter.release("me", first)  # already replaced: nothing of the first tab's is left
    assert meter.active() is second
    meter.release()
    assert meter.active() is None


# ------------------------------------------------------- playback track selection


def test_meeting_reports_which_tracks_have_sound(api, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """The player was hardwired to "them". A solo recording is silent there, so a
    perfectly good meeting looked like a broken player."""
    import wave

    import numpy as np

    meeting = api.services.meetings.create(source="manual")
    audio = meeting.path / "audio"
    audio.mkdir(parents=True, exist_ok=True)
    rate = 16000
    tone = (np.sin(2 * np.pi * 440 * np.arange(rate) / rate) * 9000).astype(np.int16)
    for name, samples in (("me", tone), ("them", np.zeros(rate, dtype=np.int16))):
        with wave.open(str(audio / f"{name}.wav"), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(rate)
            handle.writeframes(samples.tobytes())

    body = api.client().get(f"/api/meetings/{meeting.id}").json()
    tracks = body["audio_tracks"]
    assert set(tracks) == {"me", "them"}
    assert tracks["me"]["peak"] > 0.2, tracks
    assert tracks["them"]["peak"] == 0.0, "silence must be reported as silence"
    assert tracks["me"]["seconds"] == 1.0


def test_both_tracks_are_playable(api) -> None:  # type: ignore[no-untyped-def]
    import wave

    meeting = api.services.meetings.create(source="manual")
    audio = meeting.path / "audio"
    audio.mkdir(parents=True, exist_ok=True)
    for name in ("me", "them"):
        with wave.open(str(audio / f"{name}.wav"), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"\x01\x02" * 8000)

    client = api.client()
    for name in ("me", "them"):
        response = client.get(f"/api/meetings/{meeting.id}/audio?track={name}")
        assert response.status_code == 200, name
        assert len(response.content) == 44 + 16000


def test_the_meter_says_when_it_stops_on_purpose(tmp_path: Path, app_home: Path) -> None:
    """Without a stop signal the browser's EventSource just reconnects, so a server-side
    timeout achieved nothing except reopening the microphone every few minutes."""
    import app.api.routes as routes

    harness = build_harness(
        tmp_path, audio__synthetic_pattern="tone", audio__synthetic_realtime=False
    )
    original = routes.LEVEL_MAX_S
    routes.LEVEL_MAX_S = 0.3  # reach the backstop quickly
    try:
        with serve(harness) as client:
            events = []
            with client.stream("GET", "/api/audio/level") as response:
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        events.append(json.loads(line[len("data: ") :]))
                    if events and events[-1].get("done"):
                        break
    finally:
        routes.LEVEL_MAX_S = original

    assert events[-1] == {"done": True}, events[-3:]
    assert any("rms" in event for event in events), "it should have metered before stopping"


def test_the_meter_releases_the_device_when_the_client_goes_away(
    tmp_path: Path, app_home: Path
) -> None:
    """Closing the stream must free the microphone, which is what the browser now does
    when the Settings tab is hidden."""
    from app.audio import monitor as meter

    harness = build_harness(
        tmp_path, audio__synthetic_pattern="tone", audio__synthetic_realtime=False
    )
    # Nested on purpose: the stream must close while the server is still running, so the
    # release can be observed. A single combined `with` would tear both down at once.
    with serve(harness) as client:  # noqa: SIM117
        with client.stream("GET", "/api/audio/level") as response:
            for line in response.iter_lines():
                if line.startswith("data: "):
                    break
            assert meter.active() is not None, "the meter should hold the device while streaming"
    deadline = time.monotonic() + 5
    while meter.active() is not None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert meter.active() is None, "the device was not released when the client disconnected"


# ------------------------------------------------------- the system-audio meter


def test_both_tracks_can_be_metered_at_once(tmp_path: Path, app_home: Path) -> None:
    """The microphone and the loopback are different endpoints, so Settings can show a
    meter for each. Seeing both move while only one side talks is the quickest way to
    spot a microphone that is capturing system audio."""
    from app.audio import monitor as meter

    harness = build_harness(
        tmp_path, audio__synthetic_pattern="tone", audio__synthetic_realtime=False
    )
    with serve(harness) as client:
        mic = meter.acquire(harness.services.config, None, "me")
        loop = meter.acquire(harness.services.config, None, "them")
        assert meter.active("me") is mic
        assert meter.active("them") is loop
        assert mic is not loop

        events = _read_levels(client, track="them")
        assert events and any(event.get("peak", 0.0) > 0.05 for event in events), events
    meter.release()


def test_the_meter_leaves_an_armed_recorder_alone(tmp_path: Path, app_home: Path) -> None:
    """A woken detector holds both endpoints for the pre-roll before anything records.
    A meter that reopened them there fought the recorder for them (job 013)."""
    from app.audio import monitor as meter

    harness = build_harness(
        tmp_path, audio__synthetic_pattern="tone", audio__synthetic_realtime=False
    )
    recorder = harness.services.recorder
    assert recorder is not None
    recorder.arm()
    opens = meter.acquisitions()
    try:
        with serve(harness) as client:
            events = _read_levels(client)
        assert events and all(event.get("source") == "recorder" for event in events), events
        assert meter.acquisitions() == opens, "no preview stream while the recorder is armed"
    finally:
        recorder.discard()


def test_an_unknown_track_is_rejected(api) -> None:  # type: ignore[no-untyped-def]
    assert api.client().get("/api/audio/level?track=elsewhere").status_code == 404


# ------------------------------------------------------- deleting a meeting


def test_delete_removes_the_folder_and_the_row(api, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    meeting = api.services.meetings.create(source="manual")
    audio = meeting.path / "audio"
    audio.mkdir(parents=True, exist_ok=True)
    (audio / "me.wav").write_bytes(b"RIFF" + b"\x00" * 100)
    api.services.queue.enqueue(meeting.id, "transcribe")
    client = api.client()

    assert client.get(f"/api/meetings/{meeting.id}").status_code == 200
    response = client.delete(f"/api/meetings/{meeting.id}")
    assert response.status_code == 200, response.text

    assert not meeting.path.exists(), "the folder should be gone from disk"
    assert client.get(f"/api/meetings/{meeting.id}").status_code == 404
    assert api.services.queue.for_meeting(meeting.id) == [], "jobs should cascade away"


def test_delete_refuses_a_folder_outside_the_data_root(api, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """A folder path is data. It must never be able to point the delete at, say, home."""
    outside = tmp_path / "not-ours"
    outside.mkdir()
    (outside / "keep.txt").write_text("important")
    meeting = api.services.dao.insert_meeting(
        meeting_id="escape",
        folder=outside,
        source="manual",
        started_at="2026-09-03T10:00:00+03:00",
    )
    response = api.client().delete(f"/api/meetings/{meeting.id}")
    assert response.status_code == 400
    assert outside.exists() and (outside / "keep.txt").exists()


def test_delete_refuses_while_that_meeting_is_recording(api) -> None:  # type: ignore[no-untyped-def]
    client = api.client()
    started = client.post("/api/recording/start", json={}).json()
    try:
        response = client.delete(f"/api/meetings/{started['meeting_id']}")
        assert response.status_code == 409
    finally:
        client.post("/api/recording/stop")


def test_devices_lists_playback_endpoints_too(api) -> None:  # type: ignore[no-untyped-def]
    body = api.client().get("/api/audio/devices").json()
    assert "outputs" in body and isinstance(body["outputs"], list)
    assert body["selected_output"] is None
    api.client().put("/api/settings", json={"values": {"audio.output_device": 7}})
    assert api.client().get("/api/audio/devices").json()["selected_output"] == 7


# ------------------------------------------------------- the recording waveform


def test_the_waveform_stream_follows_the_recording_and_never_opens_a_device(
    tmp_path: Path, app_home: Path
) -> None:
    """The waveform reads the recorder's levels for both tracks, and ends with the
    recording. Falling back to a preview stream, as the Settings meter does, would take
    the microphone the moment Stop was pressed."""
    from app.audio import monitor as meter

    harness = build_harness(
        tmp_path, audio__synthetic_pattern="tone", audio__synthetic_realtime=False
    )
    with serve(harness) as client:
        idle: list[dict] = []
        with client.stream("GET", "/api/recording/levels") as response:
            for line in response.iter_lines():
                if line.startswith("data: "):
                    idle.append(json.loads(line[len("data: ") :]))
                    break
        assert idle == [{"done": True}], "nothing is recording, so nothing to stream"

        assert client.post("/api/recording/start", json={}).status_code == 200
        events: list[dict] = []
        try:
            with client.stream("GET", "/api/recording/levels") as response:
                for line in response.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    events.append(json.loads(line[len("data: ") :]))
                    if len(events) == 3:
                        client.post("/api/recording/stop")
                    if events[-1].get("done"):
                        break
        finally:
            if harness.services.recorder.committed:
                client.post("/api/recording/stop")

    assert events[-1] == {"done": True}, events[-3:]
    readings = events[:-1]
    assert len(readings) >= 3
    assert all({"me", "them", "paused"} <= set(event) for event in readings), readings
    assert meter.active("me") is None and meter.active("them") is None
