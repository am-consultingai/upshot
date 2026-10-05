"""Installing a ready update at a safe moment, and the start after it (D87, B3)."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from app.clock import FakeClock, iso
from app.config import Config
from app.db.dao import Dao, connect
from app.pipeline.queue import JobQueue
from app.pipeline.states import JobStage
from app.updates.install import MAX_ATTEMPTS, QUIET_S, UpdateInstaller, meeting_soon
from app.updates.service import UpdateService


class World:
    """An installed copy on 0.2.0 with 0.3.0 downloaded and verified."""

    def __init__(self, tmp_path: Path, *, mandatory: bool = False, auto: bool = True) -> None:
        self.tmp_path = tmp_path
        self.clock = FakeClock()
        self.config = Config.load(file=tmp_path / "app_config.json")
        self.config.set("updates.auto_install", auto)
        self.recording = False
        self.in_call = False
        self.jobs = False
        self.soon = False
        self.window = True
        self.spawned: list[list[str]] = []
        self.quits = 0
        self.updates = self.service("0.2.0")
        self.make_ready("0.3.0", mandatory=mandatory)
        self.installer = self.make_installer()

    def service(self, version: str) -> UpdateService:
        return UpdateService(
            self.config, home=self.tmp_path, clock=self.clock, current_version=version, frozen=True
        )

    def make_ready(self, version: str, *, mandatory: bool = False) -> None:
        home = self.tmp_path / "updates"
        home.mkdir(exist_ok=True)
        name = f"Upshot-{version}-Setup.exe"
        (home / name).write_bytes(b"MZ")
        record = {"version": version, "file": name, "sha256": "0" * 64, "mandatory": mandatory}
        (home / "ready.json").write_text(json.dumps(record), encoding="utf-8")

    def make_installer(self, updates: UpdateService | None = None) -> UpdateInstaller:
        installer = UpdateInstaller(
            self.config,
            updates or self.updates,
            recording=lambda: self.recording,
            in_call=lambda: self.in_call,
            jobs_busy=lambda: self.jobs,
            meeting_soon=lambda: self.soon,
            window_open=lambda: self.window,
            clock=self.clock,
            spawn=self.spawned.append,
            can_install=True,
        )
        installer.quit = self.quit
        return installer

    def quit(self) -> None:
        self.quits += 1

    def marker(self) -> dict[str, Any]:
        path = self.tmp_path / "updates" / "installing.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


# ------------------------------------------------------------------ the safe moment


def test_an_automatic_install_waits_for_ten_quiet_minutes(world: World) -> None:
    assert world.installer.tick() is False, "the first safe tick only starts the wait"
    world.clock.advance(QUIET_S - 1)
    assert world.installer.tick() is False
    world.clock.advance(1)
    assert world.installer.tick() is True
    command = world.spawned[0]
    assert command[0].endswith("Upshot-0.3.0-Setup.exe")
    assert {"/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/UPDATE=1"} <= set(command)
    assert any(arg.startswith("/LOG=") and arg.endswith("install-0.3.0.log") for arg in command)
    assert world.quits == 1, "the app quits so the installer can replace it"


@pytest.mark.parametrize("busy", ["recording", "in_call", "jobs", "soon"])
def test_anything_in_the_way_restarts_the_wait(world: World, busy: str) -> None:
    world.installer.tick()
    world.clock.advance(QUIET_S - 60)
    setattr(world, busy, True)
    assert world.installer.tick() is False
    setattr(world, busy, False)
    world.clock.advance(120)
    assert world.installer.tick() is False, "the quiet ten minutes start again"
    world.clock.advance(QUIET_S)
    assert world.installer.tick() is True


def test_a_mandatory_update_installs_at_the_first_safe_moment_even_when_turned_off(
    tmp_path: Path,
) -> None:
    world = World(tmp_path, mandatory=True, auto=False)
    world.recording = True
    assert world.installer.tick() is False, "never over a recording"
    world.recording = False
    assert world.installer.tick() is True


def test_with_automatic_installs_off_nothing_installs_by_itself(tmp_path: Path) -> None:
    world = World(tmp_path, auto=False)
    world.installer.tick()
    world.clock.advance(QUIET_S * 10)
    assert world.installer.tick() is False
    assert world.installer.at_quit() is False
    assert world.spawned == []


def test_nothing_ready_installs_nothing(tmp_path: Path) -> None:
    world = World(tmp_path)
    (tmp_path / "updates" / "ready.json").unlink()
    world.clock.advance(QUIET_S * 2)
    assert world.installer.tick() is False and world.installer.at_quit() is False


def test_a_copy_that_cannot_install_never_starts_an_installer(world: World) -> None:
    world.installer.can_install = False
    world.installer.tick()
    world.clock.advance(QUIET_S * 2)
    assert world.installer.tick() is False
    with pytest.raises(RuntimeError, match="run from source"):
        world.installer.install_now()
    assert world.spawned == []


# ------------------------------------------------------------------ quitting and asking


def test_quitting_from_the_tray_installs_at_once_and_does_not_quit_twice(world: World) -> None:
    assert world.installer.at_quit() is True
    assert len(world.spawned) == 1 and world.quits == 0


def test_quitting_during_a_meeting_installs_nothing(world: World) -> None:
    world.soon = True
    assert world.installer.at_quit() is False and world.spawned == []


def test_install_now_waits_only_for_a_recording(world: World) -> None:
    world.in_call = world.jobs = world.soon = True
    world.recording = True
    with pytest.raises(RuntimeError, match="recorded"):
        world.installer.install_now()
    world.recording = False
    assert world.installer.install_now() == {"installing": "0.3.0"}
    assert len(world.spawned) == 1


def test_install_now_with_nothing_ready_says_so(world: World) -> None:
    (world.tmp_path / "updates" / "ready.json").unlink()
    with pytest.raises(LookupError):
        world.installer.install_now()


def test_one_install_at_a_time(world: World) -> None:
    assert world.installer.at_quit() is True
    assert world.installer.at_quit() is False
    assert len(world.spawned) == 1


def test_an_installer_that_does_not_start_is_remembered(world: World) -> None:
    def refuse(command: list[str]) -> None:
        raise OSError("blocked by policy")

    world.installer.spawn = refuse
    assert world.installer.at_quit() is False
    assert "blocked by policy" in (world.updates.last_error or "")
    assert world.quits == 0


# ------------------------------------------------------------------ the start after


def test_the_marker_records_what_and_whether_the_window_was_open(world: World) -> None:
    world.window = True
    world.installer.at_quit()
    marker = world.marker()
    assert marker["from"] == "0.2.0" and marker["to"] == "0.3.0"
    assert marker["window_open"] is True and marker["why"] == "quit" and marker["attempts"] == 1


def test_a_start_on_the_new_version_cleans_up_and_says_so(world: World) -> None:
    world.installer.at_quit()
    after = world.make_installer(world.service("0.3.0"))
    outcome = after.after_start()
    assert outcome and outcome["result"] == "updated" and outcome["window_open"] is True
    assert world.config.get("updates.last_installed") == "0.3.0"
    assert world.marker() == {}
    assert not list((world.tmp_path / "updates").glob("Upshot-*"))
    assert after.after_start() is None, "said once"


def test_a_start_on_the_old_version_is_a_failed_install_and_retries_once(world: World) -> None:
    world.installer.at_quit()
    again = world.make_installer(world.service("0.2.0"))
    outcome = again.after_start()
    assert outcome and outcome["result"] == "failed"
    assert "did not install" in (again.updates.last_error or "")
    assert again.at_quit() is True, "a second attempt"
    assert world.marker()["attempts"] == MAX_ATTEMPTS
    third = world.make_installer(world.service("0.2.0"))
    third.after_start()
    assert third.at_quit() is False, "a version that failed twice is not retried alone"
    assert third.install_now() == {"installing": "0.3.0"}, "but the user may still try"


def test_no_marker_means_an_ordinary_start(world: World) -> None:
    assert world.installer.after_start() is None and world.installer.outcome is None


# ------------------------------------------------------------------ the signals


def test_the_job_queue_is_busy_only_with_work_due_now(tmp_path: Path) -> None:
    clock = FakeClock()
    conn = connect(tmp_path / "index.db")
    dao = Dao(conn, clock)
    queue = JobQueue(conn, clock)
    assert queue.busy() is False
    meeting = dao.insert_meeting(folder=tmp_path / "m", source="manual")
    later = iso(clock.now() + timedelta(hours=8))
    queue.enqueue(meeting.id, JobStage.TRANSCRIBE, not_before=later)
    assert queue.busy() is False, "held for the night window"
    clock.advance(9 * 3600)
    assert queue.busy() is True, "now due"
    job = queue.claim_next()
    assert job is not None and queue.busy() is True, "running"
    queue.complete(job)
    assert queue.busy() is False


def test_meeting_soon_asks_the_calendar_twenty_minutes_ahead() -> None:
    clock = FakeClock()
    asked: list[float] = []

    class Calendar:
        def soon(self, now: Any, *, ahead_s: float) -> list[str]:
            asked.append(ahead_s)
            return ["standup"]

    assert meeting_soon(Calendar(), clock) is True and asked == [1200.0]
    assert meeting_soon(None, clock) is False


def test_a_broken_calendar_never_blocks_or_breaks_an_update() -> None:
    class Broken:
        def soon(self, now: Any, *, ahead_s: float) -> list[str]:
            raise RuntimeError("cache locked")

    assert meeting_soon(Broken(), FakeClock()) is False
