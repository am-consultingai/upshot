"""``/api/feedback``: previewed, sent as previewed, and a summary rating kept (D87, D1-D3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import meta
from tests.fixtures.api import build_harness


@pytest.fixture
def api(tmp_path: Path, app_home: Path):  # type: ignore[no-untyped-def]
    return build_harness(tmp_path)


def test_a_source_run_shows_the_preview_but_cannot_send(api) -> None:  # type: ignore[no-untyped-def]
    client = api.client()
    assert client.get("/api/feedback").json() == {"available": False}
    preview = client.post("/api/feedback/preview", json={"kind": "idea", "message": "hello"}).json()
    assert preview["payload"]["contexts"]["feedback"]["message"] == "hello"
    refused = client.post("/api/feedback", json={"kind": "idea", "message": "hello"})
    assert refused.status_code == 422 and "from source" in refused.json()["detail"]


def test_sent_is_what_was_previewed_with_the_screenshot_the_user_saw(api) -> None:  # type: ignore[no-untyped-def]
    sent: list[tuple[str, dict, list]] = []
    feedback = api.services.feedback
    feedback.dsn = "https://0123456789abcdef0123456789abcdef@o1.ingest.example.io/42"
    feedback._send = lambda kind, payload, files: sent.append((kind, payload, files)) or True
    api.services.extras["feedback_screenshot"] = b"\x89PNGshot"
    client = api.client()
    body = {"kind": "problem", "message": "the meter froze", "screenshot": True}
    preview = client.post("/api/feedback/preview", json=body).json()
    result = client.post("/api/feedback", json=body).json()
    assert result["sent"] is True and result["reference"].startswith("FB-")
    payload = sent[0][1]
    assert payload["contexts"] == preview["payload"]["contexts"]
    assert [f.data for f in sent[0][2]] == [b"\x89PNGshot"]
    assert "feedback_screenshot" not in api.services.extras, "used once, then gone"


def test_a_bad_email_is_refused_with_a_reason(api) -> None:  # type: ignore[no-untyped-def]
    response = api.client().post(
        "/api/feedback/preview", json={"kind": "idea", "message": "x", "email": "nope"}
    )
    assert response.status_code == 422 and "email" in response.json()["detail"]


def test_a_summary_rating_is_kept_with_the_meeting(api, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    meeting = api.services.dao.insert_meeting(folder=tmp_path / "m1", source="manual")
    folder = Path(meeting.folder)
    folder.mkdir(parents=True, exist_ok=True)
    meta.update(folder, llm={"name": "anthropic"}, summary_language="he")
    (folder / "summary.html").write_text("<p>the summary</p>", encoding="utf-8")
    client = api.client()
    preview = client.post(
        f"/api/feedback/summary/{meeting.id}/preview",
        json={"rating": "down", "include_summary": True},
    ).json()
    assert preview["payload"]["tags"]["provider"] == "anthropic"
    assert preview["attachments"] == [{"filename": "summary.html", "bytes": 18}]
    result = client.post(f"/api/feedback/summary/{meeting.id}", json={"rating": "down"}).json()
    assert result["kept_locally"] is True, "a source run keeps the rating, sends nothing"
    assert client.get(f"/api/feedback/summary/{meeting.id}").json()["rating"]["value"] == "down"
