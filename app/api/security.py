"""Local-UI security (SECURITY-AND-AUTH.md §9, TECHNICAL-DESIGN.md §16).

Middleware order is load-bearing and asserted: **Host check → auth → CSRF → routing.**
"""

from __future__ import annotations

import contextlib
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.log import get

log = get(__name__)

SESSION_COOKIE = "up_session"
CSRF_COOKIE = "up_csrf"
CSRF_HEADER = "x-csrf-token"
MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
#: Where a fresh one-time link comes from. Both are things only the person at this
#: computer can reach, which is the point: a link is proof the app handed it out.
FRESH_LINK_HINT = (
    "choose Open dashboard from the Upshot tray icon, or press N in the Upshot launcher window"
)
NO_COOKIE_MESSAGE = f"this browser is not authorized yet: {FRESH_LINK_HINT}"

#: Asked for by the launcher, which cannot open the tray menu. It proves itself with the
#: key the app wrote to its own home folder, readable by this Windows user only.
LINK_PATH = "/api/auth/link"
LAUNCHER_HEADER = "x-upshot-launcher"
LAUNCHER_KEY_FILE = "launcher.key"


@dataclass
class AuthState:
    """A per-install secret plus the one-time tokens the tray hands the browser."""

    session_secret: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    csrf_secret: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    launcher_key: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    _tokens: set[str] = field(default_factory=set)
    _used: set[str] = field(default_factory=set)

    def issue_token(self) -> str:
        token = secrets.token_urlsafe(24)
        self._tokens.add(token)
        return token

    def register_token(self, token: str) -> str:
        """Adopt a token minted elsewhere — the tray, or the e2e launcher."""
        self._tokens.add(token)
        return token

    def redeem(self, token: str) -> bool:
        if token in self._used or token not in self._tokens:
            return False
        self._tokens.discard(token)
        self._used.add(token)
        return True

    def was_issued_here(self, token: str) -> bool:
        """False for a token this process never minted — which usually means the link
        came from a *different* Upshot answering on the same port."""
        return token in self._tokens or token in self._used

    def link(self, port: int) -> str:
        """A fresh one-time link. Every browser profile needs its own, once."""
        return f"http://127.0.0.1:{port}/?k={self.issue_token()}"

    def valid_launcher(self, value: str | None) -> bool:
        return bool(value) and secrets.compare_digest(str(value), self.launcher_key)

    def valid_session(self, value: str | None) -> bool:
        return bool(value) and secrets.compare_digest(str(value), self.session_secret)

    def valid_csrf(self, value: str | None) -> bool:
        return bool(value) and secrets.compare_digest(str(value), self.csrf_secret)

    def apply(self, response: Response) -> Response:
        response.set_cookie(
            SESSION_COOKIE,
            self.session_secret,
            httponly=True,
            samesite="strict",
            max_age=365 * 24 * 3600,
            path="/",
        )
        response.set_cookie(
            CSRF_COOKIE,
            self.csrf_secret,
            httponly=False,  # the UI must read it to echo it back
            samesite="strict",
            max_age=365 * 24 * 3600,
            path="/",
        )
        return response


def allowed_hosts(port: int) -> frozenset[str]:
    return frozenset(
        {
            "localhost",
            "127.0.0.1",
            f"localhost:{port}",
            f"127.0.0.1:{port}",
        }
    )


def _header(scope: Scope, name: str) -> str:
    target = name.lower().encode()
    for key, value in scope.get("headers", []):
        if key.lower() == target:
            return str(value.decode("latin-1"))
    return ""


def _cookies(scope: Scope) -> dict[str, str]:
    from http.cookies import SimpleCookie

    jar = SimpleCookie()
    jar.load(_header(scope, "cookie"))
    return {key: morsel.value for key, morsel in jar.items()}


def _query(scope: Scope, name: str) -> str | None:
    from urllib.parse import parse_qs

    values = parse_qs(scope.get("query_string", b"").decode("latin-1")).get(name)
    return values[0] if values else None


class HostHeaderMiddleware:
    """The DNS-rebinding defense. Pure ASGI, so it runs before routing *and* does not
    buffer the SSE stream the way ``BaseHTTPMiddleware`` does."""

    def __init__(self, app: ASGIApp, port: int = 8000) -> None:
        self.app = app
        self.allowed = allowed_hosts(port)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        host = _header(scope, "host").lower()
        if host not in self.allowed:
            log.warning("rejected request with Host: %r", host)
            response = JSONResponse({"detail": f"host {host!r} is not allowed"}, status_code=421)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


