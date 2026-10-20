"""``/api/tour``: the first-run tour's lines in the app log.

The tour runs in the page, on sample meetings that live only in the page's memory: none of
it is stored, and none of it reaches the database, search or the assistant. What the
server learns is that it ran, how much sample it made, how it ended and that the sample
was removed, so a log read later can tell a sample meeting from a real one.

Counts only, never a title: the sample borrows the titles of the user's own meetings.
Whether the tour was seen is a setting (``ui.tour_done``), saved through
``PUT /api/settings`` like every other.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.log import get

log = get("app.tour")

router = APIRouter(prefix="/api/tour")


class TourEvent(BaseModel):
    """One of four moments, with the counts that go with it and nothing else."""

    event: Literal["started", "sample_created", "ended", "sample_removed"]
    #: started: after first-run setup, or replayed from the menu or Settings.
    origin: Literal["setup", "replay"] | None = None
    #: sample_created: what the sample holds, and what of the user's it found.
    recordings: int | None = Field(default=None, ge=0, le=100)
    events: int | None = Field(default=None, ge=0, le=100)
    own_events: int | None = Field(default=None, ge=0, le=10_000)
    borrowed: bool | None = None
    #: ended: done, or skipped at a stop (0 is the welcome card).
    outcome: Literal["done", "skipped"] | None = None
    stop: int | None = Field(default=None, ge=0, le=20)
    of: int | None = Field(default=None, ge=0, le=20)
    #: sample_removed: how many sample items went, and how many burnt on screen.
    items: int | None = Field(default=None, ge=0, le=200)
    burnt: int | None = Field(default=None, ge=0, le=200)


def describe(body: TourEvent) -> str:
    """The log line for one event, in words a support reader can follow."""
    if body.event == "started":
        return f"tour started ({'after setup' if body.origin == 'setup' else 'replay'})"
    if body.event == "sample_created":
        week = (
            f"the week has {body.own_events} of the user's events, so none were added"
            if body.own_events
            else "the week was empty, so sample events fill it"
        )
        titles = "borrowed from the user's meetings" if body.borrowed else "the defaults"
        return (
            f"sample created for the tour: {body.recordings or 0} recordings, "
            f"{body.events or 0} calendar events; {week}; titles {titles}"
        )
    if body.event == "ended":
        if body.outcome == "done":
            return "tour done"
        where = "the welcome card" if not body.stop else f"stop {body.stop} of {body.of or 0}"
        return f"tour skipped at {where}"
    return (
        f"sample removed: {body.items or 0} items, {body.burnt or 0} burnt on screen; "
        "the user's own data was not touched"
    )


@router.post("/event")
def tour_event(body: TourEvent) -> dict[str, Any]:
    log.info(describe(body))
    return {"logged": True}
