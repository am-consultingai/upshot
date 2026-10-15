"""``/api/legal``: the Terms the interface shows, the user's acceptance of them (D83), and
the third-party notices (every component the app ships, with its licence)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.legal import notices
from app.legal.terms import TermsService

router = APIRouter(prefix="/api/legal")


class AcceptPost(BaseModel):
    version: str


def terms_of(request: Request) -> TermsService:
    terms = request.app.state.services.terms
    if terms is None:
        raise HTTPException(503, "the Terms are not available in this build")
    return terms  # type: ignore[no-any-return]


@router.get("")
def legal_state(request: Request) -> dict[str, Any]:
    """Whether the Terms must be shown, and which version is in effect."""
    return terms_of(request).state()


@router.get("/terms")
def terms_document(request: Request, version: str | None = None) -> dict[str, Any]:
    """One version of the Terms, rendered: the one in effect unless ``version`` names another."""
    try:
        terms = terms_of(request).document(version)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {
        "version": terms.version,
        "effective": terms.effective,
        "material": terms.material,
        "summary": terms.summary,
        "title": terms.title,
        "html": terms.html(heading_level=3),
        "sha256": terms.sha256,
    }


@router.post("/accept")
def accept(request: Request, body: AcceptPost) -> dict[str, Any]:
    try:
        return terms_of(request).accept(body.version, via="app")
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/decline")
def decline() -> dict[str, Any]:
    """Not accepting the Terms ends the session: the app quits, as the tray's Quit does.

    False where nothing listens for the request (from source, or off Windows); the screen
    then says to close Upshot instead.
    """
    from app.instance import request_quit

    return {"quitting": request_quit()}


@router.post("/check")
def check(request: Request) -> dict[str, Any]:
    """Look for a newer version now, as the daily check does."""
    terms = terms_of(request)
    fetched = terms.check_now()
    return {"fetched": fetched, "error": terms.last_error, **terms.state()}


@router.get("/notices")
def notices_list() -> dict[str, Any]:
    """Every third-party component and its licence, without the licence texts."""
    return notices.summary()


@router.get("/notices/{index}")
def notice_texts(index: int) -> dict[str, Any]:
    """One component's licence and notice texts."""
    try:
        return {"id": index, "texts": notices.texts(index)}
    except IndexError as exc:
        raise HTTPException(404, f"no notice {index}") from exc
