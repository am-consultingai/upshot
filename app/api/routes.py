"""Every route in TECHNICAL-DESIGN.md §12. JSON, 127.0.0.1 only."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from app import meta
from app.clock import iso
from app.db.dao import GlossaryTerm, capabilities
from app.log import get
from app.pipeline.states import STAGE_ORDER, JobStage, MeetingState
from app.services import Services

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


class SettingsPut(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class GlossaryPut(BaseModel):
    terms: list[dict[str, Any]] = Field(default_factory=list)


class SecretsPut(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)


class ProviderPost(BaseModel):
    provider: str | None = None


class IgnorePost(BaseModel):
    process: str


class StartPost(BaseModel):
    title: str | None = None


# --------------------------------------------------------------------------- status


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/status")
def status(request: Request) -> dict[str, Any]:
    svc = services_of(request)
    recorder = svc.recorder
    detector = svc.detector
    disk = shutil.disk_usage(str(svc.config.data_root.parent))
    return {
        "profile": svc.config.profile,
        "policy": svc.worker.policy if svc.worker else svc.config.job_policy,
        "recorder": {
            "active": bool(recorder and recorder.is_active()),
            "armed": bool(recorder and recorder.armed),
            "paused": bool(recorder and recorder.paused),
            "meeting_id": recorder.meeting_id if recorder else None,
            "levels": recorder.levels() if recorder else {},
        },
        "detector": {
            "mode": svc.config.get("detection.mode"),
            "state": getattr(detector, "state", "idle") if detector else "off",
        },
        "queue": svc.queue.counts(),
        "queue_depth": svc.queue.depth(),
        "disk_free_bytes": disk.free,
        "fts": capabilities(svc.conn).fts,
        "now": iso(svc.clock.now()),
    }


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
    meter.release()  # every track: the recorder needs both endpoints
    meeting = svc.meetings.create(source="manual", title=(body.title if body else None))
    svc.recorder.start(meeting.path, meeting.id)
    svc.recorder.start_thread()
    svc.meetings.committed(meeting, meeting.path)
    svc.events.publish("recorder", state="recording", meeting_id=meeting.id)
    if svc.notifier is not None:
        svc.notifier.recording_started(meeting.id, meeting.title or "")
    return {"meeting_id": meeting.id, "folder": meeting.folder, "state": meeting.state}


@router.post("/recording/stop")
def recording_stop(request: Request) -> dict[str, Any]:
    svc = services_of(request)
    if svc.recorder is None or not svc.recorder.committed:
        raise HTTPException(409, "not recording")
    meeting_id = svc.recorder.meeting_id or ""
    result = svc.recorder.stop()
    duration_s = round(result.total_duration_ms / 1000)
    meeting = svc.meetings.finish(meeting_id, duration_s=duration_s)
    svc.events.publish("recorder", state="idle", meeting_id=meeting_id)
    if svc.notifier is not None and meeting.state == MeetingState.RECORDED:
        svc.notifier.recording_ended(meeting_id, duration_s // 60)
    return {
        "meeting_id": meeting_id,
        "state": meeting.state,
        "duration_s": duration_s,
        "chunks": len(result.records),
    }


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


def _meeting_payload(svc: Services, meeting_id: str) -> dict[str, Any]:
    meeting = svc.dao.get_meeting(meeting_id)
    if meeting is None:
        raise HTTPException(404, "no such meeting")
    payload = meeting.as_dict()
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
    payload["evidence"] = meeting.evidence
    mirrored = meta.read(meeting.path)
    # A meeting whose audio the retention policy removed is not a meeting that failed to
    # record, and the page must not say so.
    payload["audio_deleted_at"] = mirrored.get("audio_deleted_at")
    # Which tracks exist and whether either actually has anything on it. The player used
    # to be hardwired to "them", so a meeting where nobody else spoke played silence and
    # looked broken.
    from app.audio.writer import track_summary

    payload["audio_tracks"] = track_summary(meeting.path)
    return payload


@router.get("/meetings")
def list_meetings(
    request: Request,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    q: str | None = None,
    state: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    svc = services_of(request)
    meetings = svc.dao.list_meetings(frm=from_, to=to, q=q, state=state, limit=limit)
    return {"meetings": [meeting.as_dict() for meeting in meetings], "count": len(meetings)}


@router.get("/meetings/{meeting_id}")
def get_meeting(request: Request, meeting_id: str) -> dict[str, Any]:
    return _meeting_payload(services_of(request), meeting_id)


@router.patch("/meetings/{meeting_id}")
def patch_meeting(request: Request, meeting_id: str, body: MeetingPatch) -> dict[str, Any]:
    svc = services_of(request)
    meeting = svc.dao.get_meeting(meeting_id)
    if meeting is None:
        raise HTTPException(404, "no such meeting")
    if body.title is not None:
        svc.dao.update_meeting(meeting_id, title=body.title, title_source="user")
    if body.sensitive is not None:
        svc.dao.update_meeting(meeting_id, sensitive=int(body.sensitive))
    if body.discard:
        svc.meetings.discard(meeting_id)
    svc.events.publish("meeting", meeting_id=meeting_id, action="patched")
    return _meeting_payload(svc, meeting_id)


@router.get("/meetings/{meeting_id}/transcript")
def get_transcript(request: Request, meeting_id: str) -> Response:
    svc = services_of(request)
    meeting = svc.dao.require_meeting(meeting_id)
    path = meeting.path / "transcript.json"
    if not path.exists():
        raise HTTPException(404, "no transcript yet")
    return Response(path.read_text(encoding="utf-8"), media_type="application/json")


@router.get("/meetings/{meeting_id}/notes")
def get_notes(request: Request, meeting_id: str) -> Response:
    svc = services_of(request)
    meeting = svc.dao.require_meeting(meeting_id)
    path = meeting.path / "notes.json"
    if not path.exists():
        raise HTTPException(404, "no notes yet")
    return Response(path.read_text(encoding="utf-8"), media_type="application/json")


@router.get("/meetings/{meeting_id}/summary.html")
def get_summary(request: Request, meeting_id: str) -> Response:
    svc = services_of(request)
    meeting = svc.dao.require_meeting(meeting_id)
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
    meeting = svc.dao.require_meeting(meeting_id)

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
    """Remove a meeting: its folder on disk and its rows. There is no undo."""
    svc = services_of(request)
    meeting = svc.dao.require_meeting(meeting_id)
    recorder = svc.recorder
    if recorder is not None and recorder.committed and recorder.meeting_id == meeting_id:
        raise HTTPException(409, "this meeting is still recording")

    try:
        # Never delete outside the data root, whatever the database says the folder is.
        folder = svc.meetings.purge(meeting)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        # Windows will not unlink a file something has open. Saying so beats deleting the
        # row against a folder that survived, which orphans it with nothing pointing at it.
        raise HTTPException(409, str(exc)) from exc
    svc.events.publish("meeting", meeting_id=meeting_id, action="deleted")
    return {"deleted": meeting_id, "folder": str(folder)}


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
    ).as_dict()


@router.post("/retention/sweep")
def retention_sweep(request: Request) -> dict[str, Any]:
    """Apply the policy now rather than waiting for the worker's next pass."""
    from app.retention import sweep_services

    return sweep_services(services_of(request)).as_dict()


