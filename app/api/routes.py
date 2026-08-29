"""Every route in TECHNICAL-DESIGN.md §12. JSON, 127.0.0.1 only."""

from __future__ import annotations

import asyncio
import json
import shutil
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
        }
        for job in svc.queue.for_meeting(meeting_id)
    ]
    payload["evidence"] = meeting.evidence
    payload["review_reasons"] = meta.review_reasons(meeting.path)
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


@router.get("/meetings/{meeting_id}/audio")
def get_audio(
    request: Request, meeting_id: str, track: str = "them", seq: int | None = None
) -> Response:
    """Range-request only, gated by the same cookie."""
    svc = services_of(request)
    meeting = svc.dao.require_meeting(meeting_id)
    directory = meeting.path / "audio" / track
    if not directory.exists():
        raise HTTPException(404, "no audio for this track")
    files = sorted(directory.glob("*.wav"))
    if not files:
        raise HTTPException(404, "no audio for this track")
    path = files[min(max(seq or 1, 1), len(files)) - 1]
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


@router.post("/meetings/{meeting_id}/jobs/{stage}/retry")
def retry_stage(request: Request, meeting_id: str, stage: str) -> dict[str, Any]:
    svc = services_of(request)
    if stage not in {str(item) for item in STAGE_ORDER}:
        raise HTTPException(404, f"no such stage {stage!r}")
    svc.dao.require_meeting(meeting_id)
    job = svc.queue.retry(meeting_id, JobStage(stage))
    svc.events.publish("job", meeting_id=meeting_id, stage=stage, state=job.state)
    return {"stage": job.stage, "state": job.state, "attempts": job.attempts}


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


# --------------------------------------------------------------------------- settings


@router.get("/settings")
def get_settings(request: Request) -> dict[str, Any]:
    """Only ever the redacted dump — no secret ever reaches the DOM."""
    svc = services_of(request)
    return {"config": svc.config.redacted_dump(), "warnings": svc.config.warnings()}


@router.put("/settings")
def put_settings(request: Request, body: SettingsPut) -> dict[str, Any]:
    svc = services_of(request)
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

    cli = ClaudeCliClient(svc.config).status()
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
            "detail": cli.version or cli.detail,
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


@router.post("/llm/signin")
def llm_signin(request: Request) -> dict[str, Any]:
    """Launch Anthropic's own login. We never see the credential it creates."""
    import subprocess
    import sys

    from app.llm.claude_cli import ClaudeCliClient

    svc = services_of(request)
    client = ClaudeCliClient(svc.config)
    path = client.resolve()
    if path is None:
        raise HTTPException(
            409,
            "Claude Code is not installed. Install it, then sign in — the app never "
            "handles your credentials.",
        )
    command = [path]
    try:
        if sys.platform == "win32":
            subprocess.Popen(command, creationflags=0x00000010)  # CREATE_NEW_CONSOLE
            launched = True
        else:
            launched = False  # no assumption about which terminal emulator exists
    except Exception as exc:
        raise HTTPException(500, f"could not launch Claude Code: {exc}") from exc
    return {"launched": launched, "command": " ".join(command)}


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
        for event in body.get("detector_events", []):
            svc.dao.add_detector_event(
                peak_score=int(event.get("peak_score", 7)),
                evidence=event.get("evidence", []),
                outcome=event.get("outcome", "shadow"),
                process=event.get("process"),
                window_title=event.get("window_title"),
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
            if item.get("summary_html"):
                (folder / "summary.html").write_text(item["summary_html"], encoding="utf-8")
            if item.get("notes"):
                (folder / "notes.json").write_text(
                    json.dumps(item["notes"], ensure_ascii=False), encoding="utf-8"
                )
        return JSONResponse({"created": created})

    return seed
