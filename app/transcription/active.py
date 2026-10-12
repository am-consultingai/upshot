"""What the one worker is doing and what waits for it, meetings and files together.

Settings → Transcription opens with this list, the meeting card and the meeting page
read their percentage from it, and the tray its tooltip. Both queues, in the order the
scheduler will take them (``Scheduler.ordered``, R5), running and waiting only: a
finished job is the Transcriptions page's or the meeting's business, not the queue's.

A meeting's progress comes from ``services.progress`` (its transcribe stage, in memory);
a file's from its row. Either has ``eta_s`` once its run can be measured. A meeting on
a hidden calendar account is left out (D82), but still counts for the positions behind
it: it runs all the same.
"""

from __future__ import annotations

from typing import Any

from app.clock import parse_iso
from app.pipeline.queue import TRANSCRIPT_STAGES, Job
from app.transcription.store import RUNNING, Transcription


def active_jobs(svc: Any) -> list[dict[str, Any]]:
    scheduler = svc.scheduler
    if scheduler is None:
        return []
    items: list[dict[str, Any]] = []
    for index, item in enumerate(scheduler.ordered()):
        entry = _meeting(svc, item) if isinstance(item, Job) else _file(svc, item)
        if entry is None:
            continue
        # As ``Scheduler.position`` counts it: 1 is next, with what runs now ahead of it.
        entry["position"] = index + 1 if item.state == "pending" else None
        items.append(entry)
    return items


def running_progress(svc: Any) -> float | None:
    """The running transcription's progress, 0–1, for the tray; ``None`` when nothing
    that reports one is running (a summary, or an idle worker)."""
    store = svc.transcriptions
    current = store.running() if store is not None else None
    if current is not None:
        return float(current.progress)
    tracker = getattr(svc, "progress", None)
    if tracker is None:
        return None
    for job in svc.queue.running_jobs():
        state = tracker.get(job.meeting_id)
        if state is not None:
            return float(state["progress"])
    return None


def stop_running(svc: Any) -> str | None:
    """Stop whatever transcription is running now, meeting or file: the tray's "Stop
    transcription". What was stopped (``meeting:<id>`` or ``file:<id>``), or ``None``."""
    from app.pipeline.worker import stop_transcription

    for job in svc.queue.running_jobs():
        if job.stage in TRANSCRIPT_STAGES and stop_transcription(svc, job.meeting_id):
            return f"meeting:{job.meeting_id}"
    store = svc.transcriptions
    current = store.running() if store is not None else None
    if current is not None:
        store.cancel(current.id)
        return f"file:{current.id}"
    return None


def _meeting(svc: Any, job: Job) -> dict[str, Any] | None:
    meeting = svc.dao.visible_meeting(job.meeting_id)
    if meeting is None:
        return None
    tracker = getattr(svc, "progress", None)
    progress = tracker.get(job.meeting_id) if tracker is not None else None
    if job.state != RUNNING or (progress or {}).get("stage") != job.stage:
        progress = None
    return {
        "kind": "meeting",
        "id": job.meeting_id,
        "title": meeting.title,
        "client": "meeting",
        "stage": job.stage,
        "state": job.state,
        "waiting_reason": _meeting_waiting(svc, job),
        "phase": progress["phase"] if progress else None,
        "progress": progress["progress"] if progress else None,
        "eta_s": progress["eta_s"] if progress else None,
        "queued_at": job.fifo_key,
        "started_at": job.started_at,
        # Its transcription can be stopped (the meeting stays); a summary cannot.
        "cancellable": job.stage in TRANSCRIPT_STAGES,
    }


def _meeting_waiting(svc: Any, job: Job) -> str | None:
    if job.state != "pending":
        return None
    if job.not_before is not None and parse_iso(job.not_before) > svc.clock.now():
        return "retry"
    worker = svc.worker
    return str(worker.waiting_reason()) if worker is not None else "queue"


def _file(svc: Any, job: Transcription) -> dict[str, Any]:
    from app.transcription.api import waiting_reason

    running = job.state == RUNNING
    return {
        "kind": "file",
        "id": job.id,
        "title": job.source_name,
        "client": job.client,
        "stage": None,
        "state": job.state,
        "waiting_reason": waiting_reason(svc, job),
        "phase": job.phase if running else None,
        "progress": job.progress if running else None,
        "eta_s": svc.transcriptions.eta_s(job),
        "queued_at": job.queued_at,
        "started_at": job.started_at,
        "cancellable": True,
    }
