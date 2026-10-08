"""Toast buttons reach the running app as upshot: links (D70)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import actions
from app.api.security import ACTION_PATH, LAUNCHER_HEADER
from app.notify import DISMISS, FakeNotifier
from app.pipeline.states import MeetingState
from tests.fixtures.api import build_harness


def test_links_carry_the_action_and_nothing_else() -> None:
    link = actions.url("recording.start", calendar="primary", event="abc 1", meeting=None)
    assert link == "upshot:recording.start?calendar=primary&event=abc+1"
    parsed = actions.parse(link)
    assert parsed == actions.Link("recording.start", {"calendar": "primary", "event": "abc 1"})
    # Windows may add a slash; a foreign scheme, an unknown action or key is refused.
    assert actions.parse("upshot:recording.stop/?meeting=m1") == actions.Link(
        "recording.stop", {"meeting": "m1"}
    )
    assert actions.parse("https://example.com") is None
    assert actions.parse("upshot:format.disk") is None
    assert actions.parse("upshot:open?evil=1") == actions.Link("open", {})
    assert actions.page(actions.Link("meeting.open", {"meeting": "m 1"})) == "/m/m%201"
    assert actions.link_argument(["--x", "UPSHOT:open"]) == "UPSHOT:open"


def test_meeting_toasts_offer_start_and_dismiss() -> None:
    notifier = FakeNotifier()
    notifier.call_detected("Teams.exe", "Weekly sync", calendar_id="primary", event_id="e1")
    toast = notifier.shown[-1]
    assert toast.title == "Meeting started: Weekly sync"
    start, dismiss = toast.buttons
    assert (start.label, dismiss.label) == ("Start recording", "Dismiss")
    assert start.link() == "upshot:recording.start?calendar=primary&event=e1"
    assert dismiss.action == DISMISS and dismiss.link() is None
    assert toast.link() == "upshot:open"

    notifier.meeting_starting("primary:e2", "Design review", calendar_id="primary", event_id="e2")
    assert [b.label for b in notifier.shown[-1].buttons] == ["Start recording", "Dismiss"]

    notifier.recording_started("m1", "Weekly sync")
    started = notifier.shown[-1]
    assert started.title == "Recording started: Weekly sync"
    assert [b.link() for b in started.buttons] == [
        "upshot:recording.stop?meeting=m1",
        "upshot:meeting.discard?meeting=m1",
    ]
    assert started.link() == "upshot:meeting.open?meeting=m1"


def test_no_meeting_toast_while_upshots_window_is_in_front() -> None:
    front = [True]
    notifier = FakeNotifier(app_in_front=lambda: front[0])
    notifier.call_detected("Teams.exe", None)
    notifier.meeting_starting("k", "Design review")
    notifier.recording_started("m1", "x")
    assert notifier.shown == []
    notifier.summary_ready("m1", "x")  # not a meeting notice: shown regardless
    assert len(notifier.shown) == 1
    front[0] = False
    notifier.call_detected("Teams.exe", None)
    assert notifier.shown[-1].title == "Meeting started: Teams"


def test_the_toast_process_gets_every_link() -> None:
    import json

    from app.notify import WindowsToastNotifier

    seen: list[list[str]] = []
    notifier = WindowsToastNotifier(spawn=lambda c: seen.append(c), app_in_front=lambda: False)
    notifier.call_detected("Zoom.exe", "Standup", calendar_id="c", event_id="e")
    payload = json.loads(seen[-1][-1])
    assert payload["launch"] == "upshot:open"
    assert payload["buttons"] == [
        {
            "label": "Start recording",
            "action": "recording.start",
            "launch": "upshot:recording.start?calendar=c&event=e",
        },
        {"label": "Dismiss", "action": DISMISS, "launch": None},
    ]


def _launcher(harness):  # type: ignore[no-untyped-def]
    client = harness.client(authorized=False)
    client.headers[LAUNCHER_HEADER] = harness.services.auth.launcher_key
    return client


def test_a_toast_button_needs_the_launcher_key(tmp_path: Path, app_home: Path) -> None:
    harness = build_harness(tmp_path)
    stranger = harness.client(authorized=False)
    response = stranger.post(ACTION_PATH, json={"action": "recording.start"})
    assert response.status_code == 403  # no CSRF token and no key
    stranger.headers[LAUNCHER_HEADER] = "wrong"
    assert stranger.post(ACTION_PATH, json={"action": "recording.start"}).status_code == 403
    # The key opens this one path, not the rest of the API.
    launcher = _launcher(harness)
    assert launcher.post("/api/recording/start", json={}).status_code == 403


def test_start_stop_and_not_a_meeting_from_toasts(tmp_path: Path, app_home: Path) -> None:
    harness = build_harness(tmp_path)
    launcher = _launcher(harness)
    started = launcher.post(ACTION_PATH, json={"action": "recording.start"})
    assert started.status_code == 200, started.text
    meeting_id = started.json()["meeting_id"]
    assert harness.services.recorder is not None and harness.services.recorder.committed

    # A stale Stop (a toast about another meeting) must not stop this one.
    stale = launcher.post(ACTION_PATH, json={"action": "recording.stop", "meeting_id": "old"})
    assert stale.status_code == 409
    harness.emit(seconds=1)
    discard = launcher.post(
        ACTION_PATH, json={"action": "meeting.discard", "meeting_id": meeting_id}
    )
    assert discard.status_code == 200, discard.text
    assert not harness.services.recorder.committed
    assert harness.services.dao.require_meeting(meeting_id).state == MeetingState.DISCARDED

    again = launcher.post(ACTION_PATH, json={"action": "recording.start"}).json()["meeting_id"]
    harness.emit(seconds=1)
    stop = launcher.post(ACTION_PATH, json={"action": "recording.stop", "meeting_id": again})
    assert stop.status_code == 200, stop.text
    assert not harness.services.recorder.committed


def test_a_link_opens_pages_itself_and_hands_the_rest_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import instance, tray, window

    opened: list[str] = []
    forwarded: list[actions.Link] = []
    monkeypatch.setattr(window, "open_window", lambda url, **_: opened.append(url) or "app")
    monkeypatch.setattr(actions, "forward", lambda link, port, home: forwarded.append(link) or True)

    # Not running: a page link starts Upshot normally, an action has nothing to go to.
    assert tray.run_link("upshot:open", tmp_path) is None
    assert tray.run_link("upshot:recording.start", tmp_path) == 1
    assert tray.run_link("upshot:nonsense", tmp_path) == 2

    instance.record_port(8012, tmp_path)
    monkeypatch.setattr(
        instance, "fresh_link", lambda port, home, path="/": f"http://127.0.0.1:{port}{path}?k=t"
    )
    assert tray.run_link("upshot:meeting.open?meeting=m1", tmp_path) == 0
    assert opened == ["http://127.0.0.1:8012/m/m1?k=t"]
    assert tray.run_link("upshot:recording.stop?meeting=m1", tmp_path) == 0
    assert forwarded == [actions.Link("recording.stop", {"meeting": "m1"})]
    assert len(opened) == 1, "an action opens no window"


def test_only_the_installed_app_sends_toasts_under_its_own_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D72: Windows ignores link buttons under the fallback identity, and never shows a
    toast under an unregistered one, which only the installer registers."""
    import json
    import sys

    from app.notify import WindowsToastNotifier

    seen: list[list[str]] = []
    notifier = WindowsToastNotifier(spawn=lambda c: seen.append(c), app_in_front=lambda: False)
    notifier.call_detected("Zoom.exe", "Standup")
    assert json.loads(seen[-1][-1])["aumid"] is None
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    notifier.call_detected("Teams.exe", "Standup")
    assert json.loads(seen[-1][-1])["aumid"] == "Upshot.App"


