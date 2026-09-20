"""The inbox's API, and the search that returns sentences rather than titles."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db.dao import Turn
from app.pipeline.states import MeetingState
from tests.fixtures.api import build_harness


@pytest.fixture
def api(tmp_path: Path, app_home: Path):  # type: ignore[no-untyped-def]
    return build_harness(tmp_path)


def _meeting(api, meeting_id: str, title: str, started_at: str):  # type: ignore[no-untyped-def]
    return api.services.dao.insert_meeting(
        meeting_id=meeting_id,
        folder=api.services.config.data_root / meeting_id,
        source="manual",
        state=MeetingState.RECORDING,
        profile="cpu-deferred",
        title=title,
        started_at=started_at,
    )


# ------------------------------------------------------------------ action items


def test_inbox_reads_across_meetings(api) -> None:  # type: ignore[no-untyped-def]
    _meeting(api, "m-a", "Onboarding funnel review", "2026-09-19T09:30:00Z")
    _meeting(api, "m-b", "Q4 roadmap", "2026-09-20T10:00:00Z")
    api.services.dao.replace_action_items(
        "m-a", [("Dana", "write the instrumentation spec", "Thursday", None)]
    )
    api.services.dao.replace_action_items("m-b", [("ME", "book the review", None, 4200)])

    body = api.client().get("/api/action-items").json()
    assert body["count"] == 2
    assert body["open"] == 2
    # Mine first, whichever meeting it came from.
    assert [item["what"] for item in body["items"]] == [
        "book the review",
        "write the instrumentation spec",
    ]
    first = body["items"][0]
    assert first["mine"] is True
    assert first["meeting_title"] == "Q4 roadmap"
    assert first["at_ms"] == 4200
    assert first["done"] is False


def test_ticking_an_item_persists_and_filters(api) -> None:  # type: ignore[no-untyped-def]
    _meeting(api, "m-a", "Standup", "2026-09-20T11:00:00Z")
    api.services.dao.replace_action_items(
        "m-a",
        [("ME", "unblock the pricing copy", None, None), ("Yoni", "pull numbers", None, None)],
    )
    client = api.client()
    item_id = client.get("/api/action-items").json()["items"][0]["id"]

    patched = client.patch(f"/api/action-items/{item_id}", json={"done": True}).json()
    assert patched["done"] is True
    assert patched["done_at"]

    assert client.get("/api/action-items").json()["open"] == 1
    still_open = client.get("/api/action-items", params={"open": True}).json()
    assert [item["what"] for item in still_open["items"]] == ["pull numbers"]

    # And back again.
    reopened = client.patch(f"/api/action-items/{item_id}", json={"done": False}).json()
    assert reopened["done"] is False
    assert client.get("/api/action-items").json()["open"] == 2


def test_ticking_a_missing_item_is_404(api) -> None:  # type: ignore[no-untyped-def]
    assert api.client().patch("/api/action-items/424242", json={"done": True}).status_code == 404


def test_one_meetings_items(api) -> None:  # type: ignore[no-untyped-def]
    _meeting(api, "m-a", "A", "2026-09-19T09:00:00Z")
    _meeting(api, "m-b", "B", "2026-09-20T09:00:00Z")
    api.services.dao.replace_action_items("m-a", [("ME", "from a", None, None)])
    api.services.dao.replace_action_items("m-b", [("ME", "from b", None, None)])
    body = api.client().get("/api/action-items", params={"meeting_id": "m-a"}).json()
    assert [item["what"] for item in body["items"]] == ["from a"]


def test_the_meeting_payload_carries_its_items(api) -> None:  # type: ignore[no-untyped-def]
    """So the meeting page can tick the same row the inbox ticks."""
    _meeting(api, "m-a", "A", "2026-09-20T09:00:00Z")
    api.services.dao.replace_action_items("m-a", [("Dana", "write the spec", None, None)])
    payload = api.client().get("/api/meetings/m-a").json()
    assert [item["what"] for item in payload["action_items"]] == ["write the spec"]


# ------------------------------------------------------------------ search


def test_search_returns_the_sentence(api) -> None:  # type: ignore[no-untyped-def]
    """Four identical-looking titles was the complaint; this is the fix."""
    _meeting(api, "m-a", "Onboarding funnel review", "2026-09-20T09:30:00Z")
    api.services.dao.index_turns(
        "m-a",
        [
            Turn(0, "THEM", 4000, "So activation is down six percent since the new flow."),
            Turn(1, "ME", 15500, "Mostly step three. The interstitial is where people fall off."),
        ],
    )
    body = api.client().get("/api/search", params={"q": "interstitial"}).json()
    assert body["count"] == 1
    hit = body["hits"][0]
    assert hit["meeting_id"] == "m-a"
    assert hit["meeting_title"] == "Onboarding funnel review"
    assert hit["speaker"] == "ME"
    # The moment, so the result can land on it rather than at the top of the meeting.
    assert hit["at_ms"] == 15500
    assert "interstitial" in hit["snippet"].lower()


def test_search_for_nothing_is_empty_rather_than_everything(api) -> None:  # type: ignore[no-untyped-def]
    _meeting(api, "m-a", "A", "2026-09-20T09:00:00Z")
    api.services.dao.index_turns("m-a", [Turn(0, "ME", 0, "hello")])
    assert api.client().get("/api/search", params={"q": "   "}).json()["hits"] == []
