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
    """What is installed, what is available, how far its download has got, and what an
    install waits for."""
    state = updates_of(request).state()
    installer = request.app.state.services.installer
    if installer is not None:
        ready = installer.updates.ready() is not None
        state["install"] = {
            "can_install": installer.can_install,
            "waiting_for": installer.why_not_now() if ready else None,
            "last": installer.outcome,
        }
    return state


@router.post("/install")
def install_now(request: Request) -> dict[str, Any]:
    """For "Install now" and "Restart to update". The app quits; the installer restarts it."""
    installer = request.app.state.services.installer
    if installer is None:
        raise HTTPException(503, "updates are not available in this build")
    try:
        return installer.install_now()  # type: ignore[no-any-return]
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/check")
def check_now(request: Request) -> dict[str, Any]:
    """The "Check now" button. Answers at once; progress arrives as ``updates`` events."""
    return updates_of(request).check_in_background()
