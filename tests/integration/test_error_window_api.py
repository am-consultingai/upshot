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
