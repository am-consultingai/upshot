"""Composition root: config → db → services → FastAPI app → threads."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
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
<html lang="en"><head><meta charset="utf-8"><title>Upshot</title></head>
<body><h1>Upshot</h1>
<p>The web UI has not been built yet. Run <code>npm run build</code> in <code>frontend/</code>.</p>
<p><a href="/api/status">/api/status</a></p></body></html>
"""


def test_mode() -> bool:
    return os.environ.get("UP_TEST_MODE") == "1"


def frontend_dir() -> Any:
    return paths.resource("frontend", "dist")


def shell(path: Path) -> Response:
    """The SPA shell, explicitly not cached.

    `FileResponse` sends an etag and a last-modified date but no `Cache-Control`,
    and a browser given no instruction is free to reuse an HTML document without
    asking. That is fine for a page; it is not fine for this page, because this
    page is the only thing that names the hashed asset files. A stale shell keeps
    requesting the bundle it was built against, so a rebuilt application goes on
    looking exactly like the old one until someone thinks to hard-refresh — which
    is not a thing to ask of anyone, and would happen on every future update.

    `no-cache` rather than `no-store`: the browser still holds the file and the
    etag still answers most requests with a 304, it simply has to ask first.
    The assets themselves are content-hashed, so they stay cacheable forever.
    """
    return FileResponse(path, headers={"Cache-Control": "no-cache, must-revalidate"})


def create_app(services: Services | None = None, *, config: Config | None = None) -> FastAPI:
    svc = services or build(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        import asyncio

        svc.events.bind_loop(asyncio.get_running_loop())
        svc.queue.reset_running()
        yield

    app = FastAPI(
        title="Upshot",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.state.services = svc
    app.include_router(router)
    if test_mode():
        app.include_router(test_router())
        log.warning("UP_TEST_MODE=1 — the seed route is mounted")

    @app.get("/", response_class=HTMLResponse)
    def index() -> Response:
        candidate = frontend_dir() / "index.html"
        if candidate.exists():
            return shell(candidate)
        return HTMLResponse(INDEX_FALLBACK)

    @app.exception_handler(KeyError)
    async def _missing(_request: Any, exc: KeyError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404)

    dist = frontend_dir()
    if (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=str(dist / "assets")), name="assets")

    # Vite copies public/ to the dist *root*, not into assets/, so without these
    # the icons fall through to the SPA route below and the browser is handed an
    # HTML document where it asked for an image — which it shows as no icon at all.
    for name, media in (("favicon.svg", "image/svg+xml"), ("favicon.ico", "image/x-icon")):

        def serve_root_file(_name: str = name, _media: str = media) -> Response:
            candidate = frontend_dir() / _name
            if not candidate.exists():  # a source checkout with no build yet
                return Response(status_code=404)
            return FileResponse(candidate, media_type=_media)

        app.get(f"/{name}", include_in_schema=False)(serve_root_file)

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
            return shell(candidate)
        return HTMLResponse(INDEX_FALLBACK)

    # Middleware runs in reverse registration order, so this registers
    # CSRF, then auth, then the host check — and the host check runs first.
    app.add_middleware(CsrfMiddleware, auth=svc.auth)
    app.add_middleware(AuthMiddleware, auth=svc.auth, port=svc.config.server_port)
    app.add_middleware(HostHeaderMiddleware, port=svc.config.server_port)
    # No CORS middleware at all: no Access-Control-Allow-Origin on any response.
    return app


def start_background(services: Services) -> None:
    """The worker and the detector. Without these the app records and then sits there."""
    # Said once, by name. A launcher that exports UP_DETECTION__MODE beats app_config.json
    # on every start, so a setting saved from the screen came back changed and the log
    # gave no hint why. Now the log names every key the environment is holding.
    pinned = services.config.env_pinned()
    if pinned:
        log.info(
            "the environment is holding %s — settings changed in the app will not "
            "survive a restart while it does",
            ", ".join(f"{key} ({var})" for key, var in pinned.items()),
        )
    if services.worker is not None:
        services.worker.start()
        log.info("worker started (policy %s)", services.worker.policy)
    detector = services.detector
    if detector is not None:
        # Started even when detection is off: the loop reads the mode every second, so
        # the setting can be changed from Settings while the app runs.
        detector.start()
        log.info("detector watching in %s mode", services.config.get("detection.mode"))
    # Idle until a Google account is connected; see app/gcal/sync.py.
    if services.calendar_sync is not None:
        services.calendar_sync.start()


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - process entry point
    """Headless: the server, the worker and the detector, without the tray icon.

        python -m app.main

    The tray entry point (``python -m app.tray``) is this plus the icon, and is what the
    installer runs. Use this one on a machine with no desktop, or to see the log.
    """
    setup()
    services = build()
    app = create_app(services)
    from app.server import LocalServer

    server = LocalServer(app, host=services.config.server_host, port=services.config.server_port)
    start_background(services)
    from app import paths
    from app.api.security import write_launcher_key

    # The launcher asks for fresh links with this; see AuthMiddleware and LINK_PATH.
    write_launcher_key(services.auth, paths.app_home())
    url = services.auth.link(services.config.server_port)
    log.info("open %s", url)
    print(f"\n  Upshot is running. Open this once to authorize the browser:\n\n  {url}\n")
    try:
        server.run()
    except KeyboardInterrupt:
        log.info("shutting down")
    finally:
        services.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