@router.post("/meetings/{meeting_id}/jobs/{stage}/retry")
def retry_stage(
    request: Request, meeting_id: str, stage: str, force: bool = False
) -> dict[str, Any]:
    """Re-run a stage. With ``force``, redo the work rather than reuse what is on disk."""
    svc = services_of(request)
    if stage not in {str(item) for item in STAGE_ORDER}:
        raise HTTPException(404, f"no such stage {stage!r}")
    svc.dao.require_meeting(meeting_id)
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
    meeting = svc.dao.require_meeting(meeting_id)
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

        holding = False
        deadline = time.monotonic() + LEVEL_MAX_S
        try:
            while time.monotonic() < deadline:
                recording = recorder is not None and recorder.is_active()
                if recording:
                    # The recorder owns the endpoint now. Drop the preview stream rather
                    # than hold a second one open, and report the level being recorded.
                    if holding:
                        await asyncio.to_thread(meter.release, track)
                        holding = False
                    assert recorder is not None
                    level = float(recorder.levels().get(track, 0.0))
                    yield _level_event(level, level, source="recorder")
                else:
                    if not holding:
                        try:
                            await asyncio.to_thread(
                                meter.acquire, svc.config, device if track == "me" else None, track
                            )
                        except Exception as exc:
                            yield _sse({"error": str(exc)})
                            return
                        holding = True
                    current = meter.active(track)
                    reading = current.read() if current is not None else None
                    if reading is None:
                        holding = False
                        continue
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
            if holding:
                # Synchronously, NOT via `await asyncio.to_thread`: a browser closing the
                # tab cancels this task, and awaiting anything in a cancelled task raises
                # CancelledError at the await — so the release never ran and the
                # microphone stayed open until the backstop fired. The monitor thread
                # polls its stop flag every 50 ms, so this returns promptly.
                meter.release(track)

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
    """Only ever the redacted dump — no secret ever reaches the DOM."""
    svc = services_of(request)
    return {"config": svc.config.redacted_dump(), "warnings": svc.config.warnings()}


