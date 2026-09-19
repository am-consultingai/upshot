"""The calendar routes, through the real middleware stack."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.config import FakeKeyring
from app.gcal.oauth import SCOPE, CalendarAuth
from tests.fixtures.api import build_harness
from tests.unit.test_gcal_oauth import CLIENT, FakeGoogle


@pytest.fixture
def api(tmp_path: Path, app_home: Path):  # type: ignore[no-untyped-def]
    harness = build_harness(tmp_path)
    google = FakeGoogle()
    harness.google = google
    harness.services.calendar = CalendarAuth(
        harness.services.config.secrets,
        client_loader=lambda: CLIENT,
        http=httpx.Client(transport=httpx.MockTransport(google.handler)),
        publish=lambda **payload: harness.services.events.publish("calendar", **payload),
    )
    yield harness
    harness.services.calendar.close()


def test_connect_status_disconnect(api, app_home: Path) -> None:  # type: ignore[no-untyped-def]
    client = api.client()
    assert client.get("/api/calendar/status").json()["state"] == "disconnected"

    started = client.post("/api/calendar/connect").json()
    assert started["state"] == "connecting"
    consent = api.google.consent(started["auth_url"])
    httpx.get(
        api.google.redirect_uri + "/",
        params={"state": consent["state"], "code": "good-code", "scope": SCOPE},
        timeout=5.0,
    )

    status = client.get("/api/calendar/status").json()
    assert status["state"] == "connected"
    assert status["account"] == "dana@example.com"
    assert any(event.type == "calendar" for event in api.services.events.history)

    # The token is in the credential store and nowhere a user or a bundle can read it.
    assert api.services.config.secrets.get("google_refresh_token") == "r-1"
    api.services.config.save()
    for path in app_home.rglob("*"):
        if path.is_file():
            assert "r-1" not in path.read_text(errors="ignore"), path
    assert "r-1" not in client.get("/api/settings").text

    gone = client.post("/api/calendar/disconnect").json()
    assert gone["revoked"] is True and gone["state"] == "disconnected"
    assert api.google.revoked == ["r-1"]


def test_calendar_routes_need_csrf(api) -> None:  # type: ignore[no-untyped-def]
    from app.api.security import CSRF_HEADER

    client = api.client()
    del client.headers[CSRF_HEADER]
    assert client.post("/api/calendar/connect").status_code == 403
    assert client.post("/api/calendar/disconnect").status_code == 403


def test_connect_without_a_client_is_a_409(api) -> None:  # type: ignore[no-untyped-def]
    api.services.calendar = CalendarAuth(FakeKeyring(), client_loader=lambda: None)
    response = api.client().post("/api/calendar/connect")
    assert response.status_code == 409
    assert "no Google client" in response.text
