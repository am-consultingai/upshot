"""``/api/diagnostics``: whether crash reports can be sent, the user's answer, and the
last report sent, so anyone can see exactly what left the machine (D87).

The answer itself is a setting (``diagnostics.crash_reports``), saved through
``PUT /api/settings`` like every other.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/diagnostics")


class ClientError(BaseModel):
    """What the page posts when it catches an error: no more than this, and bounded."""

    kind: str = Field(max_length=40)
    message: str = Field(default="", max_length=2000)
    stack: str = Field(default="", max_length=20000)


class SetupFailure(BaseModel):
    """An install or sign-in in setup that the page gave up on, and why."""

    provider: str = Field(max_length=40)
    stage: Literal["install", "signin", "test"]
    #: What the page saw: the window closed, the wait ran out, the test prompt failed, ….
    reason: str = Field(max_length=40, pattern=r"^[a-z-]+$")


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


#: Reasons the page gives when no run began, so there is no log of it to send.
NO_RUN = frozenset({"refused", "not-launched"})


@router.post("/setup-failure")
def setup_failure(request: Request, body: SetupFailure) -> dict[str, Any]:
    """Setup could not install or sign in to an AI CLI. Reported only with consent.

    The page knows that it failed; the log the run wrote says why, and only the scrubbed
    last lines of an install's or a sign-in's log go with it (``scrub.log_tail``).
    """
    from app.api.routes import CLI_PROVIDERS
    from app.diagnostics.scrub import log_tail
    from app.llm.console_log import log_path

    product = CLI_PROVIDERS.get(body.provider)
    if product is None:
        raise HTTPException(400, f"unknown provider {body.provider!r}")
    reporter = request.app.state.services.reporter
    sent = None
    if reporter is not None:
        short = body.provider.removesuffix("-subscription")
        # A test prompt's log is not this one's; a run refused or never launched wrote
        # nothing, and the log's last run would be an older one.
        ran = body.stage != "test" and body.reason not in NO_RUN
        lines = log_tail(log_path(f"{short}-{body.stage}")) if ran else []
        sent = reporter.report_setup_failure(
            product=product, stage=body.stage, reason=body.reason, log=lines
        )
    return {"reported": sent is not None}
