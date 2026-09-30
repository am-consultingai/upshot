"""The calendar routes, through the real middleware stack."""

from __future__ import annotations

import os
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


# ------------------------------------------------ the browser profile (z8tj1hca86)


def test_connect_opens_googles_page_in_the_default_browser(  # type: ignore[no-untyped-def]
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not from the page: Upshot's window runs in a browser profile of its own."""
    from app import window

    opened: list[str] = []
    monkeypatch.setattr(window, "open_external", lambda url: opened.append(url) or True)
    started = api.client().post("/api/calendar/connect").json()
    assert started["opened"] is True and opened == [started["auth_url"]]


def test_where_the_server_cannot_open_it_the_page_does(api) -> None:  # type: ignore[no-untyped-def]
    started = api.client().post("/api/calendar/connect").json()
    assert started["state"] == "connecting" and started["opened"] is False
    assert api.client().get("/api/calendar/status").json()["opens_externally"] is False


def test_links_leaving_the_app_go_to_the_default_browser(  # type: ignore[no-untyped-def]
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import window

    opened: list[str] = []
    monkeypatch.setattr(window.sys, "platform", "win32")
    # os.startfile exists on Windows only.
    monkeypatch.setattr(os, "startfile", lambda url: opened.append(url), raising=False)
    client = api.client()
    ok = client.post("/api/open", json={"url": "https://myaccount.google.com/connections"})
    assert ok.json() == {"opened": True} and opened == ["https://myaccount.google.com/connections"]
    for bad in ("file:///C:/Windows/System32/calc.exe", "C:\\Windows\\notepad.exe", "javascript:x"):
        assert client.post("/api/open", json={"url": bad}).status_code == 422, bad
    assert len(opened) == 1, "nothing but http(s) is ever opened"


def test_the_status_carries_the_users_name(api) -> None:  # type: ignore[no-untyped-def]
    client = api.client()
    started = client.post("/api/calendar/connect").json()
    consent = api.google.consent(started["auth_url"])
    httpx.get(
        api.google.redirect_uri + "/",
        params={"state": consent["state"], "code": "good-code", "scope": SCOPE},
        timeout=5.0,
    )
    assert client.get("/api/calendar/status").json()["name"] == "Dana"
