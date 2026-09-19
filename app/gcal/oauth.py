"""Connecting a Google account: loopback OAuth with PKCE (Calendar 1, z8tj1h8jrg).

The flow, once per connection:

1. ``start`` makes a PKCE verifier and a ``state``, opens a one-shot listener on
   ``127.0.0.1:<ephemeral>``, and returns Google's consent URL. The UI opens it in the
   user's own browser — never an embedded view, which Google blocks.
2. Google redirects the browser to the listener. The listener takes the first request
   that carries a ``state``, checks it, exchanges the code, answers with a page saying
   the tab can be closed, and stops.
3. The refresh token goes to the OS credential store (``SecretStore``), never to
   ``app_config.json``. Access tokens live in memory only and are refreshed on demand.

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

REFRESH_SECRET = "google_refresh_token"
#: The connected account's address, for Settings to show. It is not a credential, but it
#: is personal, so it stays beside the token rather than in the config file.
ACCOUNT_SECRET = "google_account"

#: How long the listener waits for the browser to come back.
CONNECT_TIMEOUT_S = 300.0
#: Refresh this long before Google's stated expiry, so a token never dies mid-request.
EXPIRY_MARGIN_S = 60.0
HTTP_TIMEOUT_S = 10.0

REVOKE_BY_HAND = "https://myaccount.google.com/connections"


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


def authorization_url(client: OAuthClient, redirect_uri: str, challenge: str, state: str) -> str:
    query = {
        "client_id": client.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        # A refresh token, and a fresh one on every connect: without prompt=consent
        # Google omits it for an account that has granted access before.
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{AUTH_URL}?{urlencode(query)}"


@dataclass
class _Pending:
    state: str
    verifier: str
    redirect_uri: str
    auth_url: str
    deadline: float
    server: HTTPServer


class CalendarAuth:
    """The one Google connection this installation has."""

    def __init__(
        self,
        secrets_store: SecretStore,
        *,
        client_loader: Callable[[], OAuthClient | None] = load,
        http: httpx.Client | None = None,
        publish: Callable[..., Any] | None = None,
        app_url: str | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        connect_timeout_s: float = CONNECT_TIMEOUT_S,
    ) -> None:
        self._secrets = secrets_store
        self._client_loader = client_loader
        self._http = http or httpx.Client(timeout=HTTP_TIMEOUT_S)
        self._publish = publish
        self._app_url = app_url
        self._now = monotonic
        self._connect_timeout_s = connect_timeout_s
        self._lock = threading.Lock()
        self._pending: _Pending | None = None
        self._access: tuple[str, float] | None = None  # token, expires at (monotonic)
        self._error: str | None = None
        #: Set when Google rejects the client itself. Every refresh would fail the same
        #: way, so nothing is sent until the user connects again.
        self._client_rejected = False

    # ------------------------------------------------------------------ status

    def client(self) -> OAuthClient | None:
        try:
            return self._client_loader()
        except (OSError, ValueError) as exc:
            log.warning("google client unusable: %s", exc)
            return None

    def status(self) -> dict[str, Any]:
        self._expire_pending()
        with self._lock:
            pending = self._pending
            error = self._error
        refresh = self._secrets.get(REFRESH_SECRET)
        account = self._secrets.get(ACCOUNT_SECRET)
        if pending is not None:
            state = "connecting"
        elif refresh and not self._client_rejected:
            state = "connected"
        elif account:
            # An account is remembered but its token is gone: it was revoked or expired.
            state = "reconnect"
        else:
            state = "disconnected"
        return {
            "configured": self.client() is not None,
            "state": state,
            "account": account,
            "auth_url": pending.auth_url if pending else None,
            "error": error,
            "scope": SCOPE,
        }

    # ------------------------------------------------------------------ connect

    def start(self) -> str:
        """Open the listener and return the URL the browser should visit."""
        client = self.client()
        if client is None:
            raise CalendarAuthError("this build of Upshot has no Google client configured")
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
            auth_url=authorization_url(client, redirect_uri, challenge, state),
            deadline=self._now() + self._connect_timeout_s,
            server=server,
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
        if not current:
            return False, "This sign-in has already finished. Go back to Upshot."
        ok, message = self._finish(pending, params)
        with self._lock:
            self._error = None if ok else message
        self._announce()
        return ok, message

    def _finish(self, pending: _Pending, params: dict[str, str]) -> tuple[bool, str]:
        if not secrets.compare_digest(params.get("state", ""), pending.state):
            log.warning("google connect: state mismatch; refusing the callback")
            return False, "This sign-in link did not come from this Upshot. Connect again."
        if params.get("error"):
            if params["error"] == "access_denied":
                return False, "Access was not granted, so nothing was connected."
            return False, f"Google refused the sign-in ({params['error']})."
        code = params.get("code")
        client = self.client()
        if not code or client is None:
            return False, "Google did not send a sign-in code. Connect again."
        try:
            token = self._token_request(
                client,
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "code_verifier": pending.verifier,
                    "redirect_uri": pending.redirect_uri,
                },
            )
        except CalendarUnavailable as exc:
            return False, f"Could not reach Google to finish connecting: {exc}"
        except CalendarAuthError as exc:
            return False, str(exc)
        # Google's consent screen lets the user untick a scope and still press Continue.
        if SCOPE not in str(token.get("scope", "")).split():
            self._revoke(str(token.get("access_token", "")))
            return False, (
                "Upshot was not given permission to see calendar events. Connect again, "
                "and leave that box ticked."
            )
        refresh = token.get("refresh_token")
        if not refresh:
            return False, "Google did not return a lasting token. Connect again."
        self._client_rejected = False
        self._remember_access(token)
        self._secrets.set(REFRESH_SECRET, str(refresh))
        account = self._account()
        if account:
            self._secrets.set(ACCOUNT_SECRET, account)
        log.info("google calendar connected")
        return True, "Upshot is connected to your Google Calendar."

    def _account(self) -> str | None:
        """The primary calendar's name, which for a Google account is its address.

        Read from the events endpoint so that no identity scope is needed on top of the
        calendar one. Not knowing it is not a failure.
        """
        try:
            body = self.get("/calendars/primary/events", {"maxResults": 1, "fields": "summary"})
        except (CalendarAuthError, CalendarUnavailable) as exc:
            log.warning("google connect: could not read the account name: %s", exc)
            return None
        summary = body.get("summary")
        return str(summary) if summary else None

    # ------------------------------------------------------------------ tokens

    def access_token(self) -> str:
        with self._lock:
            cached = self._access
        if cached and self._now() < cached[1]:
            return cached[0]
        if self._client_rejected:
            raise CalendarAuthError("Google rejected this build's client. Connect again.")
        refresh = self._secrets.get(REFRESH_SECRET)
        client = self.client()
        if not refresh or client is None:
            raise CalendarAuthError("Google Calendar is not connected")
        token = self._token_request(
            client, {"grant_type": "refresh_token", "refresh_token": refresh}
        )
        return self._remember_access(token)

    def _remember_access(self, token: dict[str, Any]) -> str:
        access = str(token["access_token"])
        # Monotonic, from Google's expires_in: a wrong wall clock cannot make a live
        # token look expired or an expired one look live.
        lifetime = float(token.get("expires_in", 3600))
        with self._lock:
            self._access = (access, self._now() + max(0.0, lifetime - EXPIRY_MARGIN_S))
        return access

    def _token_request(self, client: OAuthClient, form: dict[str, str]) -> dict[str, Any]:
        data = {"client_id": client.client_id, "client_secret": client.client_secret, **form}
        try:
            response = self._http.post(TOKEN_URL, data=data)
        except httpx.TransportError as exc:
            raise CalendarUnavailable(type(exc).__name__) from exc
        if response.status_code >= 500:
            raise CalendarUnavailable(f"Google answered {response.status_code}")
        body = _json(response)
        if response.status_code == 200 and "access_token" in body:
            return body
        error = str(body.get("error", response.status_code))
        if error == "invalid_grant" and form["grant_type"] == "refresh_token":
            self._drop_token("Google no longer accepts this connection. Connect again.")
            raise CalendarAuthError("the refresh token was revoked or has expired")
        if error in ("invalid_client", "unauthorized_client"):
            self._client_rejected = True
            with self._lock:
                self._error = "Google rejected this build's client. Connect again."
            self._announce()
            raise CalendarAuthError(f"Google rejected the client ({error})")
        raise CalendarAuthError(f"Google refused the token request ({error})")

    def _drop_token(self, reason: str) -> None:
        log.warning("google calendar: %s", reason)
        self._secrets.delete(REFRESH_SECRET)
        with self._lock:
            self._access = None
            self._error = reason
        self._announce()

    # ------------------------------------------------------------------ calls

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET a Calendar API path, refreshing once if the access token was refused."""
        for attempt in range(2):
            token = self.access_token()
            try:
                response = self._http.get(
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

    # ------------------------------------------------------------------ disconnect

    def disconnect(self) -> dict[str, Any]:
        """Revoke at Google, then forget locally — both, whatever the first one says."""
        self.cancel()
        refresh = self._secrets.get(REFRESH_SECRET)
        with self._lock:
            access = self._access[0] if self._access else None
        revoked = self._revoke(refresh or access or "") if (refresh or access) else True
        self._secrets.delete(REFRESH_SECRET)
        self._secrets.delete(ACCOUNT_SECRET)
        with self._lock:
            self._access = None
            self._error = None
        self._client_rejected = False
        self._announce()
        return {"revoked": revoked, "revoke_by_hand": None if revoked else REVOKE_BY_HAND}

    def _revoke(self, token: str) -> bool:
        if not token:
            return False
        try:
            response = self._http.post(REVOKE_URL, data={"token": token})
        except httpx.TransportError as exc:
            log.warning("google revoke failed: %s", type(exc).__name__)
            return False
        # 400 invalid_token means Google had already forgotten it, which is the goal.
        return response.status_code in (200, 400)

    def close(self) -> None:
        self.cancel()
        self._http.close()

    def _announce(self) -> None:
        if self._publish is None:
            return
        try:
            self._publish(state=self.status()["state"])
        except Exception as exc:  # an event is advisory; never let it break the flow
            log.warning("calendar event not published: %s", exc)

    @property
    def app_url(self) -> str | None:
        return self._app_url


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
<body><main><h1>{title}</h1><p>{message}</p><p>{next}</p></main></body></html>
"""


class _CallbackHandler(BaseHTTPRequestHandler):
    """Answers Google's redirect. Anything without a ``state`` is not the redirect."""

    server: HTTPServer

    def do_GET(self) -> None:
        query = {key: values[0] for key, values in parse_qs(urlparse(self.path).query).items()}
        if "state" not in query:
            self.send_error(404)  # a favicon, a probe: not ours, keep waiting
            return
        auth: CalendarAuth = self.server.auth  # type: ignore[attr-defined]
        pending: _Pending = self.server.pending  # type: ignore[attr-defined]
        self.server.finished = True  # type: ignore[attr-defined]  # exactly one callback
        ok, message = auth.complete(pending, query)
        back = auth.app_url
        page = PAGE.format(
            title="Connected" if ok else "Not connected",
            message=html.escape(message),
            next=(
                f'You can close this tab, or <a href="{html.escape(back)}">go back to Upshot</a>.'
                if back
                else "You can close this tab."
            ),
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
