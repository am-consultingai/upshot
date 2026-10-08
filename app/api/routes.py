"""Every route in TECHNICAL-DESIGN.md §12. JSON, 127.0.0.1 only."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from app import meta
from app.clock import iso
from app.config import LLM_PROVIDERS
from app.db.dao import GlossaryTerm, Meeting, capabilities, transaction
from app.log import get
from app.pipeline.states import STAGE_ORDER, JobStage, MeetingState
from app.services import Services
from app.version import build_info

log = get(__name__)

HEARTBEAT_S = 15.0
# 20 Hz reads as continuous without flooding the SSE stream.
LEVEL_INTERVAL_S = 0.05
# A backstop against a wedged client, not a routine timeout. The browser owns the meter's
# lifetime: it closes the stream when the tab is hidden and reopens it when shown. An
# earlier five-minute cap here was pointless — EventSource simply reconnected, so the
# microphone stayed open anyway and the log gained an entry every five minutes.
LEVEL_MAX_S = 1800.0


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _level_event(rms: float, peak: float, *, source: str, clipped: bool = False) -> str:
    return _sse(
        {
            "rms": round(float(rms), 4),
            "peak": round(float(peak), 4),
            "source": source,
            "clipped": clipped,
        }
    )


def services_of(request: Request) -> Services:
    return request.app.state.services  # type: ignore[no-any-return]


router = APIRouter(prefix="/api")


# --------------------------------------------------------------------------- models


class MeetingPatch(BaseModel):
    title: str | None = None
    sensitive: bool | None = None
    discard: bool | None = None
    #: Merged into the stored map: a slot set to null or "" is removed, others are kept.
    speaker_names: dict[str, str | None] | None = None
    #: The meeting's details, from the dialog shown when recording starts or later. "" (or
    #: an explicit null) clears one; a field left out is left alone.
    description: str | None = None
    planned_start: str | None = None
    planned_end: str | None = None


class ActionItemPatch(BaseModel):
    """Every field optional. An absent field is left alone; an explicit null clears it —
    which is why the handler reads ``model_fields_set`` rather than testing for None."""

    done: bool | None = None
    due_at: str | None = None
    snoozed_until: str | None = None
    who: str | None = None
    what: str | None = None
    detail: str | None = None


class ActionItemPost(BaseModel):
    what: str
    who: str = "ME"
    due_at: str | None = None
    detail: str | None = None


class TagsPut(BaseModel):
    tags: list[str] = Field(default_factory=list)


class AskPost(BaseModel):
    question: str
    scope: str = "meeting"


class SettingsPut(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class GlossaryPut(BaseModel):
    terms: list[dict[str, Any]] = Field(default_factory=list)


class SecretsPut(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)


class ProviderPost(BaseModel):
    provider: str | None = None
    #: First-run setup (D75): install with no window and no sign-in in it, and sign in
    #: with no window, the page taking Claude's code. Settings still opens windows.
    background: bool = False


class SigninCodePost(BaseModel):
    provider: str | None = None
    code: str


class IgnorePost(BaseModel):
    process: str


class LauncherAction(BaseModel):
    action: str
    meeting_id: str | None = None
    account_id: str | None = None
    calendar_id: str | None = None
    event_id: str | None = None
    #: The button also opened the meeting's link ("Join and record"). For the log.
    joined: bool = False


class StartPost(BaseModel):
    title: str | None = None
    #: "Record this one" on an upcoming calendar event: the recording starts already
    #: matched to it, the way the user would have matched it by hand afterwards.
    #: ``account_id`` may be left out when only one active account has the event.
    account_id: str | None = None
    calendar_id: str | None = None
    event_id: str | None = None


# --------------------------------------------------------------------------- status


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _disk_usage(path: Path) -> Any:
    """From the nearest folder that exists: a data folder on an unplugged drive made
    ``/status`` a 500, and the whole UI with it. A drive that is gone reads as full."""
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        return shutil.disk_usage(str(probe))
    except OSError:
        from types import SimpleNamespace

        return SimpleNamespace(total=0, used=0, free=0)


# --------------------------------------------------------------------------- speech model


def _model_set(svc: Services) -> Any:
    """All three speech models as one download, shared by every request."""
    from app.asr.model_manager import model_set

    return model_set(svc.config)


def _model_payload(svc: Services, models: Any = None) -> dict[str, Any]:
    from app.asr.local import MIN_VRAM_MB, device_plan
    from app.asr.models import TOTAL_BYTES

    models = models or _model_set(svc)
    payload: dict[str, Any] = models.status().as_dict()
    plan = device_plan(svc.config)
    payload.update(
        # The size before the download starts, when the hub has not been asked yet.
        expected_bytes=payload["total_bytes"] or TOTAL_BYTES,
        # Where it will run and why, for the setup screen's "CPU or GPU".
        device=plan.device,
        device_reason=plan.reason,
        vram_mb=plan.vram_mb,
        min_vram_mb=MIN_VRAM_MB,
    )
    return payload


@router.get("/model")
def model_status(request: Request) -> dict[str, Any]:
    """The speech model: on disk, downloading (with bytes), failed or missing."""
    return _model_payload(services_of(request))


@router.post("/model/download")
def model_download(request: Request) -> dict[str, Any]:
    svc = services_of(request)
    models = _model_set(svc)
    models.start()
    return _model_payload(svc, models)


@router.post("/model/cancel")
def model_cancel(request: Request) -> dict[str, Any]:
    svc = services_of(request)
    models = _model_set(svc)
    models.cancel()
    return _model_payload(svc, models)


class OpenPost(BaseModel):
    url: str


@router.post("/open")
def open_link(body: OpenPost) -> dict[str, bool]:
    """Open a link in the user's default browser (``window.open_external``).

    Upshot's window runs in a browser profile of its own, so a link it opened itself would
    land there, signed in to nothing. Every link leaving the app comes here instead.
    ``opened`` false: the page opens it itself (not Windows). http and https only.
    """
    from app import window

    try:
        return {"opened": window.open_external(body.url)}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/languages")
def languages() -> dict[str, Any]:
    """Every language Whisper transcribes, for the hidden "Transcribe again as…" list."""
    from app.asr.languages import LANGUAGES

    return {"languages": [language.as_dict() for language in LANGUAGES.values()]}


@router.get("/status")
def status(request: Request) -> dict[str, Any]:
    svc = services_of(request)
    recorder = svc.recorder
    detector = svc.detector
    disk = _disk_usage(svc.config.data_root.parent)
    return {
        "profile": svc.config.profile,
        "policy": svc.worker.policy if svc.worker else svc.config.job_policy,
        # The offer to record, shared with the toasts (app/prompts.py, D76): the page
        # shows the banner from this, so it is never out of step with the recorder.
        "prompt": svc.prompts.snapshot() if svc.prompts is not None else None,
        "recorder": {
            "active": bool(recorder and recorder.is_active()),
            "armed": bool(recorder and recorder.armed),
            "paused": bool(recorder and recorder.paused),
            "meeting_id": recorder.meeting_id if recorder else None,
            "levels": recorder.levels() if recorder else {},
            # The call's app let go: when the recording saves itself unless the call
            # comes back or the user keeps it going (D77). The page counts down to it.
            "ending": _ending(svc),
        },
        "detector": {
            "mode": svc.config.get("detection.mode"),
            # False until the user has answered how capture should work; the library
            # asks until they do rather than letting the default decide in silence.
            "decided": bool(svc.config.get("detection.decided", False)),
            "state": getattr(detector, "state", "idle") if detector else "off",
        },
        "queue": svc.queue.counts(),
        # File transcriptions count as work too (D86); failed ones are not in "queue".
        "queue_depth": svc.scheduler.depth() if svc.scheduler else svc.queue.depth(),
        "disk_free_bytes": disk.free,
        "storage_bytes": storage_bytes(svc),
        "fts": capabilities(svc.conn).fts,
        "now": iso(svc.clock.now()),
        # Which build answered: a report from a tester's machine names its commit.
        "build": build_info().as_dict(),
    }


@router.get("/jobs/active")
def active_jobs(request: Request) -> dict[str, Any]:
    """Every transcription running or waiting, meetings and files together, in the order
    the one worker will take them, with percent done and time left where known."""
    from app.transcription.active import active_jobs as listed

    return {"jobs": listed(services_of(request))}


@router.get("/attention")
def attention(request: Request) -> dict[str, Any]:
    """Everything the pipeline is stuck on, in words: failed stages and waiting ones.

    A waiting stage is one the queue parked on purpose — a plan's allowance used up until
    a stated time, a CLI waiting to be signed in — with ``retry_at`` saying when it will
    try again by itself. Nothing here is lost: the audio and transcript are on disk.
    """
    svc = services_of(request)
    items: list[dict[str, Any]] = []
    for job in svc.queue.needing_attention():
        meeting = svc.dao.visible_meeting(job.meeting_id)
        if meeting is None and svc.dao.get_meeting(job.meeting_id) is not None:
            continue  # on a hidden calendar account: not there (D82)
        items.append(
            {
                "meeting_id": job.meeting_id,
                "title": meeting.title if meeting else None,
                "stage": job.stage,
                "state": "failed" if job.state == "failed" else "waiting",
                "message": job.last_error,
                "retry_at": job.not_before if job.state != "failed" else None,
            }
        )
    return {"items": items}


#: How long a measured library size is reused. The status endpoint is polled every five
#: seconds, and walking every chunk file of every meeting that often is wasted work for a
#: number that moves by a meeting at a time.
STORAGE_TTL_S = 60.0


def _tree_bytes(root: Path) -> int:
    total = 0
    stack = [root]
    while stack:
        try:
            entries = list(os.scandir(stack.pop()))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue  # removed while walking: a retention sweep, a discard
    return total


def storage_bytes(svc: Services) -> int:
    """What the library occupies on disk: audio, transcripts, notes. 0 if it is absent."""
    now = svc.clock.monotonic()
    cached = svc.extras.get("storage_bytes")
    if cached is not None and now - cached[0] < STORAGE_TTL_S:
        return int(cached[1])
    root = Path(svc.config.data_root)
    value = _tree_bytes(root) if root.is_dir() else 0
    svc.extras["storage_bytes"] = (now, value)
    return value


# --------------------------------------------------------------------------- recording


@router.post("/recording/start")
def recording_start(request: Request, body: StartPost | None = None) -> dict[str, Any]:
    svc = services_of(request)
    if svc.recorder is None:
        raise HTTPException(503, "no recorder in this process")
    if svc.recorder.committed:
        raise HTTPException(409, "already recording")
    # The Settings meter may be holding the microphone. Release it *before* arming:
    # WASAPI gives one capture stream per endpoint, and the recorder must win.
    from app.audio import monitor as meter

    held = [name for name in ("me", "them") if meter.active(name) is not None]
    if held:
        log.info("taking %s back from the settings meters", " and ".join(held))
    event = None
    if body is not None and (body.calendar_id or body.event_id):
        if not body.calendar_id or not body.event_id:
            raise HTTPException(400, "name the event with both calendar_id and event_id")
        event = _event_lookup(svc, body.account_id, body.calendar_id, body.event_id)
        if event is None:
            raise HTTPException(404, "that event is not in the calendar cache")
    meter.release()  # every track: the recorder needs both endpoints
    payload = _start_payload(svc, event)
    # The same calendar meeting recorded again moments after its recording ended (a
    # restart, a rejoin, an end that came too soon) carries on that recording (D88).
    after_ms: int | None = None
    earlier = svc.meetings.to_continue(payload)
    reopened = svc.meetings.reopen(earlier.id) if earlier is not None else None
    if reopened is not None:
        meeting, after_ms = reopened
    elif event is not None:
        # Created under the event's name, then matched: as the user would match it by hand
        # when they picked it, so neither the end-of-recording rematch nor a later sync
        # second-guesses it.
        meeting = svc.meetings.create(
            source="manual",
            title=(body.title if body and body.title else event.title) or None,
            title_source="user" if body and body.title else "calendar",
        )
        meeting = svc.meetings.choose_event(meeting.id, payload)
    else:
        meeting = svc.meetings.create(source="manual", title=(body.title if body else None))
        if payload is not None:
            meeting = svc.meetings.choose_event(meeting.id, payload)
    from app.audio.devices import NoDeviceError

    try:
        svc.recorder.start(meeting.path, meeting.id, after_ms=after_ms)
    except NoDeviceError as exc:
        # No microphone (unplugged, or a Remote Desktop session that passes none
        # through): say so, and leave no meeting behind that never recorded. A meeting
        # being continued keeps what it had: it ends again where it ended.
        if after_ms is not None:
            svc.meetings.finish(
                meeting.id, ended_at=svc.clock.now() - timedelta(milliseconds=after_ms)
            )
        else:
            svc.meetings.discard(meeting.id)
        log.warning("recording %s could not start: %s", meeting.id, exc)
        raise HTTPException(
            409, "No microphone or speakers were found to record from. Connect one, then try again."
        ) from exc
    svc.recorder.start_thread()
    svc.meetings.committed(meeting, meeting.path)
    if after_ms is not None:
        log.info("recording %s continued from the interface", meeting.id)
    else:
        log.info("recording %s started from the interface", meeting.id)
    if svc.prompts is not None:
        svc.prompts.withdraw("a recording started")
    svc.events.publish("recorder", state="recording", meeting_id=meeting.id)
    if svc.notifier is not None:
        svc.notifier.recording_started(meeting.id, meeting.title or "")
    return {"meeting_id": meeting.id, "folder": meeting.folder, "state": meeting.state}


def _ending(svc: Services) -> dict[str, Any] | None:
    detector, recorder = svc.detector, svc.recorder
    if detector is None or recorder is None or not recorder.holding:
        return None
    ends_at = getattr(detector, "ending_at", None)
    if ends_at is None or detector.meeting_id != recorder.meeting_id:
        return None
    return {"ends_at": iso(ends_at), "meeting_id": recorder.meeting_id}


@router.post("/recording/keep")
def recording_keep(request: Request) -> dict[str, Any]:
    """ "Keep recording" while the call looks over: the meeting goes on (D77)."""
    svc = services_of(request)
    recorder = svc.recorder
    if svc.detector is None or recorder is None or not recorder.committed:
        raise HTTPException(409, "not recording")
    if not svc.detector.keep(recorder.meeting_id):
        raise HTTPException(409, "that recording is not being followed")
    svc.events.publish("recorder", state="recording", meeting_id=recorder.meeting_id)
    return {"meeting_id": recorder.meeting_id, "kept": True}


@router.post("/recording/stop")
def recording_stop(request: Request) -> dict[str, Any]:
    """Stop the recording. Every request is logged, refused or not: a Stop that seemed to
    do nothing (2026-09-26) could not be diagnosed, because nothing here said whether
    the request had even arrived."""
    svc = services_of(request)
    recorder = svc.recorder
    if (
        svc.detector is not None
        and recorder is not None
        and recorder.committed
        and recorder.holding
    ):
        # Stop now, while the call looks over: end where the call ended, not now (D77).
        ended = svc.detector.end_now(
            recorder.meeting_id, "stopped by the user after the call ended"
        )
        if ended:
            meeting = svc.dao.require_meeting(ended)
            log.info("recording %s stopped by the user after its call ended", ended)
            svc.events.publish("recorder", state="idle", meeting_id=ended)
            return {"meeting_id": ended, "state": meeting.state, "duration_s": meeting.duration_s}
    if svc.recorder is None or not svc.recorder.committed:
        # The page offers Stop because a meeting is marked "recording". Nothing is: it is
        # left from a run that ended. Stop means stop, so end that instead of refusing.
        repaired = svc.meetings.recover_orphans()
        if repaired:
            log.info("stop: nothing was recording; repaired %s", ", ".join(repaired))
            for meeting_id in repaired:
                svc.events.publish("meeting", meeting_id=meeting_id, action="patched")
            svc.events.publish("recorder", state="idle", meeting_id=repaired[0])
            return {"meeting_id": repaired[0], "state": "repaired", "repaired": repaired}
        log.warning(
            "stop requested, refused: not recording (recorder %s)",
            "absent" if svc.recorder is None else f"armed={svc.recorder.armed}",
        )
        raise HTTPException(409, "not recording")
    meeting_id = svc.recorder.meeting_id or ""
    log.info("recording %s: stop requested from the interface", meeting_id)
    try:
        result = svc.recorder.stop()
    except Exception:
        log.exception("recording %s: stop failed", meeting_id)
        raise
    duration_s = round(result.total_duration_ms / 1000)
    meeting = svc.meetings.finish(meeting_id, duration_s=duration_s)
    log.info("recording %s stopped after %d s (%s)", meeting_id, duration_s, meeting.state)
    svc.events.publish("recorder", state="idle", meeting_id=meeting_id)
    if meeting.id != meeting_id:
        log.info("recording %s joined the earlier recording %s", meeting_id, meeting.id)
    if svc.notifier is not None and meeting.state == MeetingState.RECORDED:
        svc.notifier.recording_ended(meeting.id, duration_s // 60)
    svc.meetings.ask_if_unsettled(meeting.id, svc.notifier, calendar_connected=bool(_active(svc)))
    return {
        "meeting_id": meeting.id,
        "state": meeting.state,
        "duration_s": duration_s,
        "chunks": len(result.records),
    }


@router.post("/prompt/dismiss")
def prompt_dismiss(request: Request) -> dict[str, Any]:
    """ "Not a meeting" on the banner: the offer goes, and is not made again (D76)."""
    svc = services_of(request)
    if svc.prompts is not None:
        svc.prompts.dismiss()
    return {"prompt": None}


class TestEvent(BaseModel):
    id: str
    title: str
    start: str
    end: str
    conference_url: str | None = None
    attendees: list[str] = Field(default_factory=lambda: ["Test Attendee"])


class TestCalendar(BaseModel):
    events: list[TestEvent] = Field(default_factory=list)


@router.post("/launcher/test/calendar")
def launcher_test_calendar(request: Request, body: TestCalendar) -> dict[str, Any]:
    """Test hook (D76): set the calendar meetings of the "upshot-test" calendar.

    For machine B's scenario runs only: off unless ``testing.hooks`` is true in that
    machine's own settings, and then only for the launcher key. The events go into the
    cache the way a sync leaves them, so reminders, matching and the scheduled end all see
    real calendar meetings. An empty list clears them.
    """
    from datetime import datetime as _dt

    from app.api.security import LAUNCHER_HEADER
    from app.gcal.events import Attendee, CalendarEvent, EventStore

    svc = services_of(request)
    if not svc.auth.valid_launcher(request.headers.get(LAUNCHER_HEADER)):
        raise HTTPException(401, "not the launcher")
    if svc.config.get("testing.hooks") is not True:
        raise HTTPException(404, "test hooks are off")
    # Under the first connected account that is shown, so reminders and matching see them
    # the way they see that account's own meetings (D82).
    active = sorted(_active(svc))
    if not active:
        raise HTTPException(409, "connect a Google account first: test events need one")
    account_id = active[0]
    now = svc.clock.now()
    events = [
        CalendarEvent(
            account_id=account_id,
            calendar_id="upshot-test",
            event_id=item.id,
            title=item.title,
            start=_dt.fromisoformat(item.start),
            end=_dt.fromisoformat(item.end),
            ical_uid=f"{item.id}@upshot-test",
            response="accepted",
            attendees=tuple(Attendee(name=name) for name in item.attendees),
            attendee_count=len(item.attendees),
            conference_url=item.conference_url,
        )
        for item in body.events
    ]
    EventStore(svc.conn).replace_window(
        account_id,
        "upshot-test",
        now - timedelta(days=1),
        now + timedelta(days=2),
        events,
        synced_at=now,
    )
    log.info("test hook: %d calendar event(s) set", len(events))
    return {"events": [e.event_id for e in events]}


@router.post("/launcher/action")
def launcher_action(request: Request, body: LauncherAction) -> dict[str, Any]:
    """A toast button, pressed (D70). The caller is ``upshot.exe "upshot:..."``, which
    proves itself with the launcher key: CSRF lets this one path through on it, so it is
    checked again here rather than trusted from the middleware alone.

    Each action takes the same path as its button in the window. Stop and "Not a meeting"
    name their meeting, so a notification left in the Action Center cannot stop a later
    recording than the one it was about.
    """
    from app.api.security import LAUNCHER_HEADER

    svc = services_of(request)
    if not svc.auth.valid_launcher(request.headers.get(LAUNCHER_HEADER)):
        raise HTTPException(401, "not the launcher")
    log.info(
        "notification button: %s%s (meeting %s, calendar %s)",
        "Join and record" if body.joined else body.action,
        "" if not body.joined else " — the meeting was opened in the browser",
        body.meeting_id or "-",
        f"{body.calendar_id}/{body.event_id}" if body.calendar_id else "-",
    )
    if body.action == "recording.start":
        # A button that names no meeting starts the one on offer (D76), so a detected
        # call is recorded under its name ("Meet - Weekly sync"), not as "meeting".
        offer = svc.prompts.current if svc.prompts is not None else None
        calendar_id = body.calendar_id or (offer.calendar_id if offer else None)
        event_id = body.event_id or (offer.event_id if offer else None)
        account_id = body.account_id or (offer.account_id if offer else None)
        event = (
            StartPost(account_id=account_id, calendar_id=calendar_id, event_id=event_id)
            if calendar_id and event_id
            else StartPost(title=offer.title)
            if offer is not None and offer.title and not offer.candidates
            else None
        )
        if (
            event is not None
            and event.calendar_id
            and event.event_id
            and _event_or_none(svc, event.account_id, event.calendar_id, event.event_id) is None
        ):
            event = None  # the event left the cache since the toast: record it unnamed
        try:
            return recording_start(request, event)
        except HTTPException as exc:
            # The press came from a notification, so nobody is looking at Upshot to see
            # an error: answer with another notification.
            if svc.notifier is not None:
                svc.notifier.could_not_start(str(exc.detail))
            raise
    recording = svc.recorder is not None and svc.recorder.committed
    current = svc.recorder.meeting_id if svc.recorder is not None else None
    if body.action == "recording.stop":
        if body.meeting_id and body.meeting_id != current:
            raise HTTPException(409, "that meeting is no longer recording")
        return recording_stop(request)
    if body.action == "recording.keep":
        if svc.detector is None or not svc.detector.keep(body.meeting_id or current):
            raise HTTPException(409, "that recording is not being followed")
        svc.events.publish("recorder", state="recording", meeting_id=current)
        return {"meeting_id": current, "kept": True}
    if body.action in ("meeting.assign", "meeting.none"):
        if not body.meeting_id:
            raise HTTPException(400, "name the meeting")
        survivor = _assign(
            svc,
            body.meeting_id,
            account_id=body.account_id,
            calendar_id=body.calendar_id,
            event_id=body.event_id,
            none=body.action == "meeting.none",
        )
        return {"meeting_id": survivor}
    if body.action == "meeting.discard":
        if not body.meeting_id:
            raise HTTPException(400, "name the meeting")
        if recording and body.meeting_id == current:
            recording_stop(request)
        # Stopping may have discarded it already (too short to be a meeting).
        if svc.dao.require_meeting(body.meeting_id).state != MeetingState.DISCARDED:
            svc.meetings.discard(body.meeting_id)
        svc.events.publish("meeting", meeting_id=body.meeting_id, action="patched")
        return {"meeting_id": body.meeting_id, "state": MeetingState.DISCARDED}
    raise HTTPException(400, f"unknown action {body.action!r}")


@router.post("/recording/pause")
def recording_pause(request: Request) -> dict[str, Any]:
    svc = services_of(request)
    if svc.recorder is None or not svc.recorder.committed:
        raise HTTPException(409, "not recording")
    if svc.recorder.paused:
        svc.recorder.resume()
    else:
        svc.recorder.pause()
    svc.events.publish("recorder", state="paused" if svc.recorder.paused else "recording")
    return {"paused": svc.recorder.paused}


# --------------------------------------------------------------------------- meetings


def _instant(text: str) -> datetime:
    """A stored timestamp as an instant. A value written without an offset is read as
    local time, which is what it was."""
    when = datetime.fromisoformat(text)
    return when if when.tzinfo else when.astimezone()


def _inferred_calendar(
    svc: Services, meeting: Meeting, events: list[Any] | None = None
) -> dict[str, Any]:
    """The event a recording belongs to when nothing was ever stored on it.

    A recording made before the calendar was connected carries no snapshot, and nothing
    ever goes back to give it one. So it drew a second block of its own beside its own
    event in the calendar view — the same meeting, twice, under two names — and its page
    knew nothing about the meeting it had been. The matcher that runs at record time is
    asked again here, against the cache, and only a *matched* verdict is used: a guess is
    not worth drawing as a fact.

    Nothing is written. The snapshot belongs to the moment of recording, and a meeting
    the user has already ruled on is never second-guessed, because that one is not empty.
    """
    from app.gcal.match import MATCHED, match
    from app.gcal.source import SEARCH_MARGIN, snapshot

    try:
        started = _instant(meeting.started_at)
        ended = _instant(meeting.ended_at) if meeting.ended_at else None
    except ValueError:
        return {}
    active = _active(svc)
    nearby = (
        events
        if events is not None
        else _event_store(svc).between(
            started - SEARCH_MARGIN, (ended or started) + SEARCH_MARGIN, accounts=active
        )
    )
    verdict = match(nearby, started, ended)
    if verdict.state != MATCHED or verdict.best is None:
        return {}
    best = verdict.best.event
    return snapshot(
        best,
        state=MATCHED,
        source="auto",
        confidence=verdict.confidence,
        reason="matched against the calendar cache when this page was read",
        accounts=[e.account_id for e in nearby if e.occurrence == best.occurrence],
    )


def _calendar_of(
    svc: Services, meeting: Meeting, events: list[Any] | None = None
) -> dict[str, Any]:
    """What event this recording was: the stored snapshot, else one worked out now."""
    from app.meetings import calendar_payload

    return calendar_payload(meeting) or _inferred_calendar(svc, meeting, events)


def _action_counts(pair: tuple[int, int] | None) -> dict[str, int]:
    total, still_open = pair or (0, 0)
    return {"actions_total": total, "actions_open": still_open}


def _visible(svc: Services, meeting_id: str) -> Meeting:
    """The meeting, or 404 — also when it belongs only to hidden calendar accounts. An id
    merged into another meeting finds that one (D89)."""
    meeting = svc.dao.visible_meeting(svc.dao.resolve(meeting_id))
    if meeting is None:
        raise HTTPException(404, "no such meeting")
    return meeting


def _meeting_payload(svc: Services, meeting_id: str) -> dict[str, Any]:
    meeting_id = svc.dao.resolve(meeting_id)
    meeting = svc.dao.visible_meeting(meeting_id)
    if meeting is None or svc.queue.deleting(meeting_id):
        raise HTTPException(404, "no such meeting")
    payload = meeting.as_dict()
    # Which way the transcript and the notes run, for any Whisper language: the list of
    # right-to-left languages lives in app/asr/languages.py only, not in the page.
    from app.asr.languages import direction_for

    payload["direction"] = direction_for(meeting.language)
    # For the hidden "Transcribe again as…" list only: the classifier's next guesses.
    detection = (meta.read(meeting.path).get("asr") or {}).get("language_detection") or {}
    payload["language_candidates"] = [str(lang) for lang, _p in detection.get("top3", [])]
    payload["summary_direction"] = direction_for(meeting.summary_language or meeting.language)
    payload["jobs"] = [
        {
            "stage": job.stage,
            "state": job.state,
            "attempts": job.attempts,
            "last_error": job.last_error,
            # So the page can say how long a stage has been at it, not merely that it is.
            "started_at": job.started_at,
        }
        for job in svc.queue.for_meeting(meeting_id)
    ]
    # "N files ahead in the queue": file transcriptions that run before this meeting's
    # waiting job (D86, R5), so a meeting held behind them says why.
    waiting = next((j for j in svc.queue.for_meeting(meeting_id) if j.state == "pending"), None)
    payload["files_ahead"] = (
        svc.scheduler.files_ahead(waiting) if waiting is not None and svc.scheduler else 0
    )
    payload["evidence"] = meeting.evidence
    # Parsed for the page; the stored string stays as it was for anything that reads it.
    payload["calendar"] = _calendar_of(svc, meeting) or None
    shown = payload["calendar"] or {}
    payload["needs_meeting"] = (shown.get("match") or {}).get(
        "state"
    ) != "matched" and svc.meetings.needs_meeting(meeting, calendar_connected=bool(_active(svc)))
    # Another recording of the same calendar meeting that could not be merged by itself
    # (one was busy): the page offers to merge them (D89).
    other = svc.meetings.merge_target(meeting)
    payload["merge_with"] = (
        {"id": other.id, "title": other.title, "started_at": other.started_at} if other else None
    )
    mirrored = meta.read(meeting.path)
    # A meeting whose audio the retention policy removed is not a meeting that failed to
    # record, and the page must not say so.
    payload["audio_deleted_at"] = mirrored.get("audio_deleted_at")
    # Which provider wrote the notes, and — when it was the fallback — for whom and why
    # (Codex 3). A summary from a provider other than the chosen one must say so.
    recorded = mirrored.get("llm")
    llm: dict[str, Any] = recorded if isinstance(recorded, dict) else {}
    payload["summarized_by"] = (
        {
            "provider": llm.get("name"),
            "fallback_for": llm.get("fallback_for"),
            "fallback_reason": llm.get("fallback_reason"),
        }
        if llm.get("name")
        else None
    )
    # Which tracks exist and whether either actually has anything on it. The player used
    # to be hardwired to "them", so a meeting where nobody else spoke played silence and
    # looked broken.
    from app.audio.writer import track_summary

    payload["audio_tracks"] = track_summary(meeting.path)
    # Rendered as checkboxes beside the summary, and the same rows the inbox reads.
    items = svc.dao.action_items(meeting_id=meeting.id)
    payload["action_items"] = [item.as_dict() for item in items]
    payload.update(_action_counts((len(items), sum(1 for item in items if not item.done))))
    payload["tags"] = svc.dao.tags(meeting.id)
    payload["chapters"] = _chapters_of(meeting)
    payload["failed_stage"] = svc.dao.failed_stages().get(meeting.id)
    return payload


def _chapters_of(meeting: Meeting) -> list[dict[str, Any]]:
    """The summarizer's chapters from notes.json; [] for a meeting summarized before them."""
    from app.llm.schema import chapters

    path = meeting.path / "notes.json"
    if not path.exists():
        return []
    try:
        return chapters(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return []


@router.get("/meetings")
def list_meetings(
    request: Request,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    q: str | None = None,
    state: str | None = None,
    limit: int = 200,
    account: list[str] | None = Query(default=None),
) -> dict[str, Any]:
    """Newest first. ``account`` (repeatable) is the library filter: meetings on any of
    those calendar accounts, ``none`` for meetings on no account. Meetings of hidden
    accounts are never listed (D82)."""
    svc = services_of(request)
    meetings = svc.dao.list_meetings(
        frm=from_, to=to, q=q, state=state, limit=limit, accounts=account
    )
    # A meeting being deleted is gone as far as anyone can see, while its stage stops.
    meetings = [meeting for meeting in meetings if not svc.queue.deleting(meeting.id)]
    # What each meeting still owes, so the list can say it without opening anything: one
    # grouped query for the whole page rather than one per row.
    counts = svc.dao.action_item_counts()
    tags = svc.dao.tags_by_meeting()
    failed = svc.dao.failed_stages()
    members = svc.dao.calendar_accounts_by_meeting()
    connected = bool(_active(svc))
    return {
        "meetings": [
            {
                **meeting.as_dict(),
                # The calendar meeting is not settled: the library marks it (D89).
                "needs_meeting": svc.meetings.needs_meeting(meeting, calendar_connected=connected),
                **_action_counts(counts.get(meeting.id)),
                "tags": tags.get(meeting.id, []),
                # Every calendar account the meeting is on: the dots in the list.
                "calendar_accounts": members.get(meeting.id, []),
                # Which stage stopped, so a card can say "transcription failed" without
                # opening the meeting. Null when nothing has failed.
                "failed_stage": failed.get(meeting.id),
            }
            for meeting in meetings
        ],
        "count": len(meetings),
    }


@router.get("/search")
def search(request: Request, q: str = "", limit: int = 50) -> dict[str, Any]:
    """Hits, not titles: the sentence that matched, who said it, and when.

    ``/meetings?q=`` answers "which meetings mention this", which left the Search
    screen showing four identical-looking rows that each had to be opened to find
    out whether they were the right one. This answers "show me the sentence".
    """
    svc = services_of(request)
    hits = svc.dao.search(q, limit=limit)  # hidden accounts' meetings are left out
    meetings = svc.dao.list_meetings(limit=10_000)
    titles = {meeting.id: (meeting.title, meeting.started_at) for meeting in meetings}
    # The name the user gave the hit's speaker slot (D52). ME stays null: the page says
    # "You" in the interface language rather than whatever was typed here.
    speakers = {meeting.id: meeting.speakers for meeting in meetings}
    return {
        "q": q,
        "hits": [
            {
                "meeting_id": hit.meeting_id,
                "meeting_title": titles.get(hit.meeting_id, (None, None))[0],
                "meeting_started_at": titles.get(hit.meeting_id, (None, None))[1],
                "speaker": hit.speaker,
                # Any slot can be named, the microphone's included (D85).
                "speaker_name": (
                    speakers.get(hit.meeting_id, {}).get(hit.speaker) if hit.speaker else None
                ),
                "at_ms": hit.at_ms,
                "text": hit.text,
                "snippet": hit.snippet,
                "kind": hit.kind,
            }
            for hit in hits
        ],
        "count": len(hits),
    }


@router.get("/action-items")
def list_action_items(
    request: Request,
    open_only: bool = Query(default=False, alias="open"),
    meeting_id: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    """Every commitment the summaries recorded, across every meeting.

    The point of the whole structured-envelope change: "what did I promise this week,
    and to whom" answered without opening a meeting.
    """
    svc = services_of(request)
    items = svc.dao.action_items(meeting_id=meeting_id, open_only=open_only, limit=limit)
    return {
        "items": [item.as_dict() for item in items],
        "count": len(items),
        "open": sum(1 for item in items if not item.done),
    }


def _checked_date(value: str | None, field: str) -> str | None:
    """None, or a real YYYY-MM-DD; anything else is the caller's mistake (422)."""
    from app.due import parse_iso_date

    if value is None:
        return None
    parsed = parse_iso_date(value)
    if parsed is None:
        raise HTTPException(422, f"{field} must be a date as YYYY-MM-DD, or null")
    return parsed.isoformat()


@router.patch("/action-items/{item_id}")
def patch_action_item(request: Request, item_id: int, body: ActionItemPatch) -> dict[str, Any]:
    """Tick, reschedule, snooze, reassign or reword one item.

    Only the fields sent are touched, and a field sent as null is cleared: ``{"due_at":
    null}`` removes a date, where leaving ``due_at`` out keeps it.
    """
    svc = services_of(request)
    sent = body.model_fields_set
    fields: dict[str, Any] = {}
    if "done" in sent and body.done is not None:
        fields["done"] = body.done
    for key in ("due_at", "snoozed_until"):
        if key in sent:
            fields[key] = _checked_date(getattr(body, key), key)
    if "detail" in sent:
        fields["detail"] = body.detail
    for key in ("who", "what"):
        if key in sent and getattr(body, key) is not None:
            fields[key] = getattr(body, key)
    if svc.dao.action_item(item_id) is None:
        raise HTTPException(404, "no such action item")
    try:
        item = svc.dao.update_action_item(item_id, **fields)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if item is None:
        raise HTTPException(404, "no such action item")
    svc.events.publish("action-item", item_id=item_id, done=item.done)
    return item.as_dict()


@router.delete("/action-items/{item_id}")
def delete_action_item(request: Request, item_id: int) -> dict[str, Any]:
    """Remove one item, the model's or the user's. A model item comes back if a later
    re-summarize finds it again — deleting is "not this", not "never"."""
    svc = services_of(request)
    item = svc.dao.action_item(item_id)
    if item is None or not svc.dao.delete_action_item(item_id):
        raise HTTPException(404, "no such action item")
    svc.events.publish("action-item", item_id=item_id, deleted=True)
    return {"deleted": item_id}


@router.post("/meetings/{meeting_id}/action-items", status_code=201)
def add_action_item(request: Request, meeting_id: str, body: ActionItemPost) -> dict[str, Any]:
    """An item the user typed in. It is theirs: no re-summarize removes or rewrites it."""
    svc = services_of(request)
    if svc.dao.visible_meeting(meeting_id) is None:
        raise HTTPException(404, "no such meeting")
    if not body.what.strip():
        raise HTTPException(422, "an action item needs something to do")
    item = svc.dao.add_action_item(
        meeting_id,
        what=body.what,
        who=body.who,
        due_at=_checked_date(body.due_at, "due_at"),
        detail=body.detail,
    )
    svc.events.publish("action-item", item_id=item.id, done=item.done)
    return item.as_dict()


# --------------------------------------------------------------------------- tags


@router.get("/tags")
def list_tags(request: Request) -> dict[str, Any]:
    """Every tag in use, most used first: what the filter and the tag picker offer."""
    svc = services_of(request)
    return {"tags": [{"tag": tag, "count": count} for tag, count in svc.dao.tag_counts()]}


@router.put("/meetings/{meeting_id}/tags")
def put_meeting_tags(request: Request, meeting_id: str, body: TagsPut) -> dict[str, Any]:
    """Replace this meeting's tags. Deduplicated case-insensitively; see Dao.set_tags."""
    svc = services_of(request)
    if svc.dao.visible_meeting(meeting_id) is None:
        raise HTTPException(404, "no such meeting")
    try:
        tags = svc.dao.set_tags(meeting_id, body.tags)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    svc.events.publish("meeting", meeting_id=meeting_id, action="tags")
    return {"tags": tags}


# --------------------------------------------------------------------------- threads


@router.get("/meetings/{meeting_id}/related")
def related_meetings(request: Request, meeting_id: str) -> dict[str, Any]:
    """Up to five meetings this one is a thread with, best first, each with its reasons."""
    from app.related import related

    svc = services_of(request)
    if svc.dao.visible_meeting(meeting_id) is None:
        raise HTTPException(404, "no such meeting")
    return {"related": [found.as_api() for found in related(svc.dao, meeting_id)]}


@router.post("/meetings/{meeting_id}/ask")
def ask_meeting(request: Request, meeting_id: str, body: AskPost) -> dict[str, Any]:
    """A question answered from the transcript, with the moments it rests on."""
    from app.ask import AskError, ask

    svc = services_of(request)
    meeting = svc.dao.visible_meeting(meeting_id)
    if meeting is None:
        raise HTTPException(404, "no such meeting")
    if not body.question.strip():
        raise HTTPException(422, "ask a question")
    if body.scope not in ("meeting", "related"):
        raise HTTPException(422, 'scope is "meeting" or "related"')
    try:
        answer = ask(svc.dao, svc.config, meeting, body.question, scope=body.scope, client=svc.llm)
    except AskError as exc:
        raise HTTPException(502, str(exc)) from exc
    return answer.as_api()


@router.get("/meetings/{meeting_id}")
def get_meeting(request: Request, meeting_id: str) -> dict[str, Any]:
    return _meeting_payload(services_of(request), meeting_id)


@router.patch("/meetings/{meeting_id}")
def patch_meeting(request: Request, meeting_id: str, body: MeetingPatch) -> dict[str, Any]:
    svc = services_of(request)
    meeting = svc.dao.visible_meeting(meeting_id)
    if meeting is None:
        raise HTTPException(404, "no such meeting")
    if body.title is not None:
        svc.dao.update_meeting(meeting_id, title=body.title, title_source="user")
    if body.sensitive is not None:
        svc.dao.update_meeting(meeting_id, sensitive=int(body.sensitive))
    if body.speaker_names is not None:
        names = dict(meeting.speakers)
        for slot, name in body.speaker_names.items():
            slot = slot.strip()
            if not slot or len(slot) > 40:
                raise HTTPException(422, "a speaker slot is a short label such as THEM_1")
            cleaned = " ".join((name or "").split())
            if len(cleaned) > 80:
                raise HTTPException(422, "a speaker name is at most 80 characters")
            if cleaned:
                names[slot] = cleaned
            else:
                names.pop(slot, None)
        svc.dao.update_meeting(
            meeting_id, speaker_names=json.dumps(names, ensure_ascii=False) if names else None
        )
    details = _details(body, meeting)
    if details:
        svc.dao.update_meeting(meeting_id, **details)
    if body.discard:
        svc.meetings.discard(meeting_id)
    svc.events.publish("meeting", meeting_id=meeting_id, action="patched")
    return _meeting_payload(svc, meeting_id)


def _details(body: MeetingPatch, meeting: Any) -> dict[str, Any]:
    """The description and planned times a patch sets. Times are ISO 8601 with an offset;
    the end must come after the start."""
    from datetime import datetime

    sent = body.model_fields_set
    fields: dict[str, Any] = {}
    if "description" in sent:
        text = (body.description or "").strip()
        if len(text) > 5000:
            raise HTTPException(422, "a description is at most 5000 characters")
        fields["description"] = text or None
    for name in ("planned_start", "planned_end"):
        if name not in sent:
            continue
        value = (getattr(body, name) or "").strip()
        if value:
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError as exc:
                raise HTTPException(422, f"{name} is not an ISO date and time") from exc
            if parsed.tzinfo is None:
                raise HTTPException(422, f"{name} needs its time zone offset")
        fields[name] = value or None
    start = fields.get("planned_start", meeting.planned_start)
    end = fields.get("planned_end", meeting.planned_end)
    if start and end and datetime.fromisoformat(end) <= datetime.fromisoformat(start):
        raise HTTPException(422, "the end must come after the start")
    return fields


@router.get("/locale")
def locale_formats() -> dict[str, str]:
    """The user's short date and time patterns, as Windows shows them (sShortDate,
    sShortTime): the meeting-details times are written with them."""
    from app.locale_formats import windows_formats

    return windows_formats().as_dict()


@router.get("/meetings/{meeting_id}/transcript")
def get_transcript(request: Request, meeting_id: str) -> Response:
    svc = services_of(request)
    meeting = _visible(svc, meeting_id)
    path = meeting.path / "transcript.json"
    if not path.exists():
        raise HTTPException(404, "no transcript yet")
    return Response(path.read_text(encoding="utf-8"), media_type="application/json")


@router.get("/meetings/{meeting_id}/notes")
def get_notes(request: Request, meeting_id: str) -> Response:
    svc = services_of(request)
    meeting = _visible(svc, meeting_id)
    path = meeting.path / "notes.json"
    if not path.exists():
        raise HTTPException(404, "no notes yet")
    return Response(path.read_text(encoding="utf-8"), media_type="application/json")


@router.get("/meetings/{meeting_id}/summary.html")
def get_summary(request: Request, meeting_id: str) -> Response:
    svc = services_of(request)
    meeting = _visible(svc, meeting_id)
    path = meeting.path / "summary.html"
    if not path.exists():
        raise HTTPException(404, "not rendered yet")
    return Response(path.read_text(encoding="utf-8"), media_type="text/html; charset=utf-8")


def _parse_range(header: str, size: int) -> tuple[int, int]:
    try:
        spec = header.split("=", 1)[1]
        first, _, last = spec.partition("-")
        start = int(first) if first else 0
        end = int(last) if last else size - 1
    except (IndexError, ValueError) as exc:
        raise HTTPException(416, "malformed Range header") from exc
    if start >= size:
        raise HTTPException(416, "range beyond the end of the file")
    return start, min(end, size - 1)


@router.get("/meetings/{meeting_id}/audio")
def get_audio(request: Request, meeting_id: str, track: str = "mix") -> Response:
    """``mix`` (default) is the whole meeting; ``me``/``them`` are the raw tracks."""
    from app.audio.writer import track_path

    svc = services_of(request)
    meeting = _visible(svc, meeting_id)

    if track == "mix":
        # The whole meeting as one stream, mixed on read. Nothing extra on disk, and the
        # byte range maps 1:1 onto both tracks, so seeking stays cheap.
        from app.audio.mix import layout_for, read_range

        layout = layout_for(meeting.path)
        if layout is None:
            raise HTTPException(404, "no audio for this meeting")
        range_header = request.headers.get("range")
        if not range_header:
            return Response(
                read_range(layout, 0, layout.size - 1),
                media_type="audio/wav",
                headers={"Accept-Ranges": "bytes", "Content-Length": str(layout.size)},
            )
        start, end = _parse_range(range_header, layout.size)
        return Response(
            read_range(layout, start, end),
            status_code=206,
            media_type="audio/wav",
            headers={
                "Content-Range": f"bytes {start}-{end}/{layout.size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(end - start + 1),
            },
        )

    if track not in ("me", "them"):
        raise HTTPException(404, "no such track")
    path = track_path(meeting.path, track)
    if not path.exists():
        raise HTTPException(404, "no audio for this track")
    size = path.stat().st_size
    range_header = request.headers.get("range")
    if not range_header:
        return FileResponse(path, media_type="audio/wav", headers={"Accept-Ranges": "bytes"})
    start, end = _parse_range(range_header, size)
    with path.open("rb") as handle:
        handle.seek(start)
        payload = handle.read(end - start + 1)
    return Response(
        payload,
        status_code=206,
        media_type="audio/wav",
        headers={
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(len(payload)),
        },
    )


@router.delete("/meetings/{meeting_id}")
def delete_meeting(request: Request, meeting_id: str) -> dict[str, Any]:
    """Remove a meeting: its folder on disk and its rows. There is no undo.

    Whatever is running for it stops: its waiting jobs are dropped, and a stage in progress
    stops at its next checkpoint (between chunks of speech when transcribing). The meeting
    is gone from every list at once; its files go as soon as the stage has let go of them,
    which the worker does (``pending``). A marker in the folder finishes an interrupted
    deletion at the next start.
    """
    from app.meetings import DELETING_MARKER

    svc = services_of(request)
    meeting = _visible(svc, meeting_id)
    # An id merged away names the meeting it joined (D89): every check below is about that.
    meeting_id = meeting.id
    recorder = svc.recorder
    if recorder is not None and recorder.committed and recorder.meeting_id == meeting_id:
        raise HTTPException(409, "this meeting is still recording")

    svc.queue.request_delete(meeting_id)
    if svc.notifier is not None:
        # "Transcribing..." for a meeting that is gone would mislead.
        svc.notifier.withdraw(meeting_id)
    if svc.queue.running_for(meeting_id) is not None:
        with contextlib.suppress(OSError):
            meeting.path.mkdir(parents=True, exist_ok=True)
            (meeting.path / DELETING_MARKER).write_text("", encoding="utf-8")
        svc.events.publish("meeting", meeting_id=meeting_id, action="deleted")
        log.info("meeting %s: deleting once its running stage stops", meeting_id)
        return {"deleted": meeting_id, "folder": str(meeting.path), "pending": True}
    try:
        # Never delete outside the data root, whatever the database says the folder is.
        folder = svc.meetings.purge(meeting)
    except ValueError as exc:
        svc.queue.deleted(meeting_id)
        log.warning("meeting %s: not deleted: %s", meeting_id, exc)
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        # Windows will not unlink a file something has open. Saying so beats deleting the
        # row against a folder that survived, which orphans it with nothing pointing at it.
        svc.queue.deleted(meeting_id)
        log.warning("meeting %s: not deleted: %s", meeting_id, exc)
        raise HTTPException(409, str(exc)) from exc
    svc.queue.deleted(meeting_id)
    svc.events.publish("meeting", meeting_id=meeting_id, action="deleted")
    return {"deleted": meeting_id, "folder": str(folder), "pending": False}


@router.get("/retention")
def retention_policy(request: Request) -> dict[str, Any]:
    """What the retention policy would delete right now. Reads; deletes nothing.

    A key that promises deletion and never deletes is a trust problem (D27); a sweep that
    deletes silently is a worse one. This is how the policy is checked before it is
    believed, including which meetings it declined to touch and why.
    """
    from app.retention import plan

    svc = services_of(request)
    return plan(
        dao=svc.dao,
        queue=svc.queue,
        meetings=svc.meetings,
        config=svc.config,
        clock=svc.clock,
        recorder=svc.recorder,
        transcriptions=svc.transcriptions,
    ).as_dict()


@router.post("/retention/sweep")
def retention_sweep(request: Request) -> dict[str, Any]:
    """Apply the policy now rather than waiting for the worker's next pass."""
    from app.retention import sweep_services

    return sweep_services(services_of(request)).as_dict()


@router.post("/meetings/{meeting_id}/jobs/{stage}/retry")
def retry_stage(
    request: Request,
    meeting_id: str,
    stage: str,
    force: bool = False,
    language: str | None = None,
) -> dict[str, Any]:
    """Re-run a stage. With ``force``, redo the work rather than reuse what is on disk.

    ``language`` (transcribe only) is the hidden "Transcribe again as…" (R9): the meeting
    is transcribed in that language whatever the classifier said, by the model for it,
    and stays so on every later rerun. Implies ``force``.
    """
    svc = services_of(request)
    if stage not in {str(item) for item in STAGE_ORDER}:
        raise HTTPException(404, f"no such stage {stage!r}")
    meeting = _visible(svc, meeting_id)
    meeting_id = meeting.id  # an id merged away names the meeting it joined (D89)
    if language is not None:
        from app.asr.languages import is_supported

        if stage != str(JobStage.TRANSCRIBE):
            raise HTTPException(422, "a language applies to the transcribe stage only")
        if not is_supported(language):
            raise HTTPException(422, f"{language!r} is not a language Whisper transcribes")
        meta.update(meeting.path, asr_language_override=language)
        force = True
    if force:
        # A button press is a decision. Every later stage is redone too, or a fresh
        # summary would be rendered from the previous one's HTML.
        for later in STAGE_ORDER[STAGE_ORDER.index(JobStage(stage)) :]:
            svc.queue.request_rerun(meeting_id, later)
    job = svc.queue.retry(meeting_id, JobStage(stage))
    svc.events.publish("job", meeting_id=meeting_id, stage=stage, state=job.state)
    return {"stage": job.stage, "state": job.state, "attempts": job.attempts}


@router.post("/meetings/{meeting_id}/keep")
def keep_meeting(request: Request, meeting_id: str) -> dict[str, Any]:
    """Undo a discard: treat this recording as a meeting after all.

    A recording shorter than ``audio.min_meeting_s`` is filed as DISCARDED rather
    than transcribed, which is the right default — the detector wakes on a
    notification sound often enough that without it the library fills with
    eight-second meetings. What was missing is the way back.

    Nothing was ever deleted: DISCARDED is a state, the audio stays on disk, and
    the state machine has always allowed DISCARDED -> RECORDED (the transition is
    even commented "it was a meeting after all"). But no endpoint offered it and
    no button called it, so in practice a two-minute conversation was unreachable
    — which is indistinguishable from losing it, whatever the database says.
    """
    svc = services_of(request)
    meeting = _visible(svc, meeting_id)
    meeting_id = meeting.id  # an id merged away names the meeting it joined (D89)
    if meeting.state != MeetingState.DISCARDED:
        raise HTTPException(409, f"{meeting_id} is {meeting.state}, not discarded")

    updated = svc.dao.set_state(meeting_id, MeetingState.RECORDED)
    meta.mirror(svc.dao.require_meeting(meeting_id))
    svc.queue.enqueue(meeting_id, JobStage.TRANSCRIBE)
    svc.events.publish("meeting", meeting_id=meeting_id, state=str(updated.state))
    return {"id": meeting_id, "state": str(updated.state)}


@router.post("/import")
async def import_audio(request: Request, file: UploadFile) -> dict[str, Any]:
    """An uploaded recording becomes chunk files and runs the ordinary pipeline."""
    from app.audio.ingest import UnsupportedAudio, ingest

    svc = services_of(request)
    meeting = svc.meetings.create(source="imported", title=Path(file.filename or "import").stem)
    target = meeting.path / "import"
    target.mkdir(parents=True, exist_ok=True)
    destination = target / (file.filename or "audio.wav")
    destination.write_bytes(await file.read())
    try:
        imported = ingest(destination, meeting.path, config=svc.config)
    except UnsupportedAudio as exc:
        svc.meetings.discard(meeting.id)
        raise HTTPException(415, str(exc)) from exc
    svc.meetings.finish(meeting.id, duration_s=imported.duration_s)
    svc.events.publish("meeting", meeting_id=meeting.id, action="imported")
    return {
        "meeting_id": meeting.id,
        "file": str(destination),
        "chunks": len(imported.records),
        "duration_s": imported.duration_s,
        "state": svc.dao.require_meeting(meeting.id).state,
    }


# --------------------------------------------------------------------------- glossary


@router.get("/glossary")
def get_glossary(request: Request) -> dict[str, Any]:
    svc = services_of(request)
    return {
        "terms": [
            {
                "term": term.term,
                "kind": term.kind,
                "aliases": term.aliases,
                "note": term.note,
                "hits": term.hits,
            }
            for term in svc.dao.glossary()
        ]
    }


@router.put("/glossary")
def put_glossary(request: Request, body: GlossaryPut) -> dict[str, Any]:
    svc = services_of(request)
    for item in body.terms:
        svc.dao.upsert_term(
            GlossaryTerm(
                term=str(item["term"]),
                kind=item.get("kind"),
                aliases=item.get("aliases"),
                note=item.get("note"),
            )
        )
    return get_glossary(request)


# ----------------------------------------------------------------------------- audio


@router.get("/audio/devices")
def audio_devices(request: Request) -> dict[str, Any]:
    """Microphones the settings screen can offer. Never raises: a machine with no audio
    stack (or WSL) must still be able to open Settings."""
    from app.audio import monitor as meter

    svc = services_of(request)
    selected = svc.config.get("audio.input_device")
    meter_opens = meter.acquisitions()
    selected_output = svc.config.get("audio.output_device")
    outputs: list[dict[str, Any]] = []
    try:
        from app.audio.devices import (
            NoDeviceError,
            default_capture,
            default_render,
            list_inputs,
            list_outputs,
        )

        def describe(items: Any, fallback: int | None) -> list[dict[str, Any]]:
            return [
                {
                    "index": device.index,
                    "name": device.name,
                    "rate": device.rate,
                    "channels": device.channels,
                    "is_default": device.index == fallback,
                }
                for device in items
            ]

        try:
            capture_default: int | None = default_capture().index
        except NoDeviceError:
            capture_default = None
        try:
            render_default: int | None = default_render().index
        except NoDeviceError:
            render_default = None
        devices = describe(list_inputs(), capture_default)
        outputs = describe(list_outputs(), render_default)
        error = None
    except Exception as exc:
        devices, outputs, error = [], [], str(exc)
    return {
        "devices": devices,
        "outputs": outputs,
        "selected": None if selected is None else int(selected),
        "selected_output": None if selected_output is None else int(selected_output),
        "error": error,
        # Which machine actually answered. An empty list on "linux" means the request
        # reached a WSL instance, not the Windows app — a distinction that has already
        # cost a day once.
        "platform": sys.platform,
        # Diagnostics: one open per Settings visit is healthy. A climbing number means
        # something is reopening the device in a loop.
        "meter_opens": meter_opens,
        "capture": str(svc.config.get("audio.capture")),
    }


@router.get("/audio/level")
async def audio_level(
    request: Request, device: int | None = None, track: str = "me"
) -> StreamingResponse:
    """Server-sent input levels for the Settings meters.

    ``me`` is the microphone, ``them`` the system-audio loopback. They are different
    endpoints, so both can be metered at once — which is also the quickest way to see
    crosstalk: if both bars move when only the far side is talking, the microphone is
    picking up system audio.

    While a meeting is recording the endpoints are already open, so this reports the
    recorder's own levels rather than fighting it for them.
    """
    svc = services_of(request)
    if track not in ("me", "them"):
        raise HTTPException(404, "no such track")
    recorder = svc.recorder

    async def stream() -> Any:
        from app.audio import monitor as meter

        holding: Any = None  # the monitor this client joined
        deadline = time.monotonic() + LEVEL_MAX_S
        try:
            while time.monotonic() < deadline:
                # Armed counts, not only recording: a woken detector holds both endpoints
                # for the pre-roll, and a meter reopening them would fight it (job 013).
                recording = recorder is not None and recorder.armed
                if recording:
                    # The recorder owns the endpoint now. Drop the preview stream rather
                    # than hold a second one open, and report the level being recorded.
                    if holding is not None:
                        await asyncio.to_thread(meter.release, track, holding)
                        holding = None
                    assert recorder is not None
                    level = float(recorder.levels().get(track, 0.0))
                    yield _level_event(level, level, source="recorder")
                else:
                    if holding is None:
                        try:
                            holding = await asyncio.to_thread(
                                meter.acquire, svc.config, device if track == "me" else None, track
                            )
                        except Exception as exc:
                            yield _sse({"error": str(exc)})
                            return
                    current = meter.active(track)
                    if current is None or current is not holding:
                        # Closed for the recorder, or replaced for another device: join
                        # whatever runs now rather than read a stream this client left.
                        holding = None
                        await asyncio.sleep(LEVEL_INTERVAL_S)
                        continue
                    reading = current.read()
                    if reading.error:
                        yield _sse({"error": reading.error})
                        return
                    yield _level_event(
                        reading.rms, reading.peak, source="monitor", clipped=reading.clipped
                    )
                await asyncio.sleep(LEVEL_INTERVAL_S)
            # Tell the client this was deliberate, so it closes instead of reconnecting.
            yield _sse({"done": True})
        finally:
            if holding is not None:
                # Synchronously, NOT via `await asyncio.to_thread`: a browser closing the
                # tab cancels this task, and awaiting anything in a cancelled task raises
                # CancelledError at the await — so the release never ran and the
                # microphone stayed open until the backstop fired. The monitor thread
                # polls its stop flag every 50 ms, so this returns promptly.
                meter.release(track, holding)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/recording/levels")
async def recording_levels(request: Request) -> StreamingResponse:
    """Both tracks' levels while a meeting records, for the live waveform.

    Deliberately not ``/audio/level``: that one falls back to opening a preview stream the
    moment the recording ends, so a waveform left on screen would take the microphone.
    This one only ever reads the recorder, and says it is done when the recording is.
    """
    recorder = services_of(request).recorder

    async def stream() -> Any:
        while recorder is not None and recorder.committed:
            levels = recorder.levels()
            yield _sse(
                {
                    "me": round(float(levels.get("me", 0.0)), 4),
                    "them": round(float(levels.get("them", 0.0)), 4),
                    "paused": bool(recorder.paused),
                }
            )
            await asyncio.sleep(LEVEL_INTERVAL_S)
        yield _sse({"done": True})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


# --------------------------------------------------------------------------- settings


@router.get("/settings")
def get_settings(request: Request) -> dict[str, Any]:
    """Only ever the redacted dump — no secret ever reaches the DOM.

    ``pinned`` names the keys the environment is holding down. Without it a control the
    launcher has overridden looks like any other: it saves, it reads back, and it is
    quietly replaced again on the next start.
    """
    svc = services_of(request)
    return {
        "config": svc.config.redacted_dump(),
        "warnings": svc.config.warnings(),
        "pinned": svc.config.env_pinned(),
    }


def refuse_unusable_provider(svc: Services, values: dict[str, Any]) -> None:
    """A subscription provider without its CLI fails hours later, not now.

    Nothing else rejects the selection, so the failure would surface in the summarize
    stage — at the end of a meeting already recorded and transcribed. Refusing the
    selection costs a click; refusing it later costs the summary. A fallback naming one
    is held to the same test: a fallback that cannot run is no fallback.
    """
    for key in ("llm.provider", "llm.fallback_provider"):
        chosen = values.get(key)
        if chosen not in CLI_PROVIDERS:
            continue
        if not cli_client(svc, str(chosen)).status().installed:
            product = CLI_PROVIDERS[str(chosen)]
            raise HTTPException(
                409,
                f"{product} is not installed on this machine. Install it first from "
                "Settings, AI agents — the app never handles your credentials.",
            )


@router.put("/settings")
def put_settings(request: Request, body: SettingsPut) -> dict[str, Any]:
    svc = services_of(request)
    refuse_unusable_provider(svc, body.values)
    for dotted, value in body.values.items():
        svc.config.set(dotted, value)
    svc.config.validate()
    svc.config.save()
    svc.events.publish("settings", changed=sorted(body.values))
    if (
        "updates.channel" in body.values
        and svc.updates is not None
        and svc.config.get("updates.check", True)
    ):
        # Joining beta should show what beta has now, not in six hours (D87).
        svc.updates.check_in_background()
    return get_settings(request)


# --------------------------------------------------------------------------- llm


PROBE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ok"],
    "properties": {"ok": {"type": "boolean"}},
}

#: The providers that run a CLI the user installs and signs into, and the product each
#: one needs installed — named as the software it is, because that is what gets
#: installed. Removing Codex from ``LLM_PROVIDERS`` removes it here too.
CLI_PROVIDERS: dict[str, str] = {
    provider: product
    for provider, product in (
        ("claude-subscription", "Claude Code"),
        ("codex-subscription", "Codex"),
        ("antigravity-subscription", "Antigravity CLI"),
    )
    if provider in LLM_PROVIDERS
}


def cli_client(svc: Services, provider: str) -> Any:
    """The CLI-backed client for ``provider``: Claude Code's, Codex's or Antigravity's."""
    if provider == "codex-subscription":
        from app.llm.codex_cli import CodexCliClient

        return CodexCliClient(svc.config)
    if provider == "antigravity-subscription":
        from app.llm.antigravity_cli import AntigravityCliClient

        return AntigravityCliClient(svc.config)
    from app.llm.claude_cli import ClaudeCliClient

    return ClaudeCliClient(svc.config)


def cli_module(provider: str) -> Any:
    """The module holding the install, sign-in and update helpers for ``provider``."""
    if provider == "codex-subscription":
        from app.llm import codex_cli

        return codex_cli
    if provider == "antigravity-subscription":
        from app.llm import antigravity_cli

        return antigravity_cli
    from app.llm import claude_cli

    return claude_cli


def chosen_cli(body: ProviderPost | None) -> str:
    """Which CLI a sign-in, install or update is for. Claude when unsaid, as it always was."""
    provider = (body.provider if body else None) or "claude-subscription"
    if provider not in CLI_PROVIDERS:
        raise HTTPException(400, f"{provider!r} is not a provider this app installs or signs in to")
    return provider


@router.put("/settings/secrets")
def put_secrets(request: Request, body: SecretsPut) -> dict[str, Any]:
    """Write-only. A secret goes to the OS credential store and is never readable back."""
    from app.config import SECRET_NAMES

    svc = services_of(request)
    changed: list[str] = []
    for name, value in body.values.items():
        if name not in SECRET_NAMES:
            raise HTTPException(400, f"unknown secret {name!r}")
        if value:
            svc.config.secrets.set(name, value)
        else:
            svc.config.secrets.delete(name)
        changed.append(name)
    svc.events.publish("settings", changed=[f"secret:{name}" for name in sorted(changed)])
    return secret_status(request)


@router.get("/settings/secrets")
def secret_status(request: Request) -> dict[str, Any]:
    """Which secrets exist — booleans only, never the values."""
    from app.config import SECRET_NAMES

    svc = services_of(request)
    return {"secrets": {name: bool(svc.config.secrets.get(name)) for name in SECRET_NAMES}}


# --------------------------------------------------------------------------- calendar


def _event_or_none(svc: Services, account_id: str | None, calendar_id: str, event_id: str) -> Any:
    try:
        return _event_lookup(svc, account_id, calendar_id, event_id)
    except HTTPException:
        return None


def _seed_accounts(request: Request, body: dict[str, Any]) -> list[str]:
    """Test seeding: the calendar accounts a spec asks for, pinned as connected and never
    synced. Events and meetings that name no account go to the first; without any named,
    one default account exists as soon as a spec seeds calendar data."""
    calendar = calendar_of(request)
    wanted = list(body.get("calendar_accounts") or [])
    needs_default = body.get("calendar_events") or any(
        m.get("calendar") for m in body.get("meetings", []) or []
    )
    if not wanted and needs_default and not calendar.registry.accounts():
        wanted = [{"address": "you@example.com"}]
    for item in wanted:
        account, _ = calendar.registry.add_or_restore(str(item["address"]))
        if item.get("id") and item["id"] != account.id:
            with transaction(calendar.registry.conn):
                calendar.registry.conn.execute("PRAGMA defer_foreign_keys = ON")
                calendar.registry.conn.execute(
                    "UPDATE calendar_sources SET account_id = ? WHERE account_id = ?",
                    (item["id"], account.id),
                )
                calendar.registry.conn.execute(
                    "UPDATE calendar_accounts SET id = ? WHERE id = ?", (item["id"], account.id)
                )
            account = calendar.registry.get(str(item["id"]))
        if item.get("visible") is False:
            calendar.registry.set_visible(account.id, False)
        if item.get("removed"):
            calendar.registry.remove(account.id)
        calendar.pin(account.id)
    return [a.id for a in calendar.registry.accounts(include_removed=True)]


def calendar_of(request: Request) -> Any:
    """The Google accounts, made on first use: most runs never touch them."""
    svc = services_of(request)
    if svc.calendar is None:
        from app.gcal.accounts import AccountRegistry
        from app.gcal.oauth import CalendarAccounts

        svc.calendar = CalendarAccounts(
            svc.config.secrets,
            AccountRegistry(svc.conn, svc.clock),
            publish=lambda **payload: svc.events.publish("calendar", **payload),
        )
    return svc.calendar


def _active(svc: Services) -> frozenset[str]:
    """The accounts whose events are shown and matched: connected, neither hidden nor
    removed (D82). None at all without a calendar connection."""
    return svc.calendar.active_ids() if svc.calendar is not None else frozenset()


def _event_lookup(svc: Services, account_id: str | None, calendar_id: str, event_id: str) -> Any:
    """One cached event of an active account. A caller that names no account (an older
    toast link) gets the one active account that has it; two is a question to ask."""
    store = _event_store(svc)
    active = _active(svc)
    if account_id:
        return store.get(account_id, calendar_id, event_id) if account_id in active else None
    found = [e for a in sorted(active) if (e := store.get(a, calendar_id, event_id))]
    if len(found) > 1:
        raise HTTPException(400, "this event is on more than one account: name the account")
    return found[0] if found else None


def _user_snapshot(svc: Services, event: Any, source: str = "user") -> dict[str, Any]:
    """An event as the user matched it: final, and on every account that has a copy."""
    from app.gcal.source import snapshot

    copies = _event_store(svc).copies(event, accounts=_active(svc))
    return snapshot(event, state="matched", source=source, confidence=1.0, accounts=copies)


def _start_payload(svc: Services, event: Any) -> dict[str, Any] | None:
    """The calendar meeting a recording starting now belongs to (D89).

    - Named by the start: the user's pick, unless it is just the detector's offer started
      as offered. Then it is the detector's call: matched, but a later confident match
      may correct it, and the user can.
    - Not named, while meetings booked at the same time are on offer: those, proposed.
    - Not named: the detector's reading of the call on now (window titles, the call's
      app), or nothing, and the calendar match at creation decides.
    """
    offer = svc.prompts.current if svc.prompts is not None else None
    if event is not None:
        offered = (
            offer is not None
            and offer.kind == "detected"
            and offer.names(event.calendar_id, event.event_id)
        )
        return _user_snapshot(svc, event, "detected" if offered else "user")
    if offer is not None and offer.candidates:
        from app.gcal.source import proposal

        events = [
            found
            for account, calendar, event_id, _ in offer.candidates
            if (found := _event_or_none(svc, account, calendar, event_id)) is not None
        ]
        if events:
            return proposal(events, source="detected", reason="booked at the same time")
    detector = svc.detector
    if detector is not None and hasattr(detector, "assignment_now"):
        payload: dict[str, Any] | None = detector.calendar_payload(detector.assignment_now())
        return payload
    return None


@router.get("/calendar/status")
def calendar_status(request: Request) -> dict[str, Any]:
    """The connected Google accounts and how each is syncing. Never a token.

    Removed accounts are not listed (they are kept in the database, D82)."""
    svc = services_of(request)
    status: dict[str, Any] = calendar_of(request).status()
    if svc.calendar_sync is not None:
        status.update(svc.calendar_sync.status())
        for account in status.get("accounts") or []:
            account.update(svc.calendar_sync.status(account["id"]))
    # Whether connecting opens Google's page from here (Windows) or the page opens it.
    status["opens_externally"] = sys.platform == "win32"
    return status


class CalendarChoice(BaseModel):
    """The event a recording belongs to, as the user picked it; or ``none``."""

    account_id: str | None = None
    calendar_id: str | None = None
    event_id: str | None = None
    none: bool = False


class AccountPatch(BaseModel):
    visible: bool


def _event_store(svc: Services) -> Any:
    from app.gcal.events import EventStore

    return svc.calendar_sync.store if svc.calendar_sync is not None else EventStore(svc.conn)


@router.get("/calendar/events")
def calendar_events(
    request: Request,
    frm: str = Query(..., alias="from"),
    to: str = Query(...),
) -> dict[str, Any]:
    """Cached events overlapping [from, to), each with the recording matched to it.

    ``from``/``to`` are local dates (YYYY-MM-DD) or full ISO instants. Served from the
    cache only, so the calendar view works offline and never waits on Google. Only the
    active accounts' events; the same meeting on several of them is one event, with
    ``accounts`` naming them all.
    """
    from datetime import datetime as dt

    from app.gcal.events import iso_utc
    from app.gcal.match import dedupe
    from app.gcal.source import SEARCH_MARGIN

    svc = services_of(request)

    def instant(text: str) -> Any:
        parsed = dt.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.astimezone()  # a date: local midnight

    start, end = instant(frm), instant(to)
    store = _event_store(svc)
    active = _active(svc)
    events = store.between(start, end, accounts=active)
    copies: dict[tuple[str, str], list[Any]] = {}
    for event in events:
        copies.setdefault(event.occurrence, []).append(event)
    shown = sorted(dedupe(events), key=lambda e: (e.start, e.account_id, e.event_id))
    # Matching a recording needs the events around it, not only the ones in view: a
    # recording that began before midnight belongs to an event that began before it too.
    nearby = list(store.between(start - SEARCH_MARGIN, end + SEARCH_MARGIN, accounts=active))
    matched: dict[tuple[str, str, str], str] = {}
    for meeting in svc.dao.list_meetings(frm=iso_utc(start - timedelta(days=1)), limit=2000):
        payload = _calendar_of(svc, meeting, nearby)
        ref = payload.get("event") or {}
        if (payload.get("match") or {}).get("state") == "matched" and ref:
            for account in payload.get("accounts") or [ref.get("account_id")]:
                key = (str(account), str(ref.get("calendar_id")), str(ref.get("event_id")))
                matched[key] = meeting.id
    out = []
    for event in shown:
        group = copies.get(event.occurrence, [event])
        meeting_id = next((matched[e.key] for e in group if e.key in matched), None)
        accounts = sorted({e.account_id for e in group})
        out.append({**event.as_api(), "accounts": accounts, "meeting_id": meeting_id})
    return {"events": out}


@router.post("/calendar/sync")
def calendar_sync_now(request: Request) -> dict[str, Any]:
    svc = services_of(request)
    if svc.calendar_sync is not None:
        svc.calendar_sync.kick()
    return calendar_status(request)


@router.patch("/calendar/accounts/{account_id}")
def calendar_account_patch(request: Request, account_id: str, body: AccountPatch) -> dict[str, Any]:
    """Show or hide an account. Hidden, everything of it is hidden everywhere in Upshot,
    and nothing is deleted (D82)."""
    calendar = calendar_of(request)
    account = calendar.registry.get(account_id)
    if account is None or account.removed:
        raise HTTPException(404, "no such calendar account")
    calendar.set_visible(account_id, body.visible)
    return calendar_status(request)


@router.delete("/calendar/accounts/{account_id}")
def calendar_account_remove(request: Request, account_id: str) -> dict[str, Any]:
    """Remove an account: its access is revoked at Google, and it is hidden for good.
    Its events and its recordings stay, and connecting the same address brings them back.
    Says so when the revoke could not be sent."""
    calendar = calendar_of(request)
    account = calendar.registry.get(account_id)
    if account is None or account.removed:
        raise HTTPException(404, "no such calendar account")
    result = calendar.remove(account_id)
    return {**result, **calendar_status(request)}


@router.post("/calendar/accounts/{account_id}/reconnect")
def calendar_account_reconnect(request: Request, account_id: str) -> dict[str, Any]:
    """Sign in again to an account whose connection stopped working."""
    return _start_connect(request, reconnect=account_id)


@router.get("/meetings/{meeting_id}/calendar")
def meeting_calendar(request: Request, meeting_id: str) -> dict[str, Any]:
    """What this recording is matched to, and the events it could be matched to instead."""
    svc = services_of(request)
    meeting = svc.dao.visible_meeting(meeting_id)
    if meeting is None:
        raise HTTPException(404, "no such meeting")
    started = _instant(meeting.started_at)
    ended = _instant(meeting.ended_at) if meeting.ended_at else started + timedelta(hours=1)
    nearby = _event_store(svc).between(
        started - timedelta(hours=2), ended + timedelta(hours=2), accounts=_active(svc)
    )
    return {
        "calendar": _calendar_of(svc, meeting) or None,
        "candidates": [e.as_api() for e in nearby if not e.all_day and not e.declined],
    }


@router.get("/meetings/{meeting_id}/invite")
def meeting_invite(request: Request, meeting_id: str) -> dict[str, Any]:
    """The invitation behind this recording, read from Google now — never from a store.

    The agenda, the links in it and the files attached to it live in the calendar, which
    is where they are maintained. Editing the event changes what this returns; deleting
    it takes it away. It is read with the token of the account the event was matched on.
    """
    from app.gcal.oauth import CalendarAuthError, CalendarUnavailable
    from app.meetings import event_account

    svc = services_of(request)
    meeting = svc.dao.visible_meeting(meeting_id)
    if meeting is None:
        raise HTTPException(404, "no such meeting")
    stored = bool(meeting.calendar_json)
    payload = _calendar_of(svc, meeting)
    ref = payload.get("event") or {}
    if (payload.get("match") or {}).get("state") != "matched" or not ref:
        # Not an error. A manually started recording normally has no invitation, and
        # an error-toned line on every healthy meeting trains the reader to ignore all
        # of them — including the ones that mean something. `code` lets the page tell
        # the difference without matching on prose.
        return {"available": False, "code": "unmatched", "reason": "no calendar event is matched"}
    if svc.calendar_invites is None:
        return {
            "available": False,
            "code": "no_connection",
            "reason": "no calendar connection in this process",
        }
    account = event_account(meeting) if stored else ref.get("account_id")
    if svc.calendar is not None and not svc.calendar.connected():
        # Asked and answered before Google is: never having connected an account is not
        # a fault to report on every meeting, and it reads as one. A connection that has
        # *stopped* working still comes back below, as "auth", because that one is news.
        return {
            "available": False,
            "code": "no_connection",
            "reason": "no Google account is connected",
        }
    if not account:
        return {"available": False, "code": "unmatched", "reason": "no calendar event is matched"}
    known = svc.calendar.registry.get(str(account)) if svc.calendar is not None else None
    if known is None or not known.shown:
        # Still shown through another account it is on, but the event's own account is
        # removed or hidden: that account is not asked (D82).
        return {
            "available": False,
            "code": "account_gone",
            "reason": "the event is on a calendar account that is not connected",
        }
    try:
        invite = svc.calendar_invites.fetch(
            str(account), str(ref["calendar_id"]), str(ref["event_id"])
        )
    except CalendarAuthError as exc:
        return {"available": False, "code": "auth", "reason": str(exc), "reconnect": True}
    except CalendarUnavailable as exc:
        reason = f"Google could not be reached ({exc})."
        return {"available": False, "code": "offline", "reason": reason}
    except Exception as exc:  # a deleted event answers 404; that is an answer, not a fault
        log.info("invite unavailable for %s: %s", meeting_id, exc)
        reason = "this event is no longer in the calendar"
        return {"available": False, "code": "deleted", "reason": reason}
    return {"available": True, "invite": invite.as_api()}


@router.put("/meetings/{meeting_id}/calendar")
def choose_meeting_event(request: Request, meeting_id: str, body: CalendarChoice) -> dict[str, Any]:
    """The user says which event this recording was, or that it was none."""
    svc = services_of(request)
    survivor = _assign(
        svc,
        meeting_id,
        account_id=body.account_id,
        calendar_id=body.calendar_id,
        event_id=body.event_id,
        none=body.none,
    )
    return _meeting_payload(svc, survivor)


def _assign(
    svc: Services,
    meeting_id: str,
    *,
    account_id: str | None,
    calendar_id: str | None,
    event_id: str | None,
    none: bool,
) -> str:
    """The user's answer to "which meeting was this?" (D89). The recording it leaves: this
    one, or the earlier recording of the same meeting it was merged into.

    A different calendar meeting means a different title and participants, so a summary
    already written is written again (when an AI is configured). Another recording of
    the meeting picked is merged with this one: one meeting, one recording.
    """
    meeting_id = svc.dao.resolve(meeting_id)
    meeting = svc.dao.visible_meeting(meeting_id)
    if meeting is None:
        raise HTTPException(404, "no such meeting")
    from app.meetings import calendar_payload, event_key

    before = event_key(calendar_payload(meeting))
    if none:
        svc.meetings.choose_event(meeting_id, None)
    else:
        if not calendar_id or not event_id:
            raise HTTPException(400, "name the event, or say none")
        event = _event_lookup(svc, account_id, calendar_id, event_id)
        if event is None:
            raise HTTPException(404, "that event is not in the calendar cache")
        svc.meetings.choose_event(meeting_id, _user_snapshot(svc, event))
    survivor = meeting_id
    merged = svc.meetings.merge_into_existing(meeting_id) if not none else None
    if merged is not None:
        survivor = merged.id
    elif event_key(calendar_payload(svc.dao.require_meeting(meeting_id))) != before:
        svc.meetings.resummarize(meeting_id)
    svc.events.publish("meeting", meeting_id=survivor, action="calendar")
    return survivor


class MergePost(BaseModel):
    #: The other recording of the same meeting.
    other: str


@router.post("/meetings/{meeting_id}/merge")
def merge_meetings(request: Request, meeting_id: str, body: MergePost) -> dict[str, Any]:
    """Join two recordings of one meeting (D89). The earlier one is kept; the later one's
    id goes on finding it."""
    from app.merge import MergeRefused

    svc = services_of(request)
    first, second = svc.dao.resolve(meeting_id), svc.dao.resolve(body.other)
    for each in (first, second):
        _visible(svc, each)
    try:
        merged = svc.meetings.merge(first, second)
    except MergeRefused as exc:
        raise HTTPException(409, str(exc)) from exc
    except OSError as exc:
        # The disk refused (full, or a file held open): both stay as they were.
        log.warning("merging %s and %s failed: %s", meeting_id, body.other, exc)
        raise HTTPException(409, f"could not merge: {exc}") from exc
    svc.events.publish("meeting", meeting_id=merged.id, action="merged")
    return _meeting_payload(svc, merged.id)


def _start_connect(request: Request, *, reconnect: str | None = None) -> dict[str, Any]:
    from app import window
    from app.gcal.oauth import CalendarAuthError

    calendar = calendar_of(request)
    try:
        auth_url = calendar.start(reconnect=reconnect)
    except KeyError as exc:
        raise HTTPException(404, "no such calendar account") from exc
    except CalendarAuthError as exc:
        raise HTTPException(409, str(exc)) from exc
    opened = window.open_external(auth_url)
    return {**calendar_status(request), "auth_url": auth_url, "opened": opened}


@router.post("/calendar/connect")
def calendar_connect(request: Request) -> dict[str, Any]:
    """Add a Google account: start a connection, and open Google's page in the user's
    default browser. The account Google returns is added, or restored when its address
    was connected before (D82); no other account is touched.

    Opened from here, not by the page: the page is Upshot's window, which runs in a browser
    profile of its own, so a tab it opened would have none of the user's Google accounts.
    Windows opens it in the profile the user was last in (z8tj1hca86). ``opened`` false
    (not Windows): the page opens ``auth_url`` itself.
    """
    return _start_connect(request)


@router.post("/calendar/cancel")
def calendar_cancel(request: Request) -> dict[str, Any]:
    calendar_of(request).cancel()
    return calendar_status(request)


@router.get("/llm/status")
def llm_status(request: Request) -> dict[str, Any]:
    """What each summarization provider needs, and whether it has it."""
    svc = services_of(request)

    def has(name: str, env: str) -> bool:
        return bool(svc.config.secret(name, env=env))

    providers: list[dict[str, Any]] = [
        # Transcripts only (D63): nothing to set up, so always ready.
        {"id": "none", "label": "Transcripts only", "needs": "none", "ready": True},
        {
            "id": "anthropic",
            "label": "Claude (API key)",
            "needs": "key",
            "ready": has("anthropic", "ANTHROPIC_API_KEY"),
            "console": "https://console.anthropic.com/settings/keys",
        },
        {
            "id": "gemini",
            "label": "Gemini (API key, free tier)",
            "needs": "key",
            "ready": has("gemini", "GEMINI_API_KEY"),
            "console": "https://aistudio.google.com/apikey",
        },
        {
            "id": "openai",
            "label": "OpenAI (API key)",
            "needs": "key",
            "ready": has("openai", "OPENAI_API_KEY"),
            "console": "https://platform.openai.com/api-keys",
        },
    ]
    if "claude-subscription" in CLI_PROVIDERS:
        # Not "Claude Code": Anthropic's Agent SDK branding guidance permits "Claude
        # Agent" and "Claude" but not the product name, and asks that a third-party
        # product not appear to be Claude Code. The install and sign-in copy still names
        # Claude Code, because that is the thing to install.
        providers.append(
            cli_row(svc, "claude-subscription", "Claude Agent (your own subscription)")
        )
    if "codex-subscription" in CLI_PROVIDERS:
        # OpenAI's brand page lets a developer "truthfully identify the OpenAI technology
        # you use" but keeps OpenAI's brands out of the app's own name (D58). This names
        # the program actually run, Codex CLI, and the plan it spends — the way D46 names
        # "Claude Agent" rather than presenting the row as Claude Code itself.
        providers.append(cli_row(svc, "codex-subscription", "Codex CLI (your own ChatGPT plan)"))
    if "antigravity-subscription" in CLI_PROVIDERS:
        # Named the way Codex's row is (D58, D79): the program actually run and the plan
        # it spends, with no Google mark and nothing that reads as a Google product.
        providers.append(
            cli_row(svc, "antigravity-subscription", "Antigravity CLI (your own Google AI plan)")
        )
    providers.append(
        {
            "id": "ollama",
            "label": "Local (Ollama)",
            "needs": "ollama",
            "ready": True,
            "detail": str(svc.config.get("llm.ollama_url")),
        }
    )
    return {
        "active": str(svc.config.get("llm.provider")),
        # Used only when a subscription reports its allowance spent; "" is none.
        "fallback": str(svc.config.get("llm.fallback_provider", "") or ""),
        "providers": providers,
    }


def cli_row(svc: Services, provider: str, label: str) -> dict[str, Any]:
    """One CLI-backed provider's Settings row: installed, signed in, and how to fix it."""
    module = cli_module(provider)
    cli = cli_client(svc, provider).status()
    # Only probed when there is something to install: winget's own start-up is slow
    # enough to be felt on every settings load otherwise.
    plan = module.install_plan() if not cli.installed else None
    return {
        "id": provider,
        "label": label,
        "needs": "cli",
        "ready": cli.installed,
        # Three-valued on purpose: `None` means the build is too old to be asked,
        # which is a different thing from being signed out and reads differently.
        "signed_in": cli.signed_in,
        "account": cli.account,
        # The binary actually resolved, so the hint below can be pinned to it.
        "path": cli.path,
        "detail": cli.version or cli.detail,
        "can_install": plan is not None,
        # Shown beside the button, so the command is disclosed before it is clicked.
        "install_command": plan.display if plan else "",
        "install_method": plan.method if plan else "",
        "install_docs": module.INSTALL_DOCS_URL,
        "update_hint": module.update_command(cli.path) if cli.installed else "",
        # False for a CLI with no way to sign out from outside it (Antigravity, D79).
        "can_sign_out": cli_client(svc, provider).logout_command() is not None
        or hasattr(module, "logout_console"),
        # Sign out opens a window where the user types the CLI's own command (Antigravity).
        "signout_in_window": cli_client(svc, provider).logout_command() is None
        and hasattr(module, "logout_console"),
        # The window Install or Sign in opened is still there; closing it ends the wait.
        "console_open": console_open(provider),
        # A windowless sign-in's link, for the page to offer in the browser it runs in.
        "signin_url": signin_url(provider),
        # Neither CLI reports its remaining allowance without an interactive session
        # (Codex shows it only inside its TUI's /status), and polling it would spend
        # what it measures. Null until one can say so cheaply.
        "quota": None,
    }


#: The console most recently launched for each CLI provider. Kept so the Settings row can
#: tell a login still in progress from one whose window the user closed: without it the
#: page waited out its whole timeout with a busy Sign in button that could not be pressed
#: again (closing the window mid-login on 2026-09-25 left it spinning until a reload).
_CONSOLES: dict[str, Any] = {}


#: Sign-ins running with no window (``app/llm/signin.py``), one per CLI provider.
_LOGINS: dict[str, Any] = {}


def console_open(provider: str) -> bool:
    """Is the install or sign-in launched for ``provider`` still going?

    A window still open, or a windowless sign-in still running: either way the row keeps
    waiting, and once neither is, it stops.
    """
    process = _CONSOLES.get(provider)
    login = _LOGINS.get(provider)
    return (process is not None and process.poll() is None) or (
        login is not None and login.running()
    )


def signin_url(provider: str) -> str:
    """The link a windowless sign-in printed, while it is still waiting for the browser."""
    login = _LOGINS.get(provider)
    return str(login.url) if login is not None and login.running() and login.url else ""


def launch_console(
    command: list[str],
    failure: str,
    *,
    provider: str = "claude-subscription",
    visible: bool = True,
) -> dict[str, Any]:
    r"""Run an interactive command in a console of its own, and report what ran.

    The working directory is the point. Inheriting this process's own means, under the
    Windows launcher, a source tree on a ``\\wsl.localhost`` UNC path — where Claude
    Code's startup watch of ``.claude/`` dies with ``EISDIR`` before a login can be
    shown — and, after an Inno install, the program directory itself.

    A frozen build owns no console (``console=False``), so an interactive child is given
    one explicitly rather than inheriting anything.

    Two attempts, because ``CreateProcess`` has been seen refusing the first with
    ``WinError 5`` while the second works: the interpreter is resolved by name, then by
    absolute path. Every attempt is logged with its full context — a launch that fails
    silently is indistinguishable from a button that does nothing, and that is exactly
    how the last one was reported.
    """
    import subprocess

    from app.llm.claude_cli import creation_flags

    module = cli_module(provider)
    child_env, workdir = module.child_env, module.workdir
    if sys.platform != "win32":
        # No assumption about which terminal emulator exists; the UI shows the command.
        return {"launched": False, "command": " ".join(command)}

    where = workdir()
    attempts: list[list[str]] = [command]
    absolute = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32"
    absolute = absolute / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if command and command[0].lower() == "powershell.exe" and absolute.exists():
        attempts.append([str(absolute), *command[1:]])

    problems: list[str] = []
    for index, argv in enumerate(attempts, start=1):
        try:
            _CONSOLES[provider] = subprocess.Popen(
                argv,
                cwd=where,
                env=child_env(),
                # Setup installs in the background (D75): no window to take the focus
                # or to frighten anyone; the page shows the progress instead.
                creationflags=creation_flags(visible=visible),
            )
        except Exception as exc:
            problems.append(f"{argv[0]}: {exc}")
            log.error(
                "console launch attempt %d/%d failed: exe=%s cwd=%s args=%d chars: %s",
                index,
                len(attempts),
                argv[0],
                where,
                len(" ".join(argv)),
                exc,
                exc_info=True,
            )
            continue
        log.info("launched %s in %s (attempt %d)", argv[0], where, index)
        return {"launched": True, "command": " ".join(command)}

    raise HTTPException(500, f"{failure}: {'; '.join(problems)}")


@router.get("/llm/prompt")
def llm_prompt(request: Request) -> dict[str, Any]:
    """The summarizing prompt: the one in force, and the shipped one to revert to."""
    from app.llm.prompts import load as load_prompt

    svc = services_of(request)
    shipped = load_prompt("system")
    custom = svc.config.get("llm.summary_prompt")
    custom_text = custom.strip() if isinstance(custom, str) and custom.strip() else ""
    return {
        "text": custom_text or shipped.text,
        "default": shipped.text,
        "custom": bool(custom_text),
        "version": shipped.version,
    }


def console_log(provider: str, action: str) -> str:
    """Where the install or sign-in window for ``provider`` is recorded (console_log.py)."""
    from app.llm.console_log import log_path

    return str(log_path(f"{provider.removesuffix('-subscription')}-{action}"))


@router.post("/llm/signin")
def llm_signin(request: Request, body: ProviderPost | None = None) -> dict[str, Any]:
    """Launch the vendor's own login. We never see the credential it creates."""
    provider = chosen_cli(body)
    module = cli_module(provider)
    product = CLI_PROVIDERS[provider]
    client = cli_client(services_of(request), provider)
    if client.resolve() is None:
        raise HTTPException(
            409,
            f"{product} is not installed. Install it, then sign in — the app never "
            "handles your credentials.",
        )
    if (body is not None and body.background) or not getattr(module, "SIGNIN_CONSOLE", True):
        return hidden_signin(provider, client.login_command(), module)
    return launch_console(
        module.login_console(client.login_command()),
        f"could not launch {product}",
        provider=provider,
    ) | {"log": console_log(provider, "signin")}


def hidden_signin(provider: str, argv: list[str], module: Any) -> dict[str, Any]:
    """Run the login with no window and return the link it prints (``app/llm/signin.py``).

    A second Sign in replaces the first: the old one still holds the callback port, and
    the user pressing the button again means they have given up on it.
    """
    from app.llm.console_log import log_path
    from app.llm.signin import HiddenLogin

    product = CLI_PROVIDERS[provider]
    previous = _LOGINS.pop(provider, None)
    if previous is not None:
        previous.stop()
    name = f"{provider.removesuffix('-subscription')}-signin"
    try:
        login = HiddenLogin(
            argv,
            url_pattern=module.LOGIN_URL,
            log_file=log_path(name),
            cwd=module.workdir(),
            env=module.child_env(),
        )
    except OSError as exc:
        log.error("hidden sign-in for %s failed to start: %s", provider, exc, exc_info=True)
        raise HTTPException(500, f"could not start the {product} sign-in: {exc}") from exc
    _LOGINS[provider] = login
    url = login.wait_for_url()
    if url is None and not login.running():
        tail = " ".join(login.lines[-3:]) or "it printed nothing"
        log.error("hidden sign-in for %s exited without a link: %s", provider, tail)
        raise HTTPException(500, f"the {product} sign-in stopped before it began: {tail}")
    log.info("hidden sign-in for %s started (pid %d)", provider, login.process.pid)
    return {
        "launched": True,
        "command": " ".join(argv),
        "url": url or "",
        "log": console_log(provider, "signin"),
    }


@router.post("/llm/signout")
def llm_signout(request: Request, body: ProviderPost | None = None) -> dict[str, Any]:
    """Sign the CLI out, with the vendor's own command. Its sign-in, if one waits, ends too.

    The command runs with no window: it asks nothing. Summaries on that plan stop until
    the user signs in again, which the row then offers.
    """
    provider = chosen_cli(body)
    product = CLI_PROVIDERS[provider]
    client = cli_client(services_of(request), provider)
    if client.resolve() is None:
        raise HTTPException(409, f"{product} is not installed, so there is nothing to sign out of.")
    login = _LOGINS.pop(provider, None)
    if login is not None:
        login.stop()
    argv = client.logout_command()
    if argv is None:
        module = cli_module(provider)
        if not hasattr(module, "logout_console"):
            raise HTTPException(
                409,
                f"{product} cannot be signed out from here. Run it in a terminal and type /logout.",
            )
        # Its sign-out only works typed into the program itself (Antigravity, D79): a
        # window that runs it and says what to type. The row watches the window close.
        path = client.resolve() or client.executable
        return launch_console(
            module.logout_console(path), f"could not start {product}", provider=provider
        ) | {"signed_out": False, "log": console_log(provider, "signout")}
    try:
        code, out, err = client.runner(argv, "", 60.0)
    except Exception as exc:
        log.error("sign-out of %s failed to run: %s", provider, exc, exc_info=True)
        raise HTTPException(500, f"could not sign out of {product}: {exc}") from exc
    log.info("signed out of %s (exit %d)", provider, code)
    signed_in = client.status().signed_in
    if signed_in is True:
        detail = (err or out).strip() or f"exit code {code}"
        raise HTTPException(500, f"{product} is still signed in after signing out: {detail}")
    return {"signed_out": True}


@router.post("/llm/signin/code")
def llm_signin_code(request: Request, body: SigninCodePost) -> dict[str, Any]:
    """Pass the code the provider's page shows to the windowless sign-in (Claude, D75).

    Claude Code can finish by itself once the browser approves, before anyone pastes
    the code the page also shows (machine B, 2026-09-28): a code that arrives after that
    is answered "already signed in", not refused. Refusing it marked a sign-in that had
    worked as failed, and the user signed in twice more.
    """
    provider = chosen_cli(ProviderPost(provider=body.provider))
    login = _LOGINS.get(provider)
    if login is None or not login.running():
        if cli_client(services_of(request), provider).status().signed_in is True:
            log.info("sign-in code for %s arrived after the sign-in finished", provider)
            return {"sent": False, "signed_in": True}
        raise HTTPException(409, "No sign-in is waiting for a code. Start it again.")
    if not body.code.strip():
        raise HTTPException(400, "The code is empty.")
    if not login.send(body.code):
        raise HTTPException(500, "Could not pass the code to the sign-in.")
    log.info("sign-in code passed to %s", provider)
    return {"sent": True}


@router.post("/llm/signin/cancel")
def llm_signin_cancel(body: ProviderPost | None = None) -> dict[str, Any]:
    """Stop a windowless sign-in: it holds a port and would otherwise wait forever."""
    provider = chosen_cli(body)
    login = _LOGINS.pop(provider, None)
    if login is not None:
        login.stop()
    return {"stopped": login is not None}


@router.post("/llm/install")
def llm_install(body: ProviderPost | None = None) -> dict[str, Any]:
    """Install Claude Code or Codex in a console the user can watch.

    Each module's ``install_plan`` chooses: the vendor's own installer where PowerShell
    can run it, and a native fallback where it cannot (winget for Claude Code; npm, then
    winget, for Codex). Neither is run blind — the exact command is rendered beside the button
    before it is pressed.
    """
    provider = chosen_cli(body)
    module = cli_module(provider)
    background = body is not None and body.background
    plan = module.install_plan(sign_in=not background)
    if plan is None:
        return {"launched": False, "command": "", "docs": module.INSTALL_DOCS_URL}
    result = launch_console(
        plan.argv,
        f"could not start the {plan.method} install",
        provider=provider,
        visible=not background,
    )
    result["command"] = plan.display  # the line the user was shown, not the wrapper
    result["docs"] = module.INSTALL_DOCS_URL
    result["log"] = console_log(provider, "install")
    return result


@router.post("/llm/update")
def llm_update(request: Request, body: ProviderPost | None = None) -> dict[str, Any]:
    """Update the CLI this application resolved — not whichever one PATH favours.

    Offered only where the app is already reporting a problem it cannot otherwise fix:
    a build too old to say whether it is signed in, or to take the options sent to it.
    Installs that update themselves never reach that state, so this is the resolution
    to a complaint rather than a standing feature.
    """
    provider = chosen_cli(body)
    product = CLI_PROVIDERS[provider]
    path = cli_client(services_of(request), provider).resolve()
    if path is None:
        raise HTTPException(409, f"{product} is not installed, so there is nothing to update.")
    command = cli_module(provider).update_command(path)
    return launch_console(
        ["powershell.exe", "-NoProfile", "-NoExit", "-Command", f"{command}"],
        "could not start the update",
        provider=provider,
    ) | {"command": command}


@router.post("/llm/test")
def llm_test(request: Request, body: ProviderPost | None = None) -> dict[str, Any]:
    """One tiny real call against the selected provider. Costs a few tokens."""
    from app.llm.client import make_client, system_blocks

    svc = services_of(request)
    provider = (body.provider if body else None) or str(svc.config.get("llm.provider"))
    # The provider is named to the factory, never written into the shared config for the
    # length of the probe. It used to be: set, call, then put back what it saw at the
    # start — so a Test pressed straight after choosing a provider (both requests in
    # flight at once) restored the *old* choice over the new one, and the next save
    # persisted it. Found by the Codex browser spec; see D59.
    try:
        client = make_client(svc.config, provider=provider)
        result = client.complete_json(
            system_blocks=system_blocks("You answer with JSON only.", None),
            user='Reply with exactly {"ok": true}',
            schema=PROBE_SCHEMA,
            max_tokens=64,
        )
        return {"provider": provider, "ok": bool(result.data.get("ok")), "model": result.model}
    except Exception as exc:
        return {"provider": provider, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}


# --------------------------------------------------------------------------- detector


@router.get("/detector/events")
def detector_events(request: Request, limit: int = 50) -> dict[str, Any]:
    svc = services_of(request)
    return {
        "events": [
            {
                "id": event.id,
                "at": event.at,
                "process": event.process,
                "window_title": event.window_title,
                "peak_score": event.peak_score,
                "evidence": event.evidence_list,
                "outcome": event.outcome,
                "meeting_id": event.meeting_id,
            }
            for event in svc.dao.detector_events(limit)
        ]
    }


@router.post("/detector/ignore")
def detector_ignore(request: Request, body: IgnorePost) -> dict[str, Any]:
    svc = services_of(request)
    ignore = list(svc.config.get("detection.ignore", []))
    if body.process not in ignore:
        ignore.append(body.process)
    svc.config.set("detection.ignore", ignore)
    svc.config.save()
    return {"ignore": ignore}


# --------------------------------------------------------------------------- events


@router.get("/events")
async def sse(request: Request) -> StreamingResponse:
    svc = services_of(request)
    svc.events.bind_loop(asyncio.get_running_loop())
    queue = svc.events.subscribe()

    async def stream() -> Any:
        # No `request.is_disconnected()` poll: it blocks on the receive channel, and a
        # closed connection already cancels this generator (the finally still runs).
        try:
            yield ": connected\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_S)
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                yield event.sse()
        finally:
            svc.events.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def _seed_audio(svc: Services, folder: Path, seconds: float) -> None:
    """Two real tracks, so a seeded meeting has a player rather than a placeholder."""
    import numpy as np

    from app.audio.writer import ChunkWriter

    rate = svc.config.sample_rate
    index = np.arange(int(seconds * rate))
    writer = ChunkWriter(folder, rate=rate)
    for track, hz in (("me", 220), ("them", 330)):
        writer.write_pcm(track, np.round(np.sin(2 * np.pi * hz * index / rate) * 8000))
    writer.close()


# --------------------------------------------------------------------------- test seed


def _seed_transcription(svc: Services, item: dict[str, Any]) -> str:
    """A file transcription in any state, as the worker would have left it (D86)."""
    from app.asr.backend import Segment, Word
    from app.transcription.store import RESULT_NAME
    from app.transcription.types import FileTranscript, Options

    store = svc.transcriptions
    options = Options.from_dict(item.get("options") or {})
    job = store.create(
        source_name=str(item.get("source_name", "clip.mp4")),
        source_kind=str(item.get("source_kind", "upload")),
        source_path=str(item.get("source_path", "")),
        options=options,
        client=str(item.get("client", "ui")),
        size_bytes=item.get("size_bytes", 1_000_000),
        duration_s=item.get("duration_s", 30.0),
    )
    state = str(item.get("state", "pending"))
    columns = {
        "state": state,
        "phase": item.get("phase"),
        "progress": float(item.get("progress", 1.0 if state == "done" else 0.0)),
        "last_error": item.get("last_error"),
        "language": item.get("language"),
        "language_conf": 0.95 if item.get("language") else None,
        "finished_at": iso(svc.clock.now()) if state in ("done", "failed", "cancelled") else None,
        "started_at": iso(svc.clock.now()) if state != "pending" else None,
        # Queued a moment before anything else this seed creates, so a meeting seeded in
        # the same request reliably waits behind it rather than tying with it.
        "queued_at": iso(svc.clock.now() - timedelta(seconds=float(item.get("queued_s_ago", 1)))),
    }
    sets = ", ".join(f"{key}=?" for key in columns)
    svc.conn.execute(
        f"UPDATE transcriptions SET {sets} WHERE id=?",
        (*columns.values(), job.id),
    )
    if state == "done":
        segments = tuple(
            Segment(
                id=index,
                track="them",
                speaker=str(seg.get("speaker", "S1")),
                start=float(seg.get("start", index * 4.0)),
                end=float(seg.get("end", index * 4.0 + 3.5)),
                text=str(seg["text"]),
                words=tuple(
                    Word(word, float(seg.get("start", index * 4.0)) + n * 0.4,
                         float(seg.get("start", index * 4.0)) + n * 0.4 + 0.35)
                    for n, word in enumerate(str(seg["text"]).split())
                ),
            )
            for index, seg in enumerate(item.get("segments") or [])
        )  # fmt: skip
        transcript = FileTranscript(
            duration_s=float(item.get("duration_s", 30.0)),
            segments=segments,
            language=item.get("language"),
            language_conf=0.95 if item.get("language") else None,
            language_source="classifier" if segments else "none",
            model={"name": "seed"},
            diarized=len({s.speaker for s in segments}) > 1,
        )
        folder = store.folder(job.id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / RESULT_NAME).write_text(
            json.dumps(transcript.as_result(id=job.id, source_name=job.source_name),
                       ensure_ascii=False),
            encoding="utf-8",
        )  # fmt: skip
    return str(job.id)


def seed_update_ready(svc: Services, version: str) -> None:
    """A verified update waiting to install, for the browser specs (D87).

    The installer it points at is two bytes, and the copy's installer is replaced by one
    that starts nothing: "Restart to update" in a spec must never run a program.
    """
    import json as _json

    from app.updates.manifest import DOWNLOAD_PREFIX, Manifest, Offer

    updates = svc.updates
    updates.home.mkdir(parents=True, exist_ok=True)
    name = f"Upshot-{version}-Setup.exe"
    (updates.home / name).write_bytes(b"MZ")
    record = {"version": version, "file": name, "sha256": "0" * 64, "mandatory": False}
    (updates.home / "ready.json").write_text(_json.dumps(record), encoding="utf-8")
    manifest = Manifest(
        channel="stable",
        version=version,
        url=f"{DOWNLOAD_PREFIX}v{version}/{name}",
        size=2,
        sha256="0" * 64,
        signer="0" * 40,
        rollout=100,
        notes={"en": "Faster summaries.", "he": "סיכומים מהירים יותר."},
    )
    updates.offer = Offer(manifest, mandatory=False)
    updates.phase = "ready"
    if svc.installer is not None:
        svc.installer.can_install = True
        svc.installer.spawn = lambda command: None
        svc.installer.quit = None


def test_router() -> APIRouter:
    """Mounted **only** when UP_TEST_MODE=1; its absence is itself asserted."""
    seed = APIRouter(prefix="/api/test")

    @seed.post("/run-jobs")
    def run_jobs(request: Request) -> dict[str, int]:
        """Run whatever the queue has that is runnable now, in this request.

        The e2e server builds the worker but does not start its thread: specs seed jobs
        in chosen states ("running", "failed") and a live worker would move them on under
        the assertion. A spec that wants the pipeline to actually run — re-summarizing
        with the Codex provider, say — asks for it here, and gets the jobs run
        deterministically rather than on a timer.
        """
        worker = services_of(request).worker
        return {"ran": worker.drain(limit=50) if worker is not None else 0}

    @seed.post("/seed")
    def seed_db(request: Request, body: dict[str, Any]) -> JSONResponse:
        svc = services_of(request)
        created: list[str] = []
        if body.get("reset"):
            # Test isolation: each spec starts from an empty library.
            for meeting in svc.dao.list_meetings(limit=10_000, include_hidden=True):
                svc.dao.clear_turns(meeting.id)
            svc.conn.execute("DELETE FROM jobs")
            if svc.progress is not None:
                svc.progress.clear()
            # Cascades from meetings too; said here so the reset does not depend on
            # PRAGMA foreign_keys being on for whichever connection runs it.
            svc.conn.execute("DELETE FROM meeting_tags")
            svc.conn.execute("DELETE FROM action_items")
            svc.conn.execute("DELETE FROM meetings")
            if capabilities(svc.conn).fts:
                svc.conn.execute("DELETE FROM search_fts")
            svc.conn.execute("DELETE FROM assistant_sessions")
            if svc.transcriptions is not None:
                for job in svc.transcriptions.recent(limit=500):
                    svc.transcriptions.purge(job.id)
            svc.extras.pop("storage_bytes", None)
            svc.conn.execute("DELETE FROM detector_events")
            if svc.prompts is not None:
                svc.prompts.reset()
            # And from the default appearance. These are saved settings, so a spec
            # that switches the interface to Hebrew, or to dark, used to leave the
            # next one running in it — the failure surfaced the moment the shell
            # started adopting what was saved instead of always booting English.
            # Read from DEFAULTS rather than restated here, so changing a default
            # cannot silently make the reset wrong.
            from app.config import DEFAULTS

            for key in (
                "language",
                "theme",
                "view",
                "calendar_span",
                "fold_nights",
                "tooltips_off",
            ):
                svc.config.set(f"ui.{key}", DEFAULTS["ui"][key])
            # Setup counts as done for every spec except the one about setup, which
            # asks for the opposite below: a spec that died on /welcome must not send
            # every spec after it there too.
            svc.config.set("setup.done", True)
            svc.config.set("setup.step", "")
            # The Terms likewise: accepted, unless a spec asks for them pending (D83).
            if svc.terms is not None:
                svc.config.set("legal.accepted_version", svc.terms.bundled.version)
            # Updates: nothing found, nothing ready, the default settings (D87).
            svc.config.set("updates.channel", "stable")
            svc.config.set("updates.auto_install", True)
            if svc.updates is not None:
                svc.updates.offer = None
                svc.updates.phase = "idle"
                svc.updates.last_error = None
                svc.updates.forget_ready()
            if svc.installer is not None:
                # As the e2e server starts: from source, nothing installing, no marker.
                svc.installer.outcome = None
                svc.installer.can_install = False
                svc.installer._installing = False
                (svc.installer.updates.home / "installing.json").unlink(missing_ok=True)
            # Crash reports: not asked yet, nothing sent, and no DSN, as from source.
            svc.config.set("diagnostics.crash_reports", "unset")
            if svc.reporter is not None:
                svc.reporter.dsn = ""
                svc.reporter.frontend_dsn = ""
                for name in ("last-report.json", "sent.json"):
                    (svc.reporter.home / name).unlink(missing_ok=True)
            if svc.feedback is not None:
                svc.feedback.dsn = ""
                (svc.feedback.home / "feedback-today.json").unlink(missing_ok=True)
            svc.extras.pop("feedback_screenshot", None)
            svc.config.save()
        if body.get("reports_available") and svc.reporter is not None:
            # As if the build carried a DSN, with a sender that goes nowhere: a spec must
            # never reach Sentry (D87).
            svc.reporter.dsn = (
                "https://0123456789abcdef0123456789abcdef@o1.ingest.example.invalid/1"
            )
            svc.reporter.frontend_dsn = (
                "https://0123456789abcdef0123456789abcdef@o1.ingest.example.invalid/2"
            )
            svc.reporter._send = lambda _kind, _event: True
            svc.reporter.background = False
        if body.get("feedback_available") and svc.feedback is not None:
            # As if the build carried a DSN; the sender goes nowhere (D87).
            svc.feedback.dsn = (
                "https://0123456789abcdef0123456789abcdef@o1.ingest.example.invalid/1"
            )
            svc.feedback._send = lambda _kind, _payload, _files: True
        if body.get("crash_report") and svc.reporter is not None:
            svc.config.set("diagnostics.crash_reports", "on")
            svc.reporter.report_exception(RuntimeError("seeded for a spec"), where="spec")
        if body.get("update_ready") and svc.updates is not None:
            seed_update_ready(svc, str(body["update_ready"]))
        if body.get("update_installed") and svc.installer is not None:
            # The start after an update: what the toast and Settings say (D87).
            svc.installer.outcome = {
                "result": "updated",
                "from": "0.0.1",
                "to": str(body["update_installed"]),
                "window_open": True,
            }
        if "terms_pending" in body and svc.terms is not None:
            pending = bool(body["terms_pending"])
            svc.config.set("legal.accepted_version", "" if pending else svc.terms.bundled.version)
            svc.config.save()
        if "setup_done" in body:
            svc.config.set("setup.done", bool(body["setup_done"]))
            # A fresh start of setup, not a resume of whatever the last spec left.
            svc.config.set("setup.step", "")
            svc.config.save()
        if body.get("reset"):
            svc.conn.execute("DELETE FROM calendar_events")
            svc.conn.execute("DELETE FROM meeting_calendar_accounts")
            svc.conn.execute("DELETE FROM calendar_sources")
            svc.conn.execute("DELETE FROM calendar_accounts")
        seeded_accounts = _seed_accounts(request, body)
        for item in body.get("calendar_events", []):
            # Seeded the way a sync would leave them, so the grids and the matcher are
            # exercised without a Google account.
            from datetime import datetime as _dt

            from app.gcal.events import Attendee, CalendarEvent, EventStore

            start = _dt.fromisoformat(str(item["start"]))
            end = _dt.fromisoformat(str(item["end"]))
            account_id = str(item.get("account_id") or seeded_accounts[0])
            # Added, not a window replaced: replacing marks every other event the window
            # overlaps as removed, so one seeded all-day event hid the timed ones under it.
            EventStore(svc.conn).add(
                [
                    CalendarEvent(
                        account_id=account_id,
                        calendar_id=str(item.get("calendar_id", "primary")),
                        event_id=str(item["id"]),
                        title=item.get("title"),
                        start=start,
                        end=end,
                        all_day=bool(item.get("all_day")),
                        ical_uid=str(item.get("ical_uid") or f"{item['id']}@seed"),
                        response=item.get("response", "accepted"),
                        attendees=tuple(
                            Attendee(name=str(name))
                            for name in item.get("attendees", ["Dana Levi"])
                        ),
                        attendee_count=len(item.get("attendees", ["Dana Levi"])),
                        conference_url=item.get("conference_url", "https://meet.google.com/seed"),
                    )
                ],
                synced_at=svc.clock.now(),
            )
        for event in body.get("detector_events", []):
            outcome = event.get("outcome", "shadow")
            svc.dao.add_detector_event(
                peak_score=int(event.get("peak_score", 7)),
                evidence=event.get("evidence", []),
                outcome=outcome,
                process=event.get("process"),
                window_title=event.get("window_title"),
            )
            # Announced as the detector announces it, so the pages that react to a
            # detection — the events table, and the nudge offering to record — can be
            # tested without a microphone and a real meeting.
            svc.events.publish(
                "detector",
                state=outcome,
                process=event.get("process"),
                score=int(event.get("peak_score", 7)),
            )
            if outcome == "shadow" and svc.prompts is not None:
                # The offer the detector makes on a verdict, which the banner shows (D76).
                from app.prompts import Prompt

                # Meetings booked at the same time that nothing told apart (D89): the
                # offer names none and lists them.
                candidates = tuple(
                    (
                        c.get("account_id") or (seeded_accounts[0] if seeded_accounts else None),
                        str(c.get("calendar_id", "primary")),
                        str(c["event_id"]),
                        str(c.get("title") or ""),
                    )
                    for c in event.get("candidates") or []
                )
                svc.prompts.offer(
                    Prompt(
                        kind="detected",
                        title=" / ".join(c[3] for c in candidates),
                        at=svc.clock.now(),
                        process=event.get("process"),
                        watch_process=False,
                        candidates=candidates,
                        score=int(event.get("peak_score", 7)),
                        evidence=tuple(
                            (str(item.get("code", "")), str(item.get("detail", "")))
                            for item in event.get("evidence", [])
                        ),
                    ),
                    recording=svc.recorder is not None and svc.recorder.committed,
                )
        for item in body.get("transcriptions", []):
            created.append(_seed_transcription(svc, item))
        for item in body.get("meetings", []):
            meeting = svc.dao.insert_meeting(
                meeting_id=item.get("id"),
                folder=svc.config.data_root / str(item.get("id", "seeded")),
                source=item.get("source", "manual"),
                state=item.get("state", MeetingState.RECORDING),
                started_at=item.get("started_at"),
                title=item.get("title"),
                profile="cpu-deferred",
                sensitive=bool(item.get("sensitive")),
            )
            row_fields = {
                key: item[key] for key in ("duration_s", "ended_at") if item.get(key) is not None
            }
            if item.get("speaker_names"):
                row_fields["speaker_names"] = json.dumps(item["speaker_names"], ensure_ascii=False)
            if row_fields:
                meeting = svc.dao.update_meeting(meeting.id, **row_fields)
            if item.get("tags"):
                svc.dao.set_tags(meeting.id, [str(tag) for tag in item["tags"]])
            # The transcript file below is written with a language, and the real ASR
            # stage puts the same value on the row. The seed did not, so the meeting
            # page — which reads the row to pick the transcript's direction — rendered
            # a Hebrew transcript left-to-right. "Looks exactly like a processed one"
            # has to include this or RTL cannot be tested from a seed at all.
            if item.get("turns"):
                svc.dao.update_meeting(
                    meeting.id,
                    language=item.get("language", "he"),
                    language_conf=0.95,
                )
            created.append(meeting.id)
            for stage, state in (item.get("jobs") or {}).items():
                job = svc.queue.enqueue(meeting.id, stage)
                if state != "pending":
                    svc.conn.execute("UPDATE jobs SET state = ? WHERE id = ?", (state, job.id))
            if item.get("progress") is not None and svc.progress is not None:
                # As far as a running transcribe stage would have reported (0–1 of it).
                svc.progress.start(meeting.id, "transcribe")
                svc.progress.report(
                    meeting.id, str(item.get("phase", "transcribe")), float(item["progress"])
                )
            if item.get("turns"):
                from app.asr.backend import Segment, TranscriptFile
                from app.asr.diarize import track_of
                from app.db.dao import Turn

                turns = list(item["turns"])
                svc.dao.index_turns(
                    meeting.id,
                    [
                        Turn(index, turn.get("speaker", "ME"), turn.get("at_ms", 0), turn["text"])
                        for index, turn in enumerate(turns)
                    ],
                )
                # The UI reads transcript.json, not the index — write it too, so a seeded
                # meeting looks exactly like a processed one.
                meeting.path.mkdir(parents=True, exist_ok=True)
                TranscriptFile(
                    language=item.get("language", "he"),
                    model={"name": "seed", "device": "none", "compute": "none"},
                    segments=[
                        Segment(
                            id=index,
                            track=track_of(turn.get("speaker", "ME")),
                            speaker=turn.get("speaker", "ME"),
                            start=turn.get("at_ms", 0) / 1000,
                            end=(
                                turn["end_ms"] / 1000
                                if turn.get("end_ms") is not None
                                else turn.get("at_ms", 0) / 1000 + 4.0
                            ),
                            text=turn["text"],
                        )
                        for index, turn in enumerate(turns)
                    ],
                ).write(meeting.path / "transcript.json")
                # And the assembled transcript.md, the same way the assemble stage writes
                # it — summarize reads that file, so without it a seeded meeting could be
                # shown but never re-summarized.
                from app.pipeline.stages.assemble import coalesce, render_markdown

                (meeting.path / "transcript.md").write_text(
                    render_markdown(
                        coalesce(TranscriptFile.read(meeting.path / "transcript.json").segments)
                    ),
                    encoding="utf-8",
                )
            folder = meeting.path
            folder.mkdir(parents=True, exist_ok=True)
            if item.get("evidence"):
                svc.dao.add_detector_event(
                    peak_score=int(item.get("peak_score", 7)),
                    evidence=item["evidence"],
                    outcome=item.get("outcome", "committed"),
                    process=item.get("process"),
                    meeting_id=meeting.id,
                )
            if item.get("proposed"):
                # The calendar meetings it may be, none settled (D89): it needs a meeting.
                from app.gcal.source import proposal

                found = [
                    _event_store(svc).get(
                        str(ref.get("account_id") or seeded_accounts[0]),
                        str(ref.get("calendar_id", "primary")),
                        str(ref["event_id"]),
                    )
                    for ref in item["proposed"]
                ]
                svc.dao.update_meeting(
                    meeting.id,
                    calendar_json=json.dumps(
                        proposal(
                            [e for e in found if e is not None],
                            source="detected",
                            reason="booked at the same time",
                        )
                    ),
                )
            if item.get("calendar"):
                # A meeting already matched to a seeded event, as the matcher leaves it —
                # which means the matcher's own snapshot when the event is in the cache,
                # so the meeting link and the participant names come from the event
                # rather than from a shorter hand-written copy of it.
                from app.gcal.source import snapshot as event_snapshot

                ref = dict(item["calendar"])
                ref.setdefault("account_id", seeded_accounts[0])
                seeded = _event_store(svc).get(
                    str(ref["account_id"]),
                    str(ref.get("calendar_id", "primary")),
                    str(ref.get("event_id")),
                )
                if seeded is not None:
                    payload = event_snapshot(
                        seeded,
                        state="matched",
                        source="auto",
                        confidence=1.0,
                        accounts=ref.get("accounts") or [],
                    )
                else:
                    payload = {
                        "accounts": ref.get("accounts") or [ref["account_id"]],
                        "event": {
                            k: v for k, v in ref.items() if k not in ("participants", "accounts")
                        },
                        "title": item.get("title"),
                        "participants": ref.get("participants", []),
                        "match": {"state": "matched", "source": "auto", "confidence": 1.0},
                    }
                svc.dao.update_meeting(
                    meeting.id,
                    calendar_json=json.dumps(payload),
                    title_source="calendar",
                )
                svc.dao.set_calendar_accounts(
                    meeting.id,
                    str((payload.get("event") or {}).get("account_id") or ref["account_id"]),
                    payload.get("accounts") or [],
                )
            if item.get("action_items"):
                # As a summary would have left them, so the inbox can be exercised
                # without running a model — and then the user's own state on top: ticks,
                # snoozes, and items typed in by hand.
                entries = list(item["action_items"])
                model = [e for e in entries if e.get("source", "model") != "user"]
                svc.dao.replace_action_items(
                    meeting.id,
                    [
                        {
                            "who": str(entry.get("who", "ME")),
                            "what": str(entry["what"]),
                            "due": entry.get("due"),
                            "at_ms": entry.get("at_ms"),
                            "detail": entry.get("detail"),
                            "due_at": entry.get("due_at"),
                        }
                        for entry in model
                    ],
                )
                # By seq, which is the order they were given in — not the inbox's order,
                # which puts mine first.
                stored = sorted(
                    svc.dao.action_items(meeting_id=meeting.id, include_hidden=True),
                    key=lambda a: a.seq,
                )
                pairs = list(zip(model, stored, strict=False))
                for entry in entries:
                    if entry.get("source") == "user":
                        added = svc.dao.add_action_item(
                            meeting.id,
                            what=str(entry["what"]),
                            who=str(entry.get("who", "ME")),
                            due_at=entry.get("due_at"),
                            detail=entry.get("detail"),
                        )
                        pairs.append((entry, added))
                for entry, row in pairs:
                    changes: dict[str, Any] = {}
                    if entry.get("done"):
                        changes["done"] = True
                    if entry.get("snoozed_until"):
                        changes["snoozed_until"] = entry["snoozed_until"]
                    if changes:
                        svc.dao.update_action_item(row.id, **changes)
            if item.get("audio_seconds"):
                _seed_audio(svc, folder, float(item["audio_seconds"]))
            if item.get("audio_deleted_at"):
                meta.update(folder, audio_deleted_at=item["audio_deleted_at"])
            if item.get("summary_html"):
                (folder / "summary.html").write_text(item["summary_html"], encoding="utf-8")
                # What the render stage would have indexed for search.
                from app.pipeline.stages.render import plaintext

                svc.dao.index_summary(
                    str(item["id"]), plaintext({"summary_html": item["summary_html"]}, "")
                )
            if item.get("notes") or item.get("chapters"):
                notes = dict(item.get("notes") or {})
                if item.get("chapters"):
                    notes["chapters"] = item["chapters"]
                (folder / "notes.json").write_text(
                    json.dumps(notes, ensure_ascii=False), encoding="utf-8"
                )
        return JSONResponse({"created": created})

    return seed