class AuthMiddleware:
    """Cookie auth. No cookie → 401 with the tray message; never a redirect."""

    def __init__(
        self,
        app: ASGIApp,
        auth: AuthState,
        exempt: frozenset[str] | None = None,
        port: int = 8000,
    ) -> None:
        self.app = app
        self.auth = auth
        self.exempt = exempt or frozenset({"/api/health"})
        self.port = port

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self.exempt:
            await self.app(scope, receive, send)
            return
        if scope.get("path") == LINK_PATH and scope.get("method") == "POST":
            # Answered here, before CSRF and routing: the launcher has no cookie and no
            # CSRF token, only the key. A browser cannot send the header cross-origin
            # without a preflight this server never approves.
            if self.auth.valid_launcher(_header(scope, LAUNCHER_HEADER)):
                response = JSONResponse({"url": self.auth.link(self.port)})
            else:
                response = JSONResponse({"detail": "not the launcher"}, 401)
            await response(scope, receive, send)
            return
        token = _query(scope, "k")
        if token:
            if self.auth.redeem(token):
                await self.app(scope, receive, self._with_cookies(send))
                return
            # A spent token must not lock out a browser that already holds a session.
            # The launcher opens the link itself, so the printed one gets clicked second
            # — same browser, cookie already set, and refusing it strands the user with
            # no way back in short of restarting the app. A stale token adds nothing to
            # a request that is already authorized, so ignore it and carry on.
            if self.auth.valid_session(_cookies(scope).get(SESSION_COOKIE)):
                await self.app(scope, receive, send)
                return
            host = _header(scope, "host") or "127.0.0.1:8000"
            if not self.auth.was_issued_here(token):
                log.warning("authorize link was not issued by this process: %s", host)
                detail = (
                    "this link was not issued by the app answering on this port — "
                    "another Upshot is probably already running here "
                    "(on WSL, a Linux instance shadows the Windows one). "
                    "Stop it, or start this one on a different port."
                )
            else:
                detail = (
                    "this link has already been used. Each link authorizes one browser, "
                    f"once. For a fresh one, {FRESH_LINK_HINT}."
                )
            await _refuse(scope, receive, send, detail)
            return
        if not self.auth.valid_session(_cookies(scope).get(SESSION_COOKIE)):
            await _refuse(scope, receive, send, NO_COOKIE_MESSAGE)
            return
        await self.app(scope, receive, send)

    def _with_cookies(self, send: Send) -> Send:
        auth = self.auth

        async def wrapped(message: Message) -> None:
            if message["type"] == "http.response.start":
                carrier = Response()
                auth.apply(carrier)
                headers = list(message.get("headers", []))
                for key, value in carrier.raw_headers:
                    if key.lower() == b"set-cookie":
                        headers.append((key, value))
                message = {**message, "headers": headers}
            await send(message)

        return wrapped


async def _refuse(scope: Scope, receive: Receive, send: Send, detail: str) -> None:
    """401, as a page when a person navigated here and as JSON for the UI's fetches.

    A browser opened on the app with the wrong profile used to show a line of raw JSON
    that named a tray the launcher does not have.
    """
    wants_page = (
        scope.get("method") == "GET"
        and not str(scope.get("path", "")).startswith("/api/")
        and "text/html" in _header(scope, "accept")
    )
    if not wants_page:
        await JSONResponse({"detail": detail}, 401)(scope, receive, send)
        return
    import html

    page = UNAUTHORIZED_PAGE.format(detail=html.escape(detail))
    await HTMLResponse(page, 401, headers={"Cache-Control": "no-store"})(scope, receive, send)


UNAUTHORIZED_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Upshot</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{{font:16px/1.55 system-ui,sans-serif;margin:0;min-height:100vh;display:grid;
place-items:center;background:#f6f7f9;color:#111}}
main{{max-width:32rem;padding:2rem}} h1{{font-size:1.25rem;margin:0 0 .75rem}}
p,li{{color:#444}} li{{margin:.35rem 0}} small{{color:#777}}
@media (prefers-color-scheme:dark){{body{{background:#0b0f19;color:#eee}}
p,li{{color:#bbb}} small{{color:#888}}}}
</style></head>
<body><main>
<h1>Authorize this browser</h1>
<p>Upshot only opens in a browser it has handed a one-time link to, so nothing else on
this computer can reach your meetings. Get a fresh link from Upshot itself:</p>
<ul>
<li>choose <b>Open dashboard</b> from the Upshot icon in the taskbar tray, or</li>
<li>press <b>N</b> in the Upshot launcher window, and paste the link it prints here.</li>
</ul>
<p>Each browser, or browser profile, needs this once.</p>
<p><small>{detail}</small></p>
</main></body></html>
"""


def write_launcher_key(auth: AuthState, home: Path) -> Path:
    """Leave the launcher key where only this Windows user can read it.

    The app home is under %LOCALAPPDATA%, private to the user, which is the same trust
    the log file holding the startup link already relies on. Rewritten on every start,
    so a key from an earlier run opens nothing.
    """
    path = home / LAUNCHER_KEY_FILE
    home.mkdir(parents=True, exist_ok=True)
    path.write_text(auth.launcher_key, encoding="utf-8")
    with contextlib.suppress(OSError):
        path.chmod(0o600)
    return path


class CsrfMiddleware:
    """Double-submit token on every mutating route; SameSite=Strict is the primary defense."""

    def __init__(self, app: ASGIApp, auth: AuthState) -> None:
        self.app = app
        self.auth = auth

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope.get("method") in MUTATING
            and not self.auth.valid_csrf(_header(scope, CSRF_HEADER))
        ):
            await JSONResponse({"detail": "missing or invalid CSRF token"}, 403)(
                scope, receive, send
            )
            return
        await self.app(scope, receive, send)
