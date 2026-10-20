"""The first-run tour's log route and its setting (app/tour.py)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from tests.fixtures.api import build_harness


@pytest.fixture
def api(tmp_path: Path, app_home: Path):  # type: ignore[no-untyped-def]
    return build_harness(tmp_path)


def test_each_event_is_one_line_in_the_app_log(api, caplog) -> None:  # type: ignore[no-untyped-def]
    client = api.client()
    caplog.set_level(logging.INFO, logger="app.tour")
    bodies = [
        {"event": "started", "origin": "setup"},
        {
            "event": "sample_created",
            "recordings": 2,
            "events": 5,
            "own_events": 0,
            "borrowed": False,
        },
        {"event": "ended", "outcome": "skipped", "stop": 3, "of": 4},
        {"event": "sample_removed", "items": 7, "burnt": 4},
    ]
    for body in bodies:
        response = client.post("/api/tour/event", json=body)
        assert response.status_code == 200
        assert response.json() == {"logged": True}
    lines = [record.getMessage() for record in caplog.records if record.name == "app.tour"]
    assert lines == [
        "tour started (after setup)",
        "sample created for the tour: 2 recordings, 5 calendar events; "
        "the week was empty, so sample events fill it; titles the defaults",
        "tour skipped at stop 3 of 4",
        "sample removed: 7 items, 4 burnt on screen; the user's own data was not touched",
    ]


def test_a_week_with_events_and_borrowed_titles_say_so(api, caplog) -> None:  # type: ignore[no-untyped-def]
    client = api.client()
    caplog.set_level(logging.INFO, logger="app.tour")
    client.post(
        "/api/tour/event",
        json={
            "event": "sample_created",
            "recordings": 2,
            "events": 0,
            "own_events": 6,
            "borrowed": True,
        },
    )
    skipped = {"event": "ended", "outcome": "skipped", "stop": 0, "of": 4}
    client.post("/api/tour/event", json=skipped)
    client.post("/api/tour/event", json={"event": "ended", "outcome": "done"})
    lines = [record.getMessage() for record in caplog.records if record.name == "app.tour"]
    assert lines == [
        "sample created for the tour: 2 recordings, 0 calendar events; "
        "the week has 6 of the user's meetings, so no sample events were added; "
        "titles borrowed from the user's meetings",
        "tour skipped at the welcome card",
        "tour done",
    ]


def test_only_the_fixed_fields_are_taken(api) -> None:  # type: ignore[no-untyped-def]
    client = api.client()
    assert client.post("/api/tour/event", json={"event": "anything"}).status_code == 422
    assert client.post("/api/tour/event", json={"event": "ended", "stop": 999}).status_code == 422
    # A title has no field to travel in: an unknown key is ignored, never logged.
    extra = {"event": "started", "origin": "replay", "title": "Board prep"}
    response = client.post("/api/tour/event", json=extra)
    assert response.status_code == 200


def test_the_tour_has_its_own_setting(api) -> None:  # type: ignore[no-untyped-def]
    client = api.client()
    assert client.get("/api/settings").json()["config"]["ui"]["tour_done"] is False
    client.put("/api/settings", json={"values": {"ui.tour_done": True}})
    assert client.get("/api/settings").json()["config"]["ui"]["tour_done"] is True
