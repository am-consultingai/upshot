"""``/api/diagnostics/errors``: the error window's notices, and closing one (owner,
2026-11-10)."""

from __future__ import annotations

from pathlib import Path

from app.diagnostics import notices
from tests.fixtures.api import build_harness


def test_lists_open_notices_with_the_meeting_title_and_closes_them(tmp_path: Path) -> None:
    api = build_harness(tmp_path)
    meeting = api.services.meetings.create(source="manual", title="Tableau")
    notices.record(
        meeting_id=meeting.id, stage="summarize", message="boom", at="now",
        exc=RuntimeError("boom"), sent=None,
    )  # fmt: skip
    client = api.client()
    [item] = client.get("/api/diagnostics/errors").json()["items"]
    assert item["title"] == "Tableau" and item["stage"] == "summarize"

    closed = client.post(f"/api/diagnostics/errors/{item['id']}/close", json={"send": False})
    assert closed.status_code == 200 and "event" not in closed.json()["report"]
    assert client.get("/api/diagnostics/errors").json()["items"] == []
    gone = client.post(f"/api/diagnostics/errors/{item['id']}/close", json={"send": False})
    assert gone.status_code == 404


def test_a_deleted_meeting_has_no_window(tmp_path: Path) -> None:
    api = build_harness(tmp_path)
    notices.record(
        meeting_id="not-a-meeting", stage="summarize", message="boom", at="now",
        exc=RuntimeError("boom"), sent=None,
    )  # fmt: skip
    assert api.client().get("/api/diagnostics/errors").json()["items"] == []


def test_deleting_a_failed_meeting_removes_its_error_text_from_disk(tmp_path: Path) -> None:
    """The notice keeps the job's error, which can quote a CLI's output: it goes with the
    meeting, whichever way the meeting is deleted."""
    api = build_harness(tmp_path)
    meeting = api.services.meetings.create(source="manual", title="Tableau")
    meeting.path.mkdir(parents=True, exist_ok=True)
    notices.record(
        meeting_id=meeting.id, stage="summarize", message="Claude Code exited 1: SECRET",
        at="now", exc=RuntimeError("boom"), sent=None,
    )  # fmt: skip
    deleted = api.client().delete(f"/api/meetings/{meeting.id}")
    assert deleted.status_code == 200, deleted.text
    assert notices.pending() == []
    assert "SECRET" not in notices._path().read_text(encoding="utf-8")


def test_the_list_says_how_many_meetings_the_same_error_stopped(tmp_path: Path) -> None:
    api = build_harness(tmp_path)
    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        error = exc
    for title in ("One", "Two", "Three"):
        meeting = api.services.meetings.create(source="manual", title=title)
        notices.record(
            meeting_id=meeting.id, stage="summarize", message="boom", at="now",
            exc=error, sent=None,
        )  # fmt: skip
    items = api.client().get("/api/diagnostics/errors").json()["items"]
    assert [item["same_error"] for item in items] == [2, 2, 2]
