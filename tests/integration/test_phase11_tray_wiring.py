from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from app.notify import Button, FakeNotifier
from app.pipeline.states import MeetingState
from app.tray import TrayApp, render_icon
from app.tray_state import Action, IconColor, RecorderState
from tests.fixtures.api import build_harness


def test_tray_reflects_the_recorder(tmp_path: Path, app_home: Path) -> None:
    harness = build_harness(tmp_path)
    tray = TrayApp(harness.services)
    assert tray.spec().color is IconColor.GREY

    tray.dispatch(Action.START)
    assert tray.observe().recorder is RecorderState.RECORDING
    assert tray.spec().color is IconColor.RED
    assert harness.services.recorder is not None
    assert harness.services.recorder.committed is True

    tray.dispatch(Action.PAUSE)
    assert tray.observe().recorder is RecorderState.PAUSED
    assert tray.spec().color is IconColor.AMBER
    tray.dispatch(Action.PAUSE)
    assert tray.observe().recorder is RecorderState.RECORDING

    harness.emit(seconds=130)
    tray.dispatch(Action.STOP)
    assert tray.observe().recorder is RecorderState.IDLE
    meetings = harness.services.dao.list_meetings()
    assert meetings and meetings[0].state in (MeetingState.RECORDED, MeetingState.DISCARDED)


def test_open_dashboard_hands_every_click_a_fresh_link(
    tmp_path: Path, app_home: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """The startup link is spent by the first browser. Opening from the tray in another
    browser profile must still work, so each click brings its own one-time link."""
    import webbrowser

    from fastapi.testclient import TestClient

    from app.main import create_app

    harness = build_harness(tmp_path)
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", opened.append)
    tray = TrayApp(harness.services)
    tray.dispatch(Action.OPEN)
    tray.dispatch(Action.OPEN)
    assert len(set(opened)) == 2 and all("/?k=" in url for url in opened)

    app = create_app(harness.services)
    for url in opened:
        browser = TestClient(app, base_url=harness.base_url)
        assert browser.get(url.removeprefix(harness.base_url)).status_code == 200


def test_mute_toggle_is_reflected(tmp_path: Path, app_home: Path) -> None:
    harness = build_harness(tmp_path)
    tray = TrayApp(harness.services)
    tray.dispatch(Action.MUTE_HOUR)
    assert tray.observe().detector_muted is True
    assert "muted" in tray.spec().tooltip
    tray.dispatch(Action.MUTE_HOUR)
    assert tray.observe().detector_muted is False


def test_tray_survives_worker_crash(tmp_path: Path, app_home: Path) -> None:
    harness = build_harness(tmp_path)
    tray = TrayApp(harness.services)
    worker = harness.services.worker
    assert worker is not None
    worker.start()
    time.sleep(0.05)
    worker.stop()  # a clean stop leaves no thread and no badge
    assert tray.spec().badge is False

    # simulate a crashed (dead) thread rather than a clean stop
    import threading

    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()
    worker._thread = dead
    spec = tray.spec()
    assert spec.badge is True
    assert "worker stopped" in spec.tooltip
    # the recorder is unaffected
    tray.dispatch(Action.START)
    assert tray.observe().recorder is RecorderState.RECORDING
    tray.dispatch(Action.STOP)


def test_a_discarded_meeting_says_so_on_disk(tmp_path: Path, app_home: Path) -> None:
    """Too short to keep: the folder's meta.json agrees with the database (machine B, job 014)."""
    from app import meta

    harness = build_harness(tmp_path)
    client = harness.client()
    meeting_id = client.post("/api/recording/start", json={}).json()["meeting_id"]
    harness.emit(seconds=1)
    client.post("/api/recording/stop")

    meeting = harness.services.dao.require_meeting(meeting_id)
    assert meeting.state == MeetingState.DISCARDED
    on_disk = meta.read(meeting.path)
    assert on_disk["state"] == MeetingState.DISCARDED
    assert on_disk["ended_at"]


def test_toast_button_posts_to_api(tmp_path: Path, app_home: Path) -> None:
    """Activating 'Not a meeting' takes the same path the UI does, and discards."""
    harness = build_harness(tmp_path)
    client = harness.client()
    calls: list[str] = []

    def on_action(button: Button) -> None:
        calls.append(button.action)
        if button.action == "meeting.discard" and button.meeting_id:
            response = client.patch(f"/api/meetings/{button.meeting_id}", json={"discard": True})
            assert response.status_code == 200

    notifier = FakeNotifier(on_action=on_action)
    harness.services.notifier = notifier
    started = client.post("/api/recording/start", json={}).json()
    meeting_id = started["meeting_id"]
    notifier.recording_started(meeting_id, "Weekly Sync")

    notifier.activate("Not a meeting")
    assert calls == ["meeting.discard"]
    assert harness.services.dao.require_meeting(meeting_id).state == MeetingState.DISCARDED
    harness.services.recorder.stop()  # type: ignore[union-attr]


def test_icon_image_renders(tmp_path: Path, app_home: Path) -> None:
    from app.tray_state import AppState, icon_for

    image = render_icon(icon_for(AppState(error=True)))
    assert image.size == (64, 64)
    assert image.mode == "RGBA"


def test_single_instance(tmp_path: Path) -> None:
    """Two entry points, one winner; the second exits 3 and says why."""
    import os

    env = {**os.environ, "UP_HOME": str(tmp_path / "home")}
    first = subprocess.Popen(
        [sys.executable, "-m", "app.instance", "hold", "--seconds", "20"],
        stdout=subprocess.PIPE,
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
    )
    try:
        assert first.stdout is not None
        assert first.stdout.readline().strip() == "acquired"

        second = subprocess.run(
            [sys.executable, "-m", "app.instance", "hold", "--seconds", "5"],
            capture_output=True,
            env=env,
            text=True,
            timeout=30,
        )
        assert second.returncode == 3
        assert "already running" in second.stdout

        assert first.poll() is None, "the first instance is unaffected"
    finally:
        if first.stdin is not None:
            first.stdin.close()
        first.wait(timeout=30)
    assert first.returncode == 0

    # once the first exits, a new instance may start
    third = subprocess.run(
        [sys.executable, "-m", "app.instance", "hold", "--seconds", "0.1"],
        capture_output=True,
        env=env,
        text=True,
        timeout=30,
    )
    assert third.returncode == 0
