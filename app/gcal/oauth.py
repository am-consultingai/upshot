"""Connecting a Google account: loopback OAuth with PKCE (Calendar 1, z8tj1h8jrg).

The flow, once per connection:

1. ``start`` makes a PKCE verifier and a ``state``, opens a one-shot listener on
   ``127.0.0.1:<ephemeral>``, and returns Google's consent URL. The UI opens it in the
   user's own browser — never an embedded view, which Google blocks.
2. Google redirects the browser to the listener. The listener takes the first request
   that carries a ``state``, checks it, exchanges the code, answers with a page saying
   the tab can be closed, and stops.
3. The address Google returns decides the account (D82): a new one is added, a known
   one — even a removed one — is restored with its id and its history. Its refresh token
   goes to the OS credential store (``SecretStore``) under that account's key, never to
   ``app_config.json``. Access tokens live in memory only and are refreshed on demand.

Several accounts are connected at once. ``CalendarAuth`` is one account's tokens and
calls; ``CalendarAccounts`` holds all of them and the one sign-in that can be under way.

Out-of-band ("paste this code") is not an option: Google blocked it for every client on
2023-01-31.

Failures are states, not exceptions that vanish into a log. ``invalid_grant`` — revoked,
expired, or a Testing-mode token past its seven days — means the token is dead: it is
deleted, the status says *reconnect*, and nothing retries it. No network is the opposite
case: ``CalendarUnavailable``, the token kept, try again later.
"""

from __future__ import annotations

import base64
import hashlib
import html
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from app.config import SecretStore
from app.gcal.accounts import AccountRegistry, token_key
from app.gcal.client import OAuthClient, load
from app.log import get

