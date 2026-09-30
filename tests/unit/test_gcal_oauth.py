"""Calendar 1: connecting Google accounts (z8tj1h8jrg), several at once (D82).

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

from app.clock import FakeClock
from app.config import FakeKeyring
from app.db.dao import connect as db_connect
from app.gcal import client as gclient
from app.gcal.accounts import AccountRegistry, token_key
from app.gcal.oauth import (
    LEGACY_NAME_SECRET,
    SCOPE,
    CalendarAccounts,
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
        #: Whose each token is: address by refresh token and by access token.
        self.refresh_owner: dict[str, str] = {"r-1": "dana@example.com"}
        self.access_owner: dict[str, str] = {}
        self.access_tokens: set[str] = set()
        self.login_hints: list[str | None] = []
        self.issued = 0
        self.granted_scope = SCOPE
        self.revoked: list[str] = []
        self.token_calls = 0
        self.offline = False
        self.client_disabled = False
        self.expires_in = 3600
        #: Called while the code is exchanged: what a poll sees in that moment.
        self.during_exchange: Any = None
        #: Who signs in, and the refresh token the next code exchange hands out.
        self.account = "dana@example.com"
        self.next_refresh = "r-1"
        #: The events endpoint answers without the calendar's name.
        self.no_address = False

    def consent(self, auth_url: str) -> dict[str, str]:
        query = {k: v[0] for k, v in parse_qs(urlparse(auth_url).query).items()}
        self.challenge = query["code_challenge"]
        self.redirect_uri = query["redirect_uri"]
        self.login_hints.append(query.get("login_hint"))
        return query

    def _access(self, owner: str) -> str:
        self.issued += 1
        token = f"a-{self.issued}"
        self.access_tokens.add(token)
        self.access_owner[token] = owner
        return token

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.offline:
            raise httpx.ConnectError("offline", request=request)
        url = str(request.url)
        if url.startswith("https://oauth2.googleapis.com/token"):
            self.token_calls += 1
            if self.during_exchange is not None:
                self.during_exchange()
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
                self.refresh_tokens.add(self.next_refresh)
                self.refresh_owner[self.next_refresh] = self.account
                return httpx.Response(
                    200,
                    json={
                        "access_token": self._access(self.account),
                        "refresh_token": self.next_refresh,
                        "expires_in": self.expires_in,
                        "scope": self.granted_scope,
                        "token_type": "Bearer",
                    },
                )
            if form["grant_type"] == "refresh_token":
                if form["refresh_token"] not in self.refresh_tokens:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                owner = self.refresh_owner.get(form["refresh_token"], self.account)
                return httpx.Response(
                    200, json={"access_token": self._access(owner), "expires_in": self.expires_in}
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
            if self.no_address:
                return httpx.Response(200, json={"items": []})
            return httpx.Response(200, json={"summary": self.access_owner.get(bearer), "items": []})
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


def make_accounts(google: FakeGoogle, clock: Clock, tmp: Path, **kwargs: Any) -> CalendarAccounts:
    conn = db_connect(tmp / "index.db")
    return CalendarAccounts(
        FakeKeyring(),
        AccountRegistry(conn, FakeClock()),
        client_loader=kwargs.pop("client_loader", lambda: CLIENT),
        http=httpx.Client(transport=httpx.MockTransport(google.handler)),
        monotonic=clock,
        **kwargs,
    )


@pytest.fixture
def auth(google: FakeGoogle, clock: Clock, tmp_path: Path) -> CalendarAccounts:
    events: list[dict[str, Any]] = []
    calendar = make_accounts(
        google, clock, tmp_path, publish=lambda **payload: events.append(payload)
    )
    calendar.events = events  # type: ignore[attr-defined]
    return calendar


def only(accounts: CalendarAccounts) -> CalendarAuth:
    """The one account connected so far."""
    listed = accounts.registry.accounts()
    assert len(listed) == 1, listed
    return accounts.auth_for(listed[0].id)


def token_of(accounts: CalendarAccounts, address: str = "dana@example.com") -> str | None:
    account = accounts.registry.by_address(address)
    assert account is not None
    return accounts._secrets.get(token_key(account.id))


def redirect(google: FakeGoogle, query: dict[str, str]) -> httpx.Response:
    """What the browser does after consent: GET the loopback redirect."""
    assert google.redirect_uri is not None
    return httpx.get(google.redirect_uri + "/", params=query, timeout=5.0)


def connect(
    auth: CalendarAccounts,
    google: FakeGoogle,
    account: str | None = None,
    *,
    reconnect: str | None = None,
) -> httpx.Response:
    if account is not None:
        google.account = account
        google.next_refresh = f"r-{account}"
    consent = google.consent(auth.start(reconnect=reconnect))
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
    # Calendar events only: no profile scope, so Google shows one consent screen.
    assert query["scope"] == "https://www.googleapis.com/auth/calendar.events.readonly"
    assert query["code_challenge_method"] == "S256"
    assert query["access_type"] == "offline"
    # The account chooser every time (z8tj1hca86), and a fresh refresh token.
    assert query["prompt"].split() == ["select_account", "consent"]
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
    auth: CalendarAccounts, google: FakeGoogle
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
    assert token_of(auth) == "r-1"
    assert "r-1" not in json.dumps(status), "status never carries a token"
    [account] = status["accounts"]
    assert account["address"] == "dana@example.com" and account["state"] == "connected"
    connected = [e for e in auth.events if e.get("account_id")]  # type: ignore[attr-defined]
    assert connected == [{"state": "connected", "account_id": account["id"], "restored": False}]


def test_a_name_kept_by_an_earlier_build_is_removed_on_connect(
    auth: CalendarAccounts, google: FakeGoogle
) -> None:
    auth._secrets.set(LEGACY_NAME_SECRET, "Dana")
    connect(auth, google)
    assert auth._secrets.get(LEGACY_NAME_SECRET) is None
    assert "name" not in auth.status()


def test_a_poll_while_the_code_is_exchanged_still_says_connecting(
    auth: CalendarAccounts, google: FakeGoogle
) -> None:
    """Machine B, 2026-09-30: first-run setup polled between Google's redirect and the
    token, read "disconnected" with no error, and showed "Cancelled" for a connection that
    succeeded a moment later."""
    seen: list[str] = []
    google.during_exchange = lambda: seen.append(auth.status()["state"])
    connect(auth, google)
    assert seen == ["connecting"]
    assert auth.status()["state"] == "connected"


def test_upshot_comes_back_in_front_when_the_sign_in_ends(
    google: FakeGoogle, clock: Clock, tmp_path: Path
) -> None:
    """The sign-in runs in the user's browser; afterwards Upshot's window is brought
    forward, connected or not, and the page has no link to follow (machine B, 2026-09-30:
    a link opened Upshot as a tab, an upshot: link made Chrome ask "Open Upshot?")."""
    ended: list[bool] = []
    calendar = make_accounts(google, clock, tmp_path, on_complete=ended.append)
    try:
        page = connect(calendar, google).text
        consent = google.consent(calendar.start())
        redirect(google, {"state": consent["state"], "error": "access_denied"})
    finally:
        calendar.close()
    assert ended == [True, False]
    assert "<a " not in page and "upshot:" not in page
    assert "You can close this tab." in page and "window.close()" in page


def test_status_while_waiting_offers_the_url_again(auth: CalendarAccounts) -> None:
    url = auth.start()
    status = auth.status()
    assert status["state"] == "connecting"
    assert status["auth_url"] == url
    auth.cancel()
    assert auth.status()["state"] == "disconnected"


def test_a_wrong_state_is_refused_and_ends_the_attempt(
    auth: CalendarAccounts, google: FakeGoogle
) -> None:
    google.consent(auth.start())
    response = redirect(google, {"state": "forged", "code": "good-code"})
    assert response.status_code == 400
    assert google.token_calls == 0, "the code is never exchanged on a bad state"
    status = auth.status()
    assert status["state"] == "disconnected"
    assert "did not come from this Upshot" in status["error"]


def test_requests_without_a_state_do_not_use_up_the_listener(
    auth: CalendarAccounts, google: FakeGoogle
) -> None:
    consent = google.consent(auth.start())
    assert google.redirect_uri is not None
    assert httpx.get(google.redirect_uri + "/favicon.ico", timeout=5.0).status_code == 404
    response = redirect(google, {"state": consent["state"], "code": "good-code"})
    assert response.status_code == 200
    assert auth.status()["state"] == "connected"


def test_declining_on_the_consent_screen_is_said_plainly(
    auth: CalendarAccounts, google: FakeGoogle
) -> None:
    consent = google.consent(auth.start())
    response = redirect(google, {"state": consent["state"], "error": "access_denied"})
    assert response.status_code == 400
    assert auth.status()["state"] == "disconnected"
    assert "not granted" in auth.status()["error"]


def test_an_unticked_calendar_box_is_not_a_connection(
    auth: CalendarAccounts, google: FakeGoogle
) -> None:
    google.granted_scope = "openid"
    connect(auth, google)
    status = auth.status()
    assert status["state"] == "disconnected"
    assert "leave that box ticked" in status["error"]
    assert status["accounts"] == []
    assert google.revoked, "the partial grant is handed back"


def test_the_listener_gives_up_after_the_timeout(auth: CalendarAccounts, clock: Clock) -> None:
    auth.start()
    clock.t += 301
    status = auth.status()
    assert status["state"] == "disconnected"
    assert "in time" in status["error"]


def test_without_a_client_connect_says_so(google: FakeGoogle, clock: Clock, tmp_path: Path) -> None:
    calendar = make_accounts(google, clock, tmp_path, client_loader=lambda: None)
    assert calendar.status()["configured"] is False
    with pytest.raises(CalendarAuthError, match="no Google client"):
        calendar.start()


# --------------------------------------------------------------------------- tokens


def test_access_tokens_are_cached_and_refreshed_before_expiry(
    auth: CalendarAccounts, google: FakeGoogle, clock: Clock
) -> None:
    connect(auth, google)
    account = only(auth)
    first = account.access_token()
    calls = google.token_calls
    assert account.access_token() == first
    assert google.token_calls == calls, "no refresh while the token is good"
    clock.t += 3600 - 59  # inside the safety margin
    assert account.access_token() != first
    assert google.token_calls == calls + 1


def test_a_401_refreshes_once_and_retries(auth: CalendarAccounts, google: FakeGoogle) -> None:
    connect(auth, google)
    account = only(auth)
    google.access_tokens.clear()  # Google has forgotten every access token it issued
    body = account.get("/calendars/primary/events", {"maxResults": 1})
    assert body["summary"] == "dana@example.com"


def test_a_revoked_token_asks_to_reconnect_and_stops(
    auth: CalendarAccounts, google: FakeGoogle, clock: Clock
) -> None:
    connect(auth, google)
    account = only(auth)
    google.refresh_tokens.clear()  # revoked from the Google account page
    clock.t += 4000
    with pytest.raises(CalendarAuthError):
        account.access_token()
    status = auth.status()
    assert status["state"] == "reconnect"
    assert status["account"] == "dana@example.com", "the account is remembered for the prompt"
    assert status["accounts"][0]["state"] == "reconnect"
    assert token_of(auth) is None
    calls = google.token_calls
    with pytest.raises(CalendarAuthError):
        account.access_token()
    assert google.token_calls == calls, "a dead token is never retried"


def test_a_token_older_than_a_week_still_refreshes(
    auth: CalendarAccounts, google: FakeGoogle, clock: Clock
) -> None:
    """Nothing on this side expires a connection. (In Google's Testing mode Google does,
    after seven days, and that arrives as the invalid_grant covered above.)"""
    connect(auth, google)
    account = only(auth)
    clock.t += 8 * 24 * 3600
    assert account.access_token()
    assert auth.status()["state"] == "connected"


def test_no_network_keeps_the_connection(
    auth: CalendarAccounts, google: FakeGoogle, clock: Clock
) -> None:
    connect(auth, google)
    account = only(auth)
    google.offline = True
    clock.t += 4000
    with pytest.raises(CalendarUnavailable):
        account.access_token()
    assert auth.status()["state"] == "connected"
    assert token_of(auth) == "r-1"


def test_a_rejected_client_stops_all_calls(
    auth: CalendarAccounts, google: FakeGoogle, clock: Clock
) -> None:
    connect(auth, google)
    account = only(auth)
    google.client_disabled = True
    clock.t += 4000
    with pytest.raises(CalendarAuthError, match="rejected"):
        account.access_token()
    assert auth.status()["state"] == "reconnect"
    calls = google.token_calls
    with pytest.raises(CalendarAuthError):
        account.access_token()
    assert google.token_calls == calls


# --------------------------------------------------------------------------- remove


def test_remove_revokes_at_google_and_keeps_the_account_hidden(
    auth: CalendarAccounts, google: FakeGoogle
) -> None:
    connect(auth, google)
    account = only(auth)
    auth._secrets.set(LEGACY_NAME_SECRET, "Dana")
    result = auth.remove(account.account_id)
    assert result == {"revoked": True, "revoke_by_hand": None}
    assert google.revoked == ["r-1"]
    assert token_of(auth) is None
    assert auth._secrets.get(LEGACY_NAME_SECRET) is None
    assert auth.status()["state"] == "disconnected"
    assert auth.status()["accounts"] == [], "a removed account is not listed"
    kept = auth.registry.get(account.account_id)
    assert kept is not None and kept.removed and kept.address == "dana@example.com"
    with pytest.raises(CalendarAuthError):
        account.access_token()


def test_remove_offline_still_forgets_and_says_how_to_finish(
    auth: CalendarAccounts, google: FakeGoogle
) -> None:
    connect(auth, google)
    google.offline = True
    result = auth.remove(only(auth).account_id)
    assert result["revoked"] is False
    assert result["revoke_by_hand"].startswith("https://myaccount.google.com/")
    assert auth.status()["state"] == "disconnected"


# --------------------------------------------------------------------------- several


def test_a_second_account_is_added_not_replaced(auth: CalendarAccounts, google: FakeGoogle) -> None:
    connect(auth, google)
    connect(auth, google, "noa@example.com")
    status = auth.status()
    assert [a["address"] for a in status["accounts"]] == ["dana@example.com", "noa@example.com"]
    assert all(a["state"] == "connected" for a in status["accounts"])
    assert google.revoked == [], "nothing is revoked when another account is added"
    assert token_of(auth) == "r-1"
    assert token_of(auth, "noa@example.com") == "r-noa@example.com"
    assert len(auth.active_ids()) == 2


def test_each_account_refreshes_its_own_token(
    auth: CalendarAccounts, google: FakeGoogle, clock: Clock
) -> None:
    connect(auth, google)
    connect(auth, google, "noa@example.com")
    clock.t += 4000
    for account in auth.registry.accounts():
        body = auth.auth_for(account.id).get("/calendars/primary/events")
        assert body["summary"] == account.address


def test_invalid_grant_on_one_account_leaves_the_other_connected(
    auth: CalendarAccounts, google: FakeGoogle, clock: Clock
) -> None:
    connect(auth, google)
    connect(auth, google, "noa@example.com")
    google.refresh_tokens.discard("r-1")
    clock.t += 4000
    dana = auth.registry.by_address("dana@example.com")
    noa = auth.registry.by_address("noa@example.com")
    assert dana is not None and noa is not None
    with pytest.raises(CalendarAuthError):
        auth.auth_for(dana.id).access_token()
    assert auth.auth_for(noa.id).access_token()
    states = {a["address"]: a["state"] for a in auth.status()["accounts"]}
    assert states == {"dana@example.com": "reconnect", "noa@example.com": "connected"}
    assert auth.status()["state"] == "connected"
    assert auth.active_ids() == {noa.id}


def test_reconnect_sends_login_hint_and_keeps_the_id(
    auth: CalendarAccounts, google: FakeGoogle
) -> None:
    connect(auth, google)
    first = only(auth).account_id
    google.next_refresh = "r-2"
    connect(auth, google, reconnect=first)
    assert google.login_hints[-1] == "dana@example.com"
    assert only(auth).account_id == first
    assert token_of(auth) == "r-2"


def test_reconnect_to_another_address_adds_that_one(
    auth: CalendarAccounts, google: FakeGoogle
) -> None:
    connect(auth, google)
    first = only(auth).account_id
    connect(auth, google, "noa@example.com", reconnect=first)
    assert {a.address for a in auth.registry.accounts()} == {
        "dana@example.com",
        "noa@example.com",
    }
    assert token_of(auth) == "r-1", "the account that was meant is untouched"


def test_reconnecting_a_removed_address_restores_it(
    auth: CalendarAccounts, google: FakeGoogle
) -> None:
    connect(auth, google)
    first = only(auth).account_id
    auth.remove(first)
    google.next_refresh = "r-2"
    connect(auth, google)
    assert only(auth).account_id == first
    restored = [e for e in auth.events if e.get("restored")]  # type: ignore[attr-defined]
    assert restored and restored[-1]["account_id"] == first


def test_no_address_fails_and_revokes_the_new_token(
    auth: CalendarAccounts, google: FakeGoogle
) -> None:
    google.no_address = True
    response = connect(auth, google)
    assert response.status_code == 400
    status = auth.status()
    assert status["accounts"] == []
    assert "could not tell which Google account" in status["error"]
    assert google.revoked == ["r-1"]


def test_an_error_reading_the_address_is_a_failure_not_a_crash(
    auth: CalendarAccounts, google: FakeGoogle
) -> None:
    """A 4xx other than 401/403 from the events endpoint used to escape the sign-in as
    an exception (z8tj1hcezv)."""
    handler = google.handler

    def forbidden(request: httpx.Request) -> httpx.Response:
        if "/calendar/v3/" in str(request.url):
            return httpx.Response(
                403, json={"error": {"errors": [{"reason": "accessNotConfigured"}]}}
            )
        return handler(request)

    auth._google.http = httpx.Client(transport=httpx.MockTransport(forbidden))
    response = connect(auth, google)
    assert response.status_code == 400
    assert "could not tell which Google account" in auth.status()["error"]


def test_hiding_an_account_leaves_it_connected_but_not_active(
    auth: CalendarAccounts, google: FakeGoogle
) -> None:
    connect(auth, google)
    hidden: list[str] = []
    auth.on_hidden.append(hidden.append)
    account = only(auth).account_id
    auth.set_visible(account, False)
    assert auth.active_ids() == set()
    assert auth.connected() is False
    assert auth.status()["accounts"][0]["visible"] is False
    assert hidden == [account]
    auth.set_visible(account, True)
    assert auth.active_ids() == {account}


def test_the_address_behind_an_older_token_can_be_read(
    auth: CalendarAccounts, google: FakeGoogle
) -> None:
    assert auth.probe_refresh("r-1") == "dana@example.com"
    assert auth.probe_refresh("r-unknown") is None