def refuse_unusable_provider(svc: Services, values: dict[str, Any]) -> None:
    """The subscription provider without its CLI fails hours later, not now.

    Nothing rejects the selection today, so the failure surfaces in the summarize stage
    — permanently, at the end of a meeting already recorded and transcribed. Refusing
    the selection costs a click; refusing it later costs the summary.
    """
    if values.get("llm.provider") != "claude-subscription":
        return
    from app.llm.claude_cli import ClaudeCliClient

    if not ClaudeCliClient(svc.config).status().installed:
        raise HTTPException(
            409,
            "Claude Code is not installed on this machine. Install it first — the app "
            "never handles your credentials.",
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
    return get_settings(request)


# --------------------------------------------------------------------------- llm


PROBE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ok"],
    "properties": {"ok": {"type": "boolean"}},
}


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


@router.get("/llm/status")
def llm_status(request: Request) -> dict[str, Any]:
    """What each summarization provider needs, and whether it has it."""
    from app.llm.claude_cli import ClaudeCliClient

    svc = services_of(request)

    def has(name: str, env: str) -> bool:
        return bool(svc.config.secret(name, env=env))

    from app.llm.claude_cli import INSTALL_DOCS_URL, install_plan, update_command

    cli = ClaudeCliClient(svc.config).status()
    # Only probed when there is something to install: winget's own start-up is slow
    # enough to be felt on every settings load otherwise.
    plan = install_plan() if not cli.installed else None
    providers = [
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
        {
            "id": "claude-subscription",
            "label": "Claude Code (your own subscription)",
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
            "install_docs": INSTALL_DOCS_URL,
            "update_hint": update_command(cli.path) if cli.installed else "",
        },
        {
            "id": "ollama",
            "label": "Local (Ollama)",
            "needs": "ollama",
            "ready": True,
            "detail": str(svc.config.get("llm.ollama_url")),
        },
    ]
    return {"active": str(svc.config.get("llm.provider")), "providers": providers}


