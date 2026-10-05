"""``/api/updates``: the next version of the app, for Settings and the tray (D87)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.updates.service import UpdateService

router = APIRouter(prefix="/api/updates")


def updates_of(request: Request) -> UpdateService:
    updates = request.app.state.services.updates
    if updates is None:
        raise HTTPException(503, "updates are not available in this build")
    return updates  # type: ignore[no-any-return]


@router.get("")
def update_state(request: Request) -> dict[str, Any]:
    """What is installed, what is available, and how far its download has got."""
    return updates_of(request).state()


@router.post("/check")
def check_now(request: Request) -> dict[str, Any]:
    """The "Check now" button. Answers at once; progress arrives as ``updates`` events."""
    return updates_of(request).check_in_background()
