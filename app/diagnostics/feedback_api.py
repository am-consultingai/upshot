"""``/api/feedback``: feedback from inside the app, previewed before it is sent (D87, D1-D3).

The screenshot is captured on request and kept here, so the picture the user previewed
is the one that is sent (and nothing is captured that was not shown). A summary rating
is kept with the meeting too, so the page shows what the user chose.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app import meta
from app.diagnostics.feedback import Feedback, FeedbackError, FeedbackSender, transcript_size
from app.pipeline.stages.render import read_summary

router = APIRouter(prefix="/api/feedback")
SCREENSHOT = "feedback_screenshot"


class FeedbackPost(BaseModel):
    kind: str = Field(max_length=20)
    message: str = Field(default="", max_length=5000)
    email: str = Field(default="", max_length=320)
    details: bool = True
    screenshot: bool = False


class RatingPost(BaseModel):
    rating: str = Field(max_length=4)
    comment: str = Field(default="", max_length=5000)
    email: str = Field(default="", max_length=320)
    include_summary: bool = False


def sender_of(request: Request) -> FeedbackSender:
    sender = request.app.state.services.feedback
    if sender is None:
        raise HTTPException(503, "feedback is not available in this build")
    return sender  # type: ignore[no-any-return]


def _feedback(request: Request, body: FeedbackPost) -> Feedback:
    extras = request.app.state.services.extras
    shot = extras.get(SCREENSHOT) if body.screenshot else None
    return Feedback(
        kind=body.kind,
        message=body.message,
        email=body.email,
        details=body.details,
        screenshot=shot,
    )


@router.get("")
def feedback_state(request: Request) -> dict[str, Any]:
    return {"available": sender_of(request).available}


@router.post("/screenshot")
def take_screenshot(request: Request) -> Response:
    """Upshot's window, now; kept until sent or removed (D2)."""
    from app.diagnostics import screenshot

    png = screenshot.capture()
    if not png:
        raise HTTPException(404, "Upshot's window could not be captured")
    request.app.state.services.extras[SCREENSHOT] = png
    return Response(png, media_type="image/png")


@router.delete("/screenshot")
def drop_screenshot(request: Request) -> dict[str, bool]:
    request.app.state.services.extras.pop(SCREENSHOT, None)
    return {"removed": True}


@router.post("/preview")
def preview(request: Request, body: FeedbackPost) -> dict[str, Any]:
    """Exactly what Send would send."""
    try:
        return sender_of(request).preview(_feedback(request, body))
    except FeedbackError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("")
def send(request: Request, body: FeedbackPost) -> dict[str, Any]:
    try:
        result = sender_of(request).send(_feedback(request, body))
    except FeedbackError as exc:
        raise HTTPException(422, str(exc)) from exc
    request.app.state.services.extras.pop(SCREENSHOT, None)
    return result


# ---------------------------------------------------------------------- summary ratings (D3)


def _rating(request: Request, meeting_id: str, body: RatingPost) -> Feedback:
    meeting = request.app.state.services.dao.require_meeting(meeting_id)
    folder = meeting.path
    recorded = meta.read(folder)
    transcript = folder / "transcript.md"
    size = (
        transcript_size(len(transcript.read_text(encoding="utf-8"))) if transcript.exists() else ""
    )
    summary = folder / "summary.html"
    included = read_summary(summary) if body.include_summary and summary.exists() else None
    return Feedback(
        kind="summary_rating",
        message=body.comment,
        email=body.email,
        details=True,
        rating=body.rating,
        meeting={
            "provider": (recorded.get("llm") or {}).get("name"),
            "prompt_version": (recorded.get("prompt_versions") or {}).get("system"),
            "summary_language": recorded.get("summary_language"),
            "transcript_size": size,
        },
        summary_html=included,
    )


@router.get("/summary/{meeting_id}")
def rating_state(request: Request, meeting_id: str) -> dict[str, Any]:
    meeting = request.app.state.services.dao.require_meeting(meeting_id)
    return {"rating": meta.read(meeting.path).get("rating")}


@router.post("/summary/{meeting_id}/preview")
def rating_preview(request: Request, meeting_id: str, body: RatingPost) -> dict[str, Any]:
    try:
        return sender_of(request).preview(_rating(request, meeting_id, body))
    except FeedbackError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/summary/{meeting_id}")
def rate_summary(request: Request, meeting_id: str, body: RatingPost) -> dict[str, Any]:
    feedback = _rating(request, meeting_id, body)
    sender = sender_of(request)
    # Kept with the meeting either way: the page shows what the user chose.
    folder = request.app.state.services.dao.require_meeting(meeting_id).path
    meta.update(folder, rating={"value": body.rating, "at": datetime.now(UTC).isoformat()})
    if not sender.available:
        return {"reference": None, "sent": False, "kept_locally": True}
    try:
        return sender.send(feedback)
    except FeedbackError as exc:
        raise HTTPException(422, str(exc)) from exc
