"""The calendar routes, through the real middleware stack."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import pytest

from app.config import FakeKeyring
from app.gcal.accounts import AccountRegistry, token_key
from app.gcal.oauth import SCOPE, CalendarAccounts
from tests.fixtures.api import build_harness
from tests.unit.test_gcal_oauth import CLIENT, FakeGoogle


@pytest.fixture
def api(tmp_path: Path, app_home: Path):  # type: ignore[no-untyped-def]
    harness = build_harness(tmp_path)
    google = FakeGoogle()
    harness.google = google
    harness.services.calendar = CalendarAccounts(
        harness.services.config.secrets,
        AccountRegistry(harness.services.conn, harness.clock),
        client_loader=lambda: CLIENT,
        http=httpx.Client(transport=httpx.MockTransport(google.handler)),
        publish=lambda **payload: harness.services.events.publish("calendar", **payload),
    )
    yield harness
    harness.services.calendar.close()


def _sign_in(api, client, address: str | None = None, path: str = "/api/calendar/connect"):  # type: ignore[no-untyped-def]
    if address is not None:
        api.google.account = address
        api.google.next_refresh = f"r-{address}"
    started = client.post(path).json()
    assert started["state"] == "connecting"
    consent = api.google.consent(started["auth_url"])
    httpx.get(
        api.google.redirect_uri + "/",
        params={"state": consent["state"], "code": "good-code", "scope": SCOPE},
        timeout=5.0,
    )
    return client.get("/api/calendar/status").json()


def test_connect_status_remove(api, app_home: Path) -> None:  # type: ignore[no-untyped-def]
    client = api.client()
    assert client.get("/api/calendar/status").json()["state"] == "disconnected"

    status = _sign_in(api, client)
    assert status["state"] == "connected"
    assert status["account"] == "dana@example.com"
    [account] = status["accounts"]
    assert account["address"] == "dana@example.com" and account["visible"] is True
    assert account["state"] == "connected" and account["color"] == 1
    assert any(event.type == "calendar" for event in api.services.events.history)

    # The token is in the credential store and nowhere a user or a bundle can read it.
    assert api.services.config.secrets.get(token_key(account["id"])) == "r-1"
    api.services.config.save()
    for path in app_home.rglob("*"):
        if path.is_file():
            assert "r-1" not in path.read_text(errors="ignore"), path
    assert "r-1" not in client.get("/api/settings").text

    gone = client.delete(f"/api/calendar/accounts/{account['id']}").json()
    assert gone["revoked"] is True and gone["state"] == "disconnected"
    assert gone["accounts"] == []
    assert api.google.revoked == ["r-1"]
    kept = api.services.calendar.registry.get(account["id"])
    assert kept.removed and kept.address == "dana@example.com", "kept in the database"
    assert client.delete(f"/api/calendar/accounts/{account['id']}").status_code == 404


def test_add_another_calendar(api) -> None:  # type: ignore[no-untyped-def]
    client = api.client()
    _sign_in(api, client)
    status = _sign_in(api, client, "noa@example.com")
    assert [a["address"] for a in status["accounts"]] == ["dana@example.com", "noa@example.com"]
    assert [a["color"] for a in status["accounts"]] == [1, 2]
    assert api.google.revoked == []


def test_hide_and_show_an_account(api) -> None:  # type: ignore[no-untyped-def]
    client = api.client()
    account = _sign_in(api, client)["accounts"][0]["id"]
    hidden = client.patch(f"/api/calendar/accounts/{account}", json={"visible": False})
    assert hidden.status_code == 200
    assert hidden.json()["accounts"][0]["visible"] is False
    assert api.services.calendar.active_ids() == set()
    shown = client.patch(f"/api/calendar/accounts/{account}", json={"visible": True}).json()
    assert shown["accounts"][0]["visible"] is True
    assert (
        client.patch("/api/calendar/accounts/ga_nope0000", json={"visible": True}).status_code
        == 404
    )


def test_reconnect_offers_the_same_account(api) -> None:  # type: ignore[no-untyped-def]
    client = api.client()
    account = _sign_in(api, client)["accounts"][0]["id"]
    status = _sign_in(api, client, path=f"/api/calendar/accounts/{account}/reconnect")
    assert api.google.login_hints[-1] == "dana@example.com"
    assert [a["id"] for a in status["accounts"]] == [account]
    assert client.post("/api/calendar/accounts/ga_nope0000/reconnect").status_code == 404


def test_the_retired_routes_are_gone(api) -> None:  # type: ignore[no-untyped-def]
    """Nothing deletes calendar data any more (D82): no "forget the cache", and
    "disconnect" is removing one account."""
    client = api.client()
    assert client.delete("/api/calendar/cache").status_code in (404, 405)
    assert client.post("/api/calendar/disconnect").status_code in (404, 405)


def test_calendar_routes_need_csrf(api) -> None:  # type: ignore[no-untyped-def]
    from app.api.security import CSRF_HEADER

    client = api.client()
    del client.headers[CSRF_HEADER]
    assert client.post("/api/calendar/connect").status_code == 403
    assert client.delete("/api/calendar/accounts/ga_00000000").status_code == 403
    assert (
        client.patch("/api/calendar/accounts/ga_00000000", json={"visible": False}).status_code
        == 403
    )


def test_connect_without_a_client_is_a_409(api) -> None:  # type: ignore[no-untyped-def]
    api.services.calendar = CalendarAccounts(
        FakeKeyring(), AccountRegistry(api.services.conn), client_loader=lambda: None
    )
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


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="on Windows the server opens it: the test above; this one ran a real browser on CI",
)
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


def test_another_account_keeps_the_first_ones_events(tmp_path: Path, app_home: Path) -> None:
    """Adding an account never touches another's cached events (D82); before, "Use a
    different account" emptied the cache."""
    from datetime import UTC, datetime, timedelta

    from app.clock import FakeClock
    from app.config import default_config
    from app.db.dao import connect as db_connect
    from app.events import EventBus
    from app.gcal.events import CalendarEvent
    from app.services import build_calendar

    clock = FakeClock()
    accounts, sync, _source = build_calendar(
        default_config(), db_connect(tmp_path / "index.db"), EventBus(), clock
    )
    first, _ = accounts.registry.add_or_restore("dana@example.com")
    start = datetime(2026, 9, 30, 9, tzinfo=UTC)
    sync.store.replace_window(
        first.id, "primary", start, start + timedelta(days=1),
        [CalendarEvent("primary", "e1", "Standup", start, start + timedelta(minutes=15))],
        synced_at=start,
    )  # fmt: skip
    second, _ = accounts.registry.add_or_restore("noa@example.com")
    accounts._announce(account_id=second.id, restored=False)
    assert sync.store.count() == 1