log = get(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API_URL = "https://www.googleapis.com/calendar/v3"

#: Events only. Not the broader ``calendar.readonly``: nothing here needs calendar
#: settings or ACLs, and a picker of calendars would add ``calendar.calendars.readonly``.
SCOPE = "https://www.googleapis.com/auth/calendar.events.readonly"

#: The user's first name, kept by builds of 2026-09-30 that asked Google for the profile
#: scope. No longer asked for (one consent screen instead of two, product owner): anything
#: stored is removed at the next connect or disconnect.
LEGACY_NAME_SECRET = "google_name"

#: How long the listener waits for the browser to come back.
CONNECT_TIMEOUT_S = 300.0
#: Refresh this long before Google's stated expiry, so a token never dies mid-request.
EXPIRY_MARGIN_S = 60.0
HTTP_TIMEOUT_S = 10.0

REVOKE_BY_HAND = "https://myaccount.google.com/connections"
CONNECTED_TTL_S = 5.0


class CalendarAuthError(Exception):
    """The connection is unusable until the user connects again. Never retried."""


class CalendarUnavailable(Exception):
    """Google could not be reached, or answered with a server error. Try again later."""


def pkce_pair() -> tuple[str, str]:
    """A code verifier and its S256 challenge (RFC 7636 §4.1-4.2)."""
    verifier = secrets.token_urlsafe(64)  # 86 characters, inside the 43-128 allowed
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def authorization_url(
    client: OAuthClient,
    redirect_uri: str,
    challenge: str,
    state: str,
    login_hint: str | None = None,
) -> str:
    query = {
        "client_id": client.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        # A refresh token, and a fresh one on every connect: without prompt=consent
        # Google omits it for an account that has granted access before. And the
        # account chooser every time, so a browser profile signed in to several accounts
        # does not connect whichever one it used last (ClickUp z8tj1hca86).
        "access_type": "offline",
        "prompt": "select_account consent",
    }
    if login_hint:
        # Reconnecting an account: Google offers that one first. The chooser still
        # shows, so the user can pick another; what comes back is checked, not assumed.
        query["login_hint"] = login_hint
    return f"{AUTH_URL}?{urlencode(query)}"


@dataclass
class _Pending:
    state: str
    verifier: str
    redirect_uri: str
    auth_url: str
    deadline: float
    server: HTTPServer
    #: The account being reconnected, when it is a reconnect.
    reconnecting: str | None = None


class _TokenError(Exception):
    """Google answered a token request with an OAuth error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _Google:
    """What every account shares: the build's OAuth client and the HTTP transport."""

    def __init__(
        self, client_loader: Callable[[], OAuthClient | None], http: httpx.Client | None
    ) -> None:
        self._client_loader = client_loader
        self.http = http or httpx.Client(timeout=HTTP_TIMEOUT_S)
        #: Set when Google rejects the client itself. Every request would fail the same
        #: way, so nothing is sent until the user connects again.
        self.client_rejected = False

    def client(self) -> OAuthClient | None:
        try:
            return self._client_loader()
        except (OSError, ValueError) as exc:
            log.warning("google client unusable: %s", exc)
            return None

    def token(self, form: dict[str, str]) -> dict[str, Any]:
        client = self.client()
        if client is None:
            raise CalendarAuthError("this build of Upshot has no Google client configured")
        data = {"client_id": client.client_id, "client_secret": client.client_secret, **form}
        try:
            response = self.http.post(TOKEN_URL, data=data)
        except httpx.TransportError as exc:
            raise CalendarUnavailable(type(exc).__name__) from exc
        if response.status_code >= 500:
            raise CalendarUnavailable(f"Google answered {response.status_code}")
        body = _json(response)
        if response.status_code == 200 and "access_token" in body:
            return body
        code = str(body.get("error", response.status_code))
        if code in ("invalid_client", "unauthorized_client"):
            self.client_rejected = True
        raise _TokenError(code)

    def revoke(self, token: str) -> bool:
        if not token:
            return False
        try:
            response = self.http.post(REVOKE_URL, data={"token": token})
        except httpx.TransportError as exc:
            log.warning("google revoke failed: %s", type(exc).__name__)
            return False
        # 400 invalid_token means Google had already forgotten it, which is the goal.
        return response.status_code in (200, 400)

    def address(self, access_token: str) -> str | None:
        """The primary calendar's name, which for a Google account is its address.

        Read from the events endpoint so that no identity scope is needed on top of the
        calendar one. Any failure is None: the caller decides what not knowing means.
        """
        try:
            response = self.http.get(
                f"{API_URL}/calendars/primary/events",
                params={"maxResults": 1, "fields": "summary"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except httpx.TransportError as exc:
            log.warning("google connect: could not read the account address: %s", exc)
            return None
        if response.status_code != 200:
            log.warning("google connect: the address answered %s", response.status_code)
            return None
        summary = _json(response).get("summary")
        return str(summary).strip() or None if summary else None


class CalendarAuth:
    """One connected Google account: its refresh token, its access token, its calls."""

    def __init__(
        self,
        google: _Google,
        secrets_store: SecretStore,
        account_id: str,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self._google = google
        self._secrets = secrets_store
        self.account_id = account_id
        self._key = token_key(account_id)
        self._now = monotonic
        self._on_change = on_change
        self._lock = threading.Lock()
        self._access: tuple[str, float] | None = None  # token, expires at (monotonic)
        self._error: str | None = None
        self._connected_cache: tuple[bool, float] | None = None

    @property
    def error(self) -> str | None:
        return self._error

    def connected(self) -> bool:
        """Cheap: no network, and the credential store read at most every few seconds.
        The detector asks this once a second."""
        now = self._now()
        cached = self._connected_cache
        if cached is None or now - cached[1] >= CONNECTED_TTL_S:
            cached = (bool(self._secrets.get(self._key)), now)
            self._connected_cache = cached
        return cached[0] and not self._google.client_rejected

    def state(self) -> str:
        return "connected" if self.connected() else "reconnect"

    def remember(self, refresh: str, token: dict[str, Any]) -> None:
        """A sign-in for this account has just finished."""
        self._secrets.set(self._key, refresh)
        self._remember_access(token)
        with self._lock:
            self._error = None
        self._changed()

    def access_token(self) -> str:
        with self._lock:
            cached = self._access
        if cached and self._now() < cached[1]:
            return cached[0]
        if self._google.client_rejected:
            raise CalendarAuthError("Google rejected this build's client. Connect again.")
        refresh = self._secrets.get(self._key)
        if not refresh:
            raise CalendarAuthError("Google Calendar is not connected")
        try:
            token = self._google.token({"grant_type": "refresh_token", "refresh_token": refresh})
        except _TokenError as exc:
            if exc.code == "invalid_grant":
                self._drop_token("Google no longer accepts this connection. Connect again.")
                raise CalendarAuthError("the refresh token was revoked or has expired") from exc
            if self._google.client_rejected:
                with self._lock:
                    self._error = "Google rejected this build's client. Connect again."
                self._changed()
                raise CalendarAuthError(f"Google rejected the client ({exc.code})") from exc
            raise CalendarAuthError(f"Google refused the token request ({exc.code})") from exc
        return self._remember_access(token)

    def _remember_access(self, token: dict[str, Any]) -> str:
        access = str(token["access_token"])
        # Monotonic, from Google's expires_in: a wrong wall clock cannot make a live
        # token look expired or an expired one look live.
        lifetime = float(token.get("expires_in", 3600))
        with self._lock:
            self._access = (access, self._now() + max(0.0, lifetime - EXPIRY_MARGIN_S))
        return access

    def _drop_token(self, reason: str) -> None:
        log.warning("google calendar %s: %s", self.account_id, reason)
        self._secrets.delete(self._key)
        with self._lock:
            self._access = None
            self._error = reason
        self._changed()

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET a Calendar API path, refreshing once if the access token was refused."""
        for attempt in range(2):
            token = self.access_token()
            try:
                response = self._google.http.get(
                    f"{API_URL}{path}",
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.TransportError as exc:
                raise CalendarUnavailable(type(exc).__name__) from exc
            if response.status_code == 401 and attempt == 0:
                with self._lock:
                    self._access = None
                continue
            if response.status_code == 401:
                raise CalendarAuthError("Google refused a freshly refreshed token")
            if response.status_code == 403 and "insufficientPermissions" in response.text:
                self._drop_token("Upshot no longer has permission to read your calendar.")
                raise CalendarAuthError("the calendar scope is no longer granted")
            if response.status_code >= 500 or response.status_code == 429:
                raise CalendarUnavailable(f"Google answered {response.status_code}")
            response.raise_for_status()
            return _json(response)
        raise AssertionError("unreachable")  # pragma: no cover

    def forget(self) -> bool:
        """Revoke at Google, then delete the token here — both, whatever the first says.
        True when Google confirmed the revoke."""
        refresh = self._secrets.get(self._key)
        with self._lock:
            access = self._access[0] if self._access else None
        revoked = self._google.revoke(refresh or access or "") if (refresh or access) else True
        self._secrets.delete(self._key)
        with self._lock:
            self._access = None
            self._error = None
        self._changed()
        return revoked

    def _changed(self) -> None:
        self._connected_cache = None
        if self._on_change is not None:
            self._on_change()


class CalendarAccounts:
    """Every Google account this installation has connected, and the one sign-in flow.

    Several accounts at once (D82). Connecting always *adds* the account Google returns,
    or restores it when its address was connected before; it never replaces another.
    Hiding and removing are here too, because they change what every reader sees.
    """

    def __init__(
        self,
        secrets_store: SecretStore,
        registry: AccountRegistry,
        *,
        client_loader: Callable[[], OAuthClient | None] = load,
        http: httpx.Client | None = None,
        publish: Callable[..., Any] | None = None,
        on_complete: Callable[[bool], object] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        connect_timeout_s: float = CONNECT_TIMEOUT_S,
    ) -> None:
        self._secrets = secrets_store
        self.registry = registry
        self._google = _Google(client_loader, http)
        self._publish = publish
        #: Called once Google's redirect has been handled, connected or not: the app
        #: brings its window back in front (the sign-in ran in the user's browser).
        self._on_complete = on_complete
        self._now = monotonic
        self._connect_timeout_s = connect_timeout_s
        self._lock = threading.Lock()
        self._auths: dict[str, CalendarAuth] = {}
        self._pending: _Pending | None = None
        #: Google's redirect has arrived and the code is being exchanged. Still
        #: "connecting" until that ends: between the two the status read "disconnected"
        #: with no error, and first-run setup took that for a cancel (machine B, 2026-09-30).
        self._finishing = False
        self._error: str | None = None
        #: Called with an account id when it is hidden or removed (the invitation cache).
        self.on_hidden: list[Callable[[str], None]] = []
        #: Accounts that count as connected with no token and are never synced: the e2e
        #: seed's, whose events are written straight into the cache (``pin``).
        self._pinned: set[str] = set()

    # ------------------------------------------------------------------ accounts

    def auth_for(self, account_id: str) -> CalendarAuth:
        with self._lock:
            auth = self._auths.get(account_id)
            if auth is None:
                auth = CalendarAuth(
                    self._google,
                    self._secrets,
                    account_id,
                    monotonic=self._now,
                    on_change=self._announce,
                )
                self._auths[account_id] = auth
            return auth

    def _is_connected(self, account_id: str) -> bool:
        return account_id in self._pinned or self.auth_for(account_id).connected()

    def pin(self, account_id: str) -> None:
        """Test seeding only: this account counts as connected and is never synced."""
        self._pinned.add(account_id)

    def connected_ids(self) -> list[str]:
        """Listed (not removed) accounts that have a working token."""
        return [a.id for a in self.registry.accounts() if self._is_connected(a.id)]

    def active_ids(self) -> frozenset[str]:
        """Connected and shown: the accounts whose events are read, shown and matched."""
        shown = self.registry.shown_ids()
        return frozenset(i for i in shown if self._is_connected(i))

    def syncable_ids(self) -> frozenset[str]:
        """The active accounts that are read from Google."""
        return frozenset(i for i in self.active_ids() if i not in self._pinned)

    def connected(self) -> bool:
        """Is any shown account connected. Cheap enough to ask once a second."""
        return any(self._is_connected(i) for i in self.registry.shown_ids())

    def client(self) -> OAuthClient | None:
        return self._google.client()

    def probe_refresh(self, refresh: str) -> str | None:
        """The address behind a refresh token (an older install that never stored it)."""
        try:
            token = self._google.token({"grant_type": "refresh_token", "refresh_token": refresh})
        except (_TokenError, CalendarAuthError, CalendarUnavailable) as exc:
            log.warning("google: could not refresh an older connection: %s", exc)
            return None
        return self._google.address(str(token["access_token"]))

    def set_visible(self, account_id: str, visible: bool) -> None:
        self.registry.set_visible(account_id, visible)
        if not visible:
            self._hidden(account_id)
        self._announce(account_id=account_id)

    def remove(self, account_id: str) -> dict[str, Any]:
        """A permanent hide: the token is revoked and deleted, every row stays."""
        if self.registry.get(account_id) is None:
            raise KeyError(f"no such calendar account: {account_id}")
        revoked = self.auth_for(account_id).forget()
        self.registry.remove(account_id)
        self._hidden(account_id)
        self._secrets.delete(LEGACY_NAME_SECRET)
        self._announce(account_id=account_id)
        return {"revoked": revoked, "revoke_by_hand": None if revoked else REVOKE_BY_HAND}

    def remove_all(self) -> dict[str, Any]:
        self.cancel()
        revoked = True
        for account in self.registry.accounts():
            revoked = self.remove(account.id)["revoked"] and revoked
        self._google.client_rejected = False
        with self._lock:
            self._error = None
        return {"revoked": revoked, "revoke_by_hand": None if revoked else REVOKE_BY_HAND}

    def _hidden(self, account_id: str) -> None:
        for callback in self.on_hidden:
            try:
                callback(account_id)
            except Exception as exc:  # advisory
                log.warning("calendar: hiding %s: %s", account_id, exc)

    # ------------------------------------------------------------------ status

    def status(self) -> dict[str, Any]:
        self._expire_pending()
        with self._lock:
            pending = self._pending
            error = self._error
            finishing = self._finishing
        accounts = []
        for account in self.registry.accounts():
            auth = self.auth_for(account.id)
            accounts.append(
                {
                    "id": account.id,
                    "address": account.address,
                    "state": "connected" if account.id in self._pinned else auth.state(),
                    "visible": account.visible,
                    "color": account.color,
                    "error": auth.error,
                }
            )
        live = [a for a in accounts if a["state"] == "connected"]
        if pending is not None or finishing:
            state = "connecting"
        elif live:
            state = "connected"
        elif accounts:
            # Accounts are remembered but no token works: revoked or expired.
            state = "reconnect"
        else:
            state = "disconnected"
        if error is None and not live:
            error = next((str(a["error"]) for a in accounts if a["error"]), None)
        first = live[0] if live else (accounts[0] if accounts else None)
        return {
            "configured": self.client() is not None,
            "state": state,
            "account": first["address"] if first else None,
            "auth_url": pending.auth_url if pending else None,
            "connecting": {"auth_url": pending.auth_url} if pending else None,
            "error": error,
            "scope": SCOPE,
            "accounts": accounts,
        }

    # ------------------------------------------------------------------ connect

    def start(self, *, reconnect: str | None = None) -> str:
        """Open the listener and return the URL the browser should visit."""
        client = self.client()
        if client is None:
            raise CalendarAuthError("this build of Upshot has no Google client configured")
        hint = None
        if reconnect is not None:
            account = self.registry.get(reconnect)
            if account is None or account.removed:
                raise KeyError(f"no such calendar account: {reconnect}")
            hint = account.address
        self.cancel()
        server = HTTPServer(("127.0.0.1", 0), _CallbackHandler)
        server.timeout = 0.5
        port = server.server_address[1]
        redirect_uri = f"http://127.0.0.1:{port}"
        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(24)
        pending = _Pending(
            state=state,
            verifier=verifier,
            redirect_uri=redirect_uri,
            auth_url=authorization_url(client, redirect_uri, challenge, state, hint),
            deadline=self._now() + self._connect_timeout_s,
            server=server,
            reconnecting=reconnect,
        )
        server.auth = self  # type: ignore[attr-defined]
        server.pending = pending  # type: ignore[attr-defined]
        server.finished = False  # type: ignore[attr-defined]
        with self._lock:
            self._pending = pending
            self._error = None
        threading.Thread(
            target=self._serve, args=(pending,), name="gcal-callback", daemon=True
        ).start()
        log.info("google connect: waiting on %s", redirect_uri)
        self._announce()
        return pending.auth_url

    def cancel(self) -> None:
        with self._lock:
            pending, self._pending = self._pending, None
        if pending is not None:
            pending.server.finished = True  # type: ignore[attr-defined]
            # Announce it, as start(), complete() and the timeout all do. Without
            # this a cancel was invisible to every view except the one that asked
            # for it: a second window kept offering to cancel a connection that had
            # already been dropped.
            self._announce()

    def _serve(self, pending: _Pending) -> None:
        server = pending.server
        try:
            while not server.finished and self._now() < pending.deadline:  # type: ignore[attr-defined]
                server.handle_request()
        finally:
            server.server_close()
            with self._lock:
                timed_out = self._pending is pending
                if timed_out:
                    self._pending = None
                    self._error = "Nothing came back from Google in time. Connect again."
            if timed_out:
                self._announce()

    def _expire_pending(self) -> None:
        with self._lock:
            pending = self._pending
        if pending is not None and self._now() >= pending.deadline:
            self.cancel()
            with self._lock:
                self._error = "Nothing came back from Google in time. Connect again."

    def complete(self, pending: _Pending, params: dict[str, str]) -> tuple[bool, str]:
        """Finish a connection from the redirect's query. Returns (ok, message)."""
        with self._lock:
            current = self._pending is pending
            if current:
                self._pending = None
                self._finishing = True
        if not current:
            return False, "This sign-in has already finished. Go back to Upshot."
        connected: tuple[str, bool] | None = None
        try:
            ok, message, connected = self._finish(pending, params)
        finally:
            with self._lock:
                self._finishing = False
        with self._lock:
            self._error = None if ok else message
        if connected is not None:
            self._announce(account_id=connected[0], restored=connected[1])
        else:
            self._announce()
        if self._on_complete is not None:
            try:
                self._on_complete(ok)
            except Exception as exc:  # advisory: the connection itself is done
                log.info("could not bring Upshot back after the sign-in: %s", exc)
        return ok, message

    def _finish(
        self, pending: _Pending, params: dict[str, str]
    ) -> tuple[bool, str, tuple[str, bool] | None]:
        if not secrets.compare_digest(params.get("state", ""), pending.state):
            log.warning("google connect: state mismatch; refusing the callback")
            return False, "This sign-in link did not come from this Upshot. Connect again.", None
        if params.get("error"):
            if params["error"] == "access_denied":
                return False, "Access was not granted, so nothing was connected.", None
            return False, f"Google refused the sign-in ({params['error']}).", None
        code = params.get("code")
        if not code or self.client() is None:
            return False, "Google did not send a sign-in code. Connect again.", None
        try:
            token = self._google.token(
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "code_verifier": pending.verifier,
                    "redirect_uri": pending.redirect_uri,
                }
            )
        except CalendarUnavailable as exc:
            return False, f"Could not reach Google to finish connecting: {exc}", None
        except CalendarAuthError as exc:
            return False, str(exc), None
        except _TokenError as exc:
            if self._google.client_rejected:
                return False, "Google rejected this build's client. Connect again.", None
            return False, f"Google refused the token request ({exc.code})", None
        access = str(token.get("access_token", ""))
        # Google's consent screen lets the user untick a scope and still press Continue.
        if SCOPE not in str(token.get("scope", "")).split():
            self._google.revoke(access)
            return (
                False,
                "Upshot was not given permission to see calendar events. Connect again, "
                "and leave that box ticked.",
                None,
            )
        refresh = token.get("refresh_token")
        if not refresh:
            return False, "Google did not return a lasting token. Connect again.", None
        self._google.client_rejected = False
        address = self._google.address(access)
        if not address:
            # Without the address there is no telling which account this is, and so no
            # knowing whose history it is. Nothing is kept.
            self._google.revoke(str(refresh))
            return False, "Upshot could not tell which Google account this is. Try again.", None
        account, restored = self.registry.add_or_restore(address)
        if pending.reconnecting and pending.reconnecting != account.id:
            log.info("google connect: a reconnect chose another account; that one is added")
        self.auth_for(account.id).remember(str(refresh), token)
        self._secrets.delete(LEGACY_NAME_SECRET)
        log.info("google calendar connected: %s%s", account.id, " (restored)" if restored else "")
        return True, "Upshot is connected to your Google Calendar.", (account.id, restored)

    def close(self) -> None:
        self.cancel()
        self._google.http.close()

    def _announce(self, **extra: Any) -> None:
        if self._publish is None:
            return
        try:
            self._publish(state=self.status()["state"], **extra)
        except Exception as exc:  # an event is advisory; never let it break the flow
            log.warning("calendar event not published: %s", exc)


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


# --------------------------------------------------------------------------- listener

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Upshot</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{{font:16px/1.5 system-ui,sans-serif;margin:0;min-height:100vh;display:grid;
place-items:center;background:#f6f7f9;color:#111}}
main{{max-width:28rem;padding:2rem;text-align:center}}
h1{{font-size:1.25rem;margin:0 0 .5rem}} p{{margin:.25rem 0;color:#444}}
a{{color:#2451d6}}
@media (prefers-color-scheme:dark){{body{{background:#0b0f19;color:#eee}}p{{color:#bbb}}
a{{color:#8fb0ff}}}}
</style></head>
<body><main><h1>{title}</h1><p>{message}</p><p>{next}</p></main>
<script>setTimeout(function(){{window.close();}},1500);</script></body></html>
"""


class _CallbackHandler(BaseHTTPRequestHandler):
    """Answers Google's redirect. Anything without a ``state`` is not the redirect."""

    server: HTTPServer

    def do_GET(self) -> None:
        query = {key: values[0] for key, values in parse_qs(urlparse(self.path).query).items()}
        if "state" not in query:
            self.send_error(404)  # a favicon, a probe: not ours, keep waiting
            return
        auth: CalendarAccounts = self.server.auth  # type: ignore[attr-defined]
        pending: _Pending = self.server.pending  # type: ignore[attr-defined]
        self.server.finished = True  # type: ignore[attr-defined]  # exactly one callback
        ok, message = auth.complete(pending, query)
        # No link back: Upshot brings its own window forward (on_complete). A link to the
        # app opened it as a tab here, and an upshot: link made Chrome ask "Open Upshot?"
        # (machine B, 2026-09-30). The page tries to close itself; Chrome allows that
        # only for some tabs, so it also says the tab can be closed.
        page = PAGE.format(
            title="Connected" if ok else "Not connected",
            message=html.escape(message),
            next="Upshot is back in front. You can close this tab.",
        ).encode("utf-8")
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.send_header("Cache-Control", "no-store")
        # The URL held a one-time code. Don't pass it on to anything the page links to.
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(page)

    def log_message(self, format: str, *args: Any) -> None:
        # The default writes the request line — the authorization code — to stderr.
        return
