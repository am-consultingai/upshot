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
