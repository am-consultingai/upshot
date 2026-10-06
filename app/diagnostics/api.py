"""``/api/diagnostics``: whether crash reports can be sent, the user's answer, and the
last report sent, so anyone can see exactly what left the machine (D87).

The answer itself is a setting (``diagnostics.crash_reports``), saved through
``PUT /api/settings`` like every other.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/diagnostics")


class ClientError(BaseModel):
    """What the page posts when it catches an error: no more than this, and bounded."""

    kind: str = Field(max_length=40)
    message: str = Field(default="", max_length=2000)
    stack: str = Field(default="", max_length=20000)


@router.get("")
def diagnostics_state(request: Request) -> dict[str, Any]:
    reporter = request.app.state.services.reporter
    if reporter is None:
        raise HTTPException(503, "crash reports are not available in this build")
    return {
        "available": reporter.available,
        "consent": reporter.consent,
        "last_report": reporter.last_report(),
    }


@router.post("/client-error")
def client_error(request: Request, body: ClientError) -> dict[str, Any]:
    """An error the interface caught (C5). Reported only with consent; always answers."""
    reporter = request.app.state.services.reporter
    sent = None
    if reporter is not None:
        sent = reporter.report_client(kind=body.kind, message=body.message, stack=body.stack)
    return {"reported": sent is not None}
