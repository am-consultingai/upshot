"""Composition root: config → db → services → FastAPI app → threads."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app import paths
from app.api.routes import router, test_router
from app.api.security import AuthMiddleware, CsrfMiddleware, HostHeaderMiddleware
from app.config import Config
from app.log import get, setup
from app.services import Services, build

log = get(__name__)

INDEX_FALLBACK = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Meeting Agent</title></head>
<body><h1>Meeting Agent</h1>
<p>The web UI has not been built yet. Run <code>npm run build</code> in <code>frontend/</code>.</p>
<p><a href="/api/status">/api/status</a></p></body></html>
"""


def test_mode() -> bool:
    return os.environ.get("MA_TEST_MODE") == "1"


def frontend_dir() -> Any:
    return paths.resource("frontend", "dist")


def create_app(services: Services | None = None, *, config: Config | None = None) -> FastAPI:
    svc = services or build(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        import asyncio

        svc.events.bind_loop(asyncio.get_running_loop())
        svc.queue.reset_running()
        yield

    app = FastAPI(
        title="Meeting Agent",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.state.services = svc
    app.include_router(router)
    if test_mode():
        app.include_router(test_router())
        log.warning("MA_TEST_MODE=1 — the seed route is mounted")

    @app.get("/", response_class=HTMLResponse)
    def index() -> Response:
        candidate = frontend_dir() / "index.html"
        if candidate.exists():
            return FileResponse(candidate)
        return HTMLResponse(INDEX_FALLBACK)

    @app.exception_handler(KeyError)
    async def _missing(_request: Any, exc: KeyError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404)

    dist = frontend_dir()
    if (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=str(dist / "assets")), name="assets")

    @app.api_route(
        "/api/{rest:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    def api_not_found(rest: str) -> JSONResponse:
        """An unknown API path is a 404 for every method — never the SPA shell."""
        return JSONResponse({"detail": f"no such route: /api/{rest}"}, status_code=404)

    @app.get("/{full_path:path}", response_class=HTMLResponse, include_in_schema=False)
    def spa(full_path: str) -> Response:
        """Client-side routes are deep-linkable: /m/<id> from a toast must open the app.

        Registered last, so every real route and the /assets mount still win.
        """
        candidate = frontend_dir() / "index.html"
        if candidate.exists():
            return FileResponse(candidate)
        return HTMLResponse(INDEX_FALLBACK)

    # Middleware runs in reverse registration order, so this registers
    # CSRF, then auth, then the host check — and the host check runs first.
    app.add_middleware(CsrfMiddleware, auth=svc.auth)
    app.add_middleware(AuthMiddleware, auth=svc.auth)
    app.add_middleware(HostHeaderMiddleware, port=svc.config.server_port)
    # No CORS middleware at all: no Access-Control-Allow-Origin on any response.
    return app


def main() -> int:  # pragma: no cover - process entry point
    setup()
    services = build()
    app = create_app(services)
    from app.server import LocalServer

    server = LocalServer(app, host=services.config.server_host, port=services.config.server_port)
    token = services.auth.issue_token()
    log.info("open http://127.0.0.1:%d/?k=%s", services.config.server_port, token)
    server.run()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
