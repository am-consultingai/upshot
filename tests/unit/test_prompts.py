"""The one offer to record, shared by the banner and the toasts (app/prompts.py, D76)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.prompts import Prompt, Prompts

NOW = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)


def meeting(**overrides: object) -> Prompt:
    fields: dict[str, object] = {
        "kind": "calendar",
        "title": "Test",
        "at": NOW,
        "calendar_id": "primary",
        "event_id": "e1",
        "until": NOW + timedelta(minutes=30),
    }
    fields.update(overrides)
    return Prompt(**fields)  # type: ignore[arg-type]


def test_a_detected_offer_carries_its_score_and_evidence() -> None:
    """The banner says why it thinks a call is on (app, score, evidence), not only that."""
    prompts = Prompts()
    prompts.offer(
        meeting(
            kind="detected",
            until=None,
            process="Zoom.exe",
            score=7,
            evidence=(("mic.known_app", "Zoom.exe"), ("vad.loopback", "someone else is speaking")),
        ),
        recording=False,
    )
    offer = prompts.snapshot()
    assert offer is not None
    assert offer["score"] == 7
    assert offer["evidence"] == [
        {"code": "mic.known_app", "detail": "Zoom.exe"},
        {"code": "vad.loopback", "detail": "someone else is speaking"},
    ]


def test_a_calendar_offer_has_no_score() -> None:
    prompts = Prompts()
    prompts.offer(meeting(), recording=False)
    offer = prompts.snapshot()
    assert offer is not None
    assert offer["score"] is None
    assert offer["evidence"] == []


def test_nothing_is_offered_while_recording() -> None:
    prompts = Prompts()
    assert prompts.offer(meeting(), recording=True) is False
    assert prompts.snapshot() is None


def test_a_recording_withdraws_the_offer() -> None:
    """Machine B: the banner said "not being recorded" while it was."""
    prompts = Prompts()
    assert prompts.offer(meeting(), recording=False)
    prompts.tick(NOW, recording=True, holders=[])
    assert prompts.snapshot() is None


def test_the_offer_lapses_when_the_meeting_is_over() -> None:
    """Machine B: the banner outlived the meeting."""
    prompts = Prompts()
    prompts.offer(meeting(), recording=False)
    prompts.tick(NOW + timedelta(minutes=29), recording=False, holders=[])
    assert prompts.snapshot() is not None
    prompts.tick(NOW + timedelta(minutes=30), recording=False, holders=[])
    assert prompts.snapshot() is None


def test_a_detected_call_is_withdrawn_when_its_app_lets_go() -> None:
    prompts = Prompts()
    prompts.offer(meeting(kind="detected", until=None, process="Zoom.exe"), recording=False)
    prompts.tick(NOW, recording=False, holders=["Zoom.exe"])
    assert prompts.snapshot() is not None
    prompts.tick(NOW, recording=False, holders=[])
    assert prompts.snapshot() is None


def test_not_a_meeting_is_said_once_per_meeting() -> None:
    prompts = Prompts()
    prompts.offer(meeting(), recording=False)
    prompts.dismiss()
    assert prompts.snapshot() is None
    assert prompts.offer(meeting(), recording=False) is False, "not offered again"
    assert prompts.offer(meeting(event_id="e2"), recording=False), "another meeting is"


def test_not_a_meeting_on_a_call_lasts_until_its_app_lets_go() -> None:
    """Keyed to the app, a dismissal used to mute that app's calls until a restart."""
    prompts = Prompts()
    call = meeting(
        kind="detected", until=None, calendar_id=None, event_id=None, process="chrome.exe"
    )
    prompts.offer(call, recording=False)
    prompts.dismiss()
    prompts.tick(NOW, recording=False, holders=["chrome.exe"])
    assert prompts.offer(call, recording=False) is False, "the same call, still dismissed"
    prompts.tick(NOW, recording=False, holders=[])
    assert prompts.offer(call, recording=False), "a new call from the same app is offered"