def launch_console(command: list[str], failure: str) -> dict[str, Any]:
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

    from app.llm.claude_cli import child_env, creation_flags, workdir

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
            subprocess.Popen(
                argv,
                cwd=where,
                env=child_env(),
                creationflags=creation_flags(visible=True),
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


@router.post("/llm/signin")
def llm_signin(request: Request) -> dict[str, Any]:
    """Launch Anthropic's own login. We never see the credential it creates."""
    from app.llm.claude_cli import ClaudeCliClient, login_console

    svc = services_of(request)
    client = ClaudeCliClient(svc.config)
    if client.resolve() is None:
        raise HTTPException(
            409,
            "Claude Code is not installed. Install it, then sign in — the app never "
            "handles your credentials.",
        )
    return launch_console(login_console(client.login_command()), "could not launch Claude Code")


@router.post("/llm/install")
def llm_install(request: Request) -> dict[str, Any]:
    """Install Claude Code in a console the user can watch.

    Two tiers, chosen in ``install_plan``: winget where it genuinely runs, and Anthropic's
    own installer where it does not. Neither is run blind — the exact command is rendered
    beside the button before it is pressed.
    """
    from app.llm.claude_cli import INSTALL_DOCS_URL, install_plan

    plan = install_plan()
    if plan is None:
        return {"launched": False, "command": "", "docs": INSTALL_DOCS_URL}
    result = launch_console(plan.argv, f"could not start the {plan.method} install")
    result["command"] = plan.display  # the line the user was shown, not the wrapper
    result["docs"] = INSTALL_DOCS_URL
    return result


@router.post("/llm/update")
def llm_update(request: Request) -> dict[str, Any]:
    """Update the Claude Code this application resolved — not whichever one PATH favours.

    Offered only where the app is already reporting a problem it cannot otherwise fix:
    a build too old to say whether it is signed in. Installs that update themselves never
    reach that state, so this is the resolution to a complaint rather than a standing
    feature.
    """
    from app.llm.claude_cli import ClaudeCliClient, update_command

    svc = services_of(request)
    path = ClaudeCliClient(svc.config).resolve()
    if path is None:
        raise HTTPException(409, "Claude Code is not installed, so there is nothing to update.")
    command = update_command(path)
    return launch_console(
        ["powershell.exe", "-NoProfile", "-NoExit", "-Command", f"{command}"],
        "could not start the update",
    ) | {"command": command}


@router.post("/llm/test")
def llm_test(request: Request, body: ProviderPost | None = None) -> dict[str, Any]:
    """One tiny real call against the selected provider. Costs a few tokens."""
    from app.llm.client import make_client, system_blocks

    svc = services_of(request)
    provider = (body.provider if body else None) or str(svc.config.get("llm.provider"))
    config = svc.config
    previous = config.get("llm.provider")
    config.set("llm.provider", provider)
    try:
        client = make_client(config)
        result = client.complete_json(
            system_blocks=system_blocks("You answer with JSON only.", None),
            user='Reply with exactly {"ok": true}',
            schema=PROBE_SCHEMA,
            max_tokens=64,
        )
        return {"provider": provider, "ok": bool(result.data.get("ok")), "model": result.model}
    except Exception as exc:
        return {"provider": provider, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
    finally:
        config.set("llm.provider", previous)


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


def test_router() -> APIRouter:
    """Mounted **only** when MA_TEST_MODE=1; its absence is itself asserted."""
    seed = APIRouter(prefix="/api/test")

    @seed.post("/seed")
    def seed_db(request: Request, body: dict[str, Any]) -> JSONResponse:
        svc = services_of(request)
        created: list[str] = []
        if body.get("reset"):
            # Test isolation: each spec starts from an empty library.
            for meeting in svc.dao.list_meetings(limit=10_000):
                svc.dao.clear_turns(meeting.id)
            svc.conn.execute("DELETE FROM jobs")
            svc.conn.execute("DELETE FROM meetings")
            svc.conn.execute("DELETE FROM detector_events")
            # And from the default appearance. These are saved settings, so a spec
            # that switches the interface to Hebrew, or to dark, used to leave the
            # next one running in it — the failure surfaced the moment the shell
            # started adopting what was saved instead of always booting English.
            # Read from DEFAULTS rather than restated here, so changing a default
            # cannot silently make the reset wrong.
            from app.config import DEFAULTS

            for key in ("language", "theme", "view", "calendar_span"):
                svc.config.set(f"ui.{key}", DEFAULTS["ui"][key])
            svc.config.save()
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
        for item in body.get("meetings", []):
            meeting = svc.dao.insert_meeting(
                meeting_id=item.get("id"),
                folder=svc.config.data_root / str(item.get("id", "seeded")),
                source=item.get("source", "manual"),
                state=item.get("state", MeetingState.RECORDING),
                started_at=item.get("started_at"),
                title=item.get("title"),
                profile="cpu-deferred",
            )
            created.append(meeting.id)
            for stage, state in (item.get("jobs") or {}).items():
                job = svc.queue.enqueue(meeting.id, stage)
                if state != "pending":
                    svc.conn.execute("UPDATE jobs SET state = ? WHERE id = ?", (state, job.id))
            if item.get("turns"):
                from app.asr.backend import Segment, TranscriptFile
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
                            track="me" if turn.get("speaker", "ME") == "ME" else "them",
                            speaker=turn.get("speaker", "ME"),
                            start=turn.get("at_ms", 0) / 1000,
                            end=turn.get("at_ms", 0) / 1000 + 4.0,
                            text=turn["text"],
                        )
                        for index, turn in enumerate(turns)
                    ],
                ).write(meeting.path / "transcript.json")
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
            if item.get("audio_seconds"):
                _seed_audio(svc, folder, float(item["audio_seconds"]))
            if item.get("audio_deleted_at"):
                meta.update(folder, audio_deleted_at=item["audio_deleted_at"])
            if item.get("summary_html"):
                (folder / "summary.html").write_text(item["summary_html"], encoding="utf-8")
            if item.get("notes"):
                (folder / "notes.json").write_text(
                    json.dumps(item["notes"], ensure_ascii=False), encoding="utf-8"
                )
        return JSONResponse({"created": created})

    return seed