def test_a_start_that_cannot_record_says_why_and_leaves_nothing(
    tmp_path: Path, app_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No microphone (machine B over Remote Desktop, D72): a 409 with a reason, no
    meeting left behind, and from a notification, a notification saying why."""
    from app.audio.devices import NoDeviceError

    harness = build_harness(tmp_path)
    notifier = FakeNotifier()
    harness.services.notifier = notifier
    recorder = harness.services.recorder
    assert recorder is not None

    def no_microphone(*_args: object, **_kwargs: object) -> None:
        raise NoDeviceError("no default capture endpoint (no microphone)")

    monkeypatch.setattr(recorder, "start", no_microphone)
    response = _launcher(harness).post(ACTION_PATH, json={"action": "recording.start"})
    assert response.status_code == 409
    assert "No microphone" in response.json()["detail"]
    assert all(m.state == MeetingState.DISCARDED for m in harness.services.dao.list_meetings())
    assert notifier.titles()[-1] == "Upshot couldn't start recording"
    assert "No microphone" in notifier.shown[-1].body

    # From the window the error shows there, so no notification is added.
    before = len(notifier.shown)
    assert harness.client().post("/api/recording/start", json={}).status_code == 409
    assert len(notifier.shown) == before


def test_stop_ends_a_meeting_left_recording_by_an_earlier_run(
    tmp_path: Path, app_home: Path
) -> None:
    """Machine B, D76: a meeting stuck at "recording 615:48" whose Stop answered 409."""
    harness = build_harness(tmp_path)
    services = harness.services
    ghost = services.meetings.create(source="manual")
    assert services.dao.require_meeting(ghost.id).state == MeetingState.RECORDING
    stopped = harness.client().post("/api/recording/stop")
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["repaired"] == [ghost.id]
    # No audio was ever written, so there is nothing to keep.
    assert services.dao.require_meeting(ghost.id).state == MeetingState.DISCARDED
    assert harness.client().post("/api/recording/stop").status_code == 409


def test_the_calendar_test_hook_is_off_unless_the_machine_turns_it_on(
    tmp_path: Path, app_home: Path
) -> None:
    harness = build_harness(tmp_path)
    launcher = _launcher(harness)
    event = {
        "id": "t1",
        "title": "Harness meeting",
        "start": "2026-09-28T09:00:00+00:00",
        "end": "2026-09-28T09:30:00+00:00",
    }
    assert launcher.post("/api/launcher/test/calendar", json={"events": [event]}).status_code == 404
    harness.services.config.set("testing.hooks", True)
    stranger = harness.client(authorized=False)
    assert stranger.post("/api/launcher/test/calendar", json={"events": []}).status_code == 403
    no_account = launcher.post("/api/launcher/test/calendar", json={"events": [event]})
    assert no_account.status_code == 409, "the events go under a connected account"
    from tests.fixtures.api import seed_calendar_account

    account = seed_calendar_account(harness.services)
    answer = launcher.post("/api/launcher/test/calendar", json={"events": [event]})
    assert answer.status_code == 200, answer.text
    from app.gcal.events import EventStore

    stored = EventStore(harness.services.conn).get(account, "upshot-test", "t1")
    assert stored is not None and stored.title == "Harness meeting"


def test_a_start_that_names_no_meeting_records_the_one_on_offer(
    tmp_path: Path, app_home: Path
) -> None:
    """Machine B: a call started from its toast was filed as "meeting"."""
    from datetime import UTC, datetime

    from app.prompts import Prompt

    harness = build_harness(tmp_path)
    harness.services.prompts.offer(
        Prompt(
            kind="detected", title="Meet - Weekly sync", at=datetime.now(UTC), process="chrome.exe"
        ),
        recording=False,
    )
    started = _launcher(harness).post(ACTION_PATH, json={"action": "recording.start"})
    assert started.status_code == 200, started.text
    meeting = harness.services.dao.require_meeting(started.json()["meeting_id"])
    assert meeting.title == "Meet - Weekly sync"
    assert harness.services.prompts.snapshot() is None
    harness.services.recorder.stop()  # type: ignore[union-attr]


def test_the_log_tells_join_and_record_from_start_recording() -> None:
    """Machine B: both logged as "recording.start (meeting -)", so a Join and record
    press was read as a plain Start."""
    join = actions.parse(
        "upshot:recording.start?calendar=primary&event=e1&join=https://meet.google.com/abc-defg"
    )
    assert join is not None
    assert actions.describe(join) == (
        "recording.start (calendar primary/e1, join meet.google.com)"
    ), "the host only: the rest of a meeting link can be its key"
    plain = actions.parse("upshot:recording.start")
    assert plain is not None and actions.describe(plain) == "recording.start"
