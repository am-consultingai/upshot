"""Delivery (§11). Default mode is **draft**: render, store, notify — no send."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app import meta
from app.log import get
from app.mail import Mailer, Outgoing
from app.pipeline.context import StageContext
from app.pipeline.stages.assemble import transcript_paths
from app.pipeline.stages.render import output_paths, plaintext
from app.pipeline.stages.summarize import load_notes

log = get(__name__)

DELIVERY_NAME = "delivery.json"


def delivery_path(folder: Path) -> Path:
    return Path(folder) / DELIVERY_NAME


def mailer_for(ctx: StageContext) -> Mailer:
    services = ctx.services
    mailer = getattr(services, "mailer", None) if services is not None else None
    if mailer is not None:
        return mailer  # type: ignore[no-any-return]
    return Mailer(ctx.config)


def notifier_for(ctx: StageContext) -> Any:
    services = ctx.services
    return getattr(services, "notifier", None) if services is not None else None


def subject_for(notes: dict[str, Any], meeting_title: str | None) -> str:
    email = notes.get("follow_up_email") or {}
    subject = str(email.get("subject") or "").strip()
    return subject or f"Meeting notes: {notes.get('title') or meeting_title or 'untitled'}"


def run(ctx: StageContext) -> None:
    folder = ctx.folder
    _ui_path, email_path = output_paths(folder)
    if not email_path.exists():
        raise FileNotFoundError(f"{email_path} is missing — run the render stage first")
    notes = load_notes(folder)
    language = ctx.meeting.summary_language or ctx.config.summary_language
    mode = str(ctx.config.get("delivery.mode", "draft"))
    mailer = mailer_for(ctx)
    recipients = mailer.recipients()
    record: dict[str, Any] = {
        "mode": mode,
        "subject": subject_for(notes, ctx.meeting.title),
        "recipients": list(recipients),
        "html": str(email_path),
        "sent": False,
    }

    if mode == "draft":
        # A draft is not a delivery: the job completes, the meeting stays RENDERED.
        ctx.hold_state = True
        # Render, store, notify. The user sends from the UI with one click — which also
        # handles the no-recipients case without a special path.
        record["deliverable"] = bool(recipients)
        delivery_path(folder).write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        meta.mirror(ctx.refresh(), delivery=record)
        _notify(ctx, notes)
        log.info("draft prepared for %s (not sent)", ctx.meeting.id)
        return

    attachments: tuple[Path, ...] = ()
    if bool(ctx.config.get("delivery.attach_transcript", False)):
        _, transcript_md = transcript_paths(folder)
        if transcript_md.exists():
            attachments = (transcript_md,)
    outgoing = Outgoing(
        subject=record["subject"],
        to=recipients,
        text=plaintext(notes, language),
        html=email_path.read_text(encoding="utf-8"),
        from_addr=mailer.from_addr,
        attachments=attachments,
    )
    mailer.send(outgoing, meeting_id=ctx.meeting.id)
    record["sent"] = True
    record["attachments"] = [path.name for path in attachments]
    delivery_path(folder).write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    meta.mirror(ctx.refresh(), delivery=record)
    _notify(ctx, notes)
    ctx.metrics.update({"delivered": True, "recipients": len(recipients)})


def _notify(ctx: StageContext, notes: dict[str, Any]) -> None:
    notifier = notifier_for(ctx)
    if notifier is None:
        return
    try:
        notifier.summary_ready(ctx.meeting.id, str(notes.get("title") or ""))
    except Exception as exc:
        log.warning("notification failed: %s", exc)
