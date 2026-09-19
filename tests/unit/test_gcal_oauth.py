"""Calendar 1: connecting a Google account (z8tj1h8jrg).

Google is faked at the transport, so the real listener, the real PKCE and the real token
handling all run. The fake checks the PKCE pair the way Google does: the verifier sent at
the exchange must hash to the challenge sent in the consent URL.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.config import FakeKeyring
from app.gcal import client as gclient
from app.gcal.oauth import (
    ACCOUNT_SECRET,
    REFRESH_SECRET,
    SCOPE,
    CalendarAuth,
    CalendarAuthError,
    CalendarUnavailable,
    authorization_url,
    pkce_pair,
)

CLIENT = gclient.OAuthClient("id-123.apps.googleusercontent.com", "GOCSPX-secret", Path("x"))


class FakeGoogle:
    """Token, revoke and one events endpoint, with enough state to misbehave on demand."""

    def __init__(self) -> None:
        self.challenge: str | None = None
        self.redirect_uri: str | None = None
        self.refresh_tokens = {"r-1"}
        self.access_tokens: set[str] = set()
        self.issued = 0
        self.granted_scope = SCOPE
        self.revoked: list[str] = []
        self.token_calls = 0
        self.offline = False
        self.client_disabled = False
        self.expires_in = 3600

    def consent(self, auth_url: str) -> dict[str, str]:
        query = {k: v[0] for k, v in parse_qs(urlparse(auth_url).query).items()}
        self.challenge = query["code_challenge"]
        self.redirect_uri = query["redirect_uri"]
        return query

    def _access(self) -> str:
        self.issued += 1
        token = f"a-{self.issued}"
        self.access_tokens.add(token)
        return token

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.offline:
            raise httpx.ConnectError("offline", request=request)
        url = str(request.url)
        if url.startswith("https://oauth2.googleapis.com/token"):
            self.token_calls += 1
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            if self.client_disabled:
                return httpx.Response(401, json={"error": "invalid_client"})
            if (
                form["client_id"] != CLIENT.client_id
                or form["client_secret"] != CLIENT.client_secret
            ):
                return httpx.Response(401, json={"error": "invalid_client"})
            if form["grant_type"] == "authorization_code":
                digest = hashlib.sha256(form["code_verifier"].encode()).digest()
                expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
                if form["code"] != "good-code" or expected != self.challenge:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                if form["redirect_uri"] != self.redirect_uri:
                    return httpx.Response(400, json={"error": "redirect_uri_mismatch"})
                return httpx.Response(
                    200,
                    json={
                        "access_token": self._access(),
                        "refresh_token": "r-1",
                        "expires_in": self.expires_in,
                        "scope": self.granted_scope,
                        "token_type": "Bearer",
                    },
                )
            if form["grant_type"] == "refresh_token":
                if form["refresh_token"] not in self.refresh_tokens:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                return httpx.Response(
                    200, json={"access_token": self._access(), "expires_in": self.expires_in}
                )
        if url.startswith("https://oauth2.googleapis.com/revoke"):
            token = parse_qs(request.content.decode())["token"][0]
            self.revoked.append(token)
            self.refresh_tokens.discard(token)
            return httpx.Response(200)
        if url.startswith("https://www.googleapis.com/calendar/v3/calendars/primary/events"):
            bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
            if bearer not in self.access_tokens:
                return httpx.Response(401, json={"error": {"code": 401}})
            return httpx.Response(200, json={"summary": "dana@example.com", "items": []})
        return httpx.Response(404)


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def google() -> FakeGoogle:
    return FakeGoogle()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def auth(google: FakeGoogle, clock: Clock) -> CalendarAuth:
    events: list[dict[str, Any]] = []
    calendar = CalendarAuth(
        FakeKeyring(),
        client_loader=lambda: CLIENT,
        http=httpx.Client(transport=httpx.MockTransport(google.handler)),
        publish=lambda **payload: events.append(payload),
        app_url="http://127.0.0.1:8000/settings#calendar",
        monotonic=clock,
    )
    calendar.events = events  # type: ignore[attr-defined]
    return calendar


def redirect(google: FakeGoogle, query: dict[str, str]) -> httpx.Response:
    """What the browser does after consent: GET the loopback redirect."""
    assert google.redirect_uri is not None
    return httpx.get(google.redirect_uri + "/", params=query, timeout=5.0)


def connect(auth: CalendarAuth, google: FakeGoogle) -> httpx.Response:
    consent = google.consent(auth.start())
    return redirect(google, {"state": consent["state"], "code": "good-code", "scope": SCOPE})


# --------------------------------------------------------------------------- pieces


def test_pkce_challenge_is_the_s256_of_the_verifier() -> None:
    verifier, challenge = pkce_pair()
    assert 43 <= len(verifier) <= 128
    digest = hashlib.sha256(verifier.encode()).digest()
    assert challenge == base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    assert "=" not in challenge
    assert pkce_pair()[0] != verifier, "a fresh verifier every time"


def test_consent_url_asks_for_exactly_what_is_needed() -> None:
    url = authorization_url(CLIENT, "http://127.0.0.1:5555", "chal", "st")
    query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert query["scope"] == "https://www.googleapis.com/auth/calendar.events.readonly"
    assert query["code_challenge_method"] == "S256"
    assert query["access_type"] == "offline" and query["prompt"] == "consent"
    assert query["redirect_uri"] == "http://127.0.0.1:5555"
    assert "client_secret" not in query, "the secret never goes into a URL"


def test_client_file_must_be_a_desktop_client(tmp_path: Path) -> None:
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"installed": {"client_id": "i", "client_secret": "s"}}))
    assert gclient.parse(good).client_id == "i"
    web = tmp_path / "web.json"
    web.write_text(json.dumps({"web": {"client_id": "i", "client_secret": "s"}}))
    with pytest.raises(ValueError, match="not a Desktop app client"):
        gclient.parse(web)


def test_the_environment_overrides_the_baked_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    override = tmp_path / "other.json"
    override.write_text(json.dumps({"installed": {"client_id": "env", "client_secret": "s"}}))
    monkeypatch.setenv(gclient.CLIENT_ENV, str(override))
    assert gclient.candidates()[0] == override
    loaded = gclient.load()
    assert loaded is not None and loaded.client_id == "env"


# --------------------------------------------------------------------------- connect


def test_connect_stores_the_token_in_the_secret_store(
    auth: CalendarAuth, google: FakeGoogle
) -> None:
    assert auth.status()["state"] == "disconnected"
    response = connect(auth, google)
    assert response.status_code == 200
    assert "close this tab" in response.text
    assert "good-code" not in response.text
    status = auth.status()
    assert status["state"] == "connected"
    assert status["account"] == "dana@example.com"
    assert status["error"] is None
    assert auth._secrets.get(REFRESH_SECRET) == "r-1"
    assert "r-1" not in json.dumps(status), "status never carries a token"
    assert {"state": "connected"} in auth.events  # type: ignore[attr-defined]


def test_status_while_waiting_offers_the_url_again(auth: CalendarAuth) -> None:
    url = auth.start()
    status = auth.status()
    assert status["state"] == "connecting"
    assert status["auth_url"] == url
    auth.cancel()
    assert auth.status()["state"] == "disconnected"


def test_a_wrong_state_is_refused_and_ends_the_attempt(
    auth: CalendarAuth, google: FakeGoogle
) -> None:
    google.consent(auth.start())
    response = redirect(google, {"state": "forged", "code": "good-code"})
    assert response.status_code == 400
    assert google.token_calls == 0, "the code is never exchanged on a bad state"
    status = auth.status()
    assert status["state"] == "disconnected"
    assert "did not come from this Upshot" in status["error"]


def test_requests_without_a_state_do_not_use_up_the_listener(
    auth: CalendarAuth, google: FakeGoogle
) -> None:
    consent = google.consent(auth.start())
    assert google.redirect_uri is not None
    assert httpx.get(google.redirect_uri + "/favicon.ico", timeout=5.0).status_code == 404
    response = redirect(google, {"state": consent["state"], "code": "good-code"})
    assert response.status_code == 200
    assert auth.status()["state"] == "connected"


def test_declining_on_the_consent_screen_is_said_plainly(
    auth: CalendarAuth, google: FakeGoogle
) -> None:
    consent = google.consent(auth.start())
    response = redirect(google, {"state": consent["state"], "error": "access_denied"})
    assert response.status_code == 400
    assert auth.status()["state"] == "disconnected"
    assert "not granted" in auth.status()["error"]


def test_an_unticked_calendar_box_is_not_a_connection(
    auth: CalendarAuth, google: FakeGoogle
) -> None:
    google.granted_scope = "openid"
    connect(auth, google)
    status = auth.status()
    assert status["state"] == "disconnected"
    assert "leave that box ticked" in status["error"]
    assert auth._secrets.get(REFRESH_SECRET) is None
    assert google.revoked, "the partial grant is handed back"


def test_the_listener_gives_up_after_the_timeout(auth: CalendarAuth, clock: Clock) -> None:
    auth.start()
    clock.t += 301
    status = auth.status()
    assert status["state"] == "disconnected"
    assert "in time" in status["error"]


def test_without_a_client_connect_says_so() -> None:
    calendar = CalendarAuth(FakeKeyring(), client_loader=lambda: None)
    assert calendar.status()["configured"] is False
    with pytest.raises(CalendarAuthError, match="no Google client"):
        calendar.start()


# --------------------------------------------------------------------------- tokens


def test_access_tokens_are_cached_and_refreshed_before_expiry(
    auth: CalendarAuth, google: FakeGoogle, clock: Clock
) -> None:
    connect(auth, google)
    first = auth.access_token()
    calls = google.token_calls
    assert auth.access_token() == first
    assert google.token_calls == calls, "no refresh while the token is good"
    clock.t += 3600 - 59  # inside the safety margin
    assert auth.access_token() != first
    assert google.token_calls == calls + 1


def test_a_401_refreshes_once_and_retries(auth: CalendarAuth, google: FakeGoogle) -> None:
    connect(auth, google)
    google.access_tokens.clear()  # Google has forgotten every access token it issued
    body = auth.get("/calendars/primary/events", {"maxResults": 1})
    assert body["summary"] == "dana@example.com"


def test_a_revoked_token_asks_to_reconnect_and_stops(
    auth: CalendarAuth, google: FakeGoogle, clock: Clock
) -> None:
    connect(auth, google)
    google.refresh_tokens.clear()  # revoked from the Google account page
    clock.t += 4000
    with pytest.raises(CalendarAuthError):
        auth.access_token()
    status = auth.status()
    assert status["state"] == "reconnect"
    assert status["account"] == "dana@example.com", "the account is remembered for the prompt"
    assert auth._secrets.get(REFRESH_SECRET) is None
    calls = google.token_calls
    with pytest.raises(CalendarAuthError):
        auth.access_token()
    assert google.token_calls == calls, "a dead token is never retried"


def test_a_token_older_than_a_week_still_refreshes(
    auth: CalendarAuth, google: FakeGoogle, clock: Clock
) -> None:
    """Nothing on this side expires a connection. (In Google's Testing mode Google does,
    after seven days, and that arrives as the invalid_grant covered above.)"""
    connect(auth, google)
    clock.t += 8 * 24 * 3600
    assert auth.access_token()
    assert auth.status()["state"] == "connected"


def test_no_network_keeps_the_connection(
    auth: CalendarAuth, google: FakeGoogle, clock: Clock
) -> None:
    connect(auth, google)
    google.offline = True
    clock.t += 4000
    with pytest.raises(CalendarUnavailable):
        auth.access_token()
    assert auth.status()["state"] == "connected"
    assert auth._secrets.get(REFRESH_SECRET) == "r-1"


def test_a_rejected_client_stops_all_calls(
    auth: CalendarAuth, google: FakeGoogle, clock: Clock
) -> None:
    connect(auth, google)
    google.client_disabled = True
    clock.t += 4000
    with pytest.raises(CalendarAuthError, match="rejected"):
        auth.access_token()
    assert auth.status()["state"] == "reconnect"
    calls = google.token_calls
    with pytest.raises(CalendarAuthError):
        auth.access_token()
    assert google.token_calls == calls


# --------------------------------------------------------------------------- disconnect


def test_disconnect_revokes_at_google_and_forgets_locally(
    auth: CalendarAuth, google: FakeGoogle
) -> None:
    connect(auth, google)
    result = auth.disconnect()
    assert result == {"revoked": True, "revoke_by_hand": None}
    assert google.revoked == ["r-1"]
    assert auth._secrets.get(REFRESH_SECRET) is None
    assert auth._secrets.get(ACCOUNT_SECRET) is None
    assert auth.status()["state"] == "disconnected"
    with pytest.raises(CalendarAuthError):
        auth.access_token()


def test_disconnect_offline_still_forgets_and_says_how_to_finish(
    auth: CalendarAuth, google: FakeGoogle
) -> None:
    connect(auth, google)
    google.offline = True
    result = auth.disconnect()
    assert result["revoked"] is False
    assert result["revoke_by_hand"].startswith("https://myaccount.google.com/")
    assert auth.status()["state"] == "disconnected"
