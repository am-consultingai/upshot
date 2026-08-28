"""Local-UI security (SECURITY-AND-AUTH.md §9, TECHNICAL-DESIGN.md §16).

Middleware order is load-bearing and asserted: **Host check → auth → CSRF → routing.**
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field

from fastapi.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.log import get

log = get(__name__)

SESSION_COOKIE = "ma_session"
CSRF_COOKIE = "ma_csrf"
CSRF_HEADER = "x-csrf-token"
MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
NO_COOKIE_MESSAGE = "open Meeting Agent from the tray to authorize this browser"


@dataclass
class AuthState:
    """A per-install secret plus the one-time tokens the tray hands the browser."""

    session_secret: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    csrf_secret: str = field(default_factory=lambda: secrets.token_urlsafe(24))
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

    def __init__(self, app: ASGIApp, auth: AuthState, exempt: frozenset[str] | None = None) -> None:
        self.app = app
        self.auth = auth
        self.exempt = exempt or frozenset({"/api/health"})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self.exempt:
            await self.app(scope, receive, send)
            return
        token = _query(scope, "k")
        if token:
            if not self.auth.redeem(token):
                await JSONResponse({"detail": "this link has already been used"}, 401)(
                    scope, receive, send
                )
                return
            await self.app(scope, receive, self._with_cookies(send))
            return
        if not self.auth.valid_session(_cookies(scope).get(SESSION_COOKIE)):
            await JSONResponse({"detail": NO_COOKIE_MESSAGE}, 401)(scope, receive, send)
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
