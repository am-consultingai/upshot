"""Feedback from inside the app, anonymous unless the user adds an email (D87, D1-D3).

Feedback is something the user chose to send, so it does not wait for the crash-report
answer; it needs only a build that can send (a DSN). It goes to Sentry as *user
feedback* (``contexts.feedback``), in the desktop project, and what is sent is exactly
what ``preview`` shows: ``build`` makes both.

- **Anonymous.** No install id and nothing about the user, unless they type an email
  for a reply. Their own words are sent as they wrote them, after they have seen them.
- **Technical details**, when ticked (the default): version, commit, the kind of machine,
  and the last crashes this install reported, by type and place only. Nothing from a
  meeting.
- **A screenshot of Upshot's window**, when the user attached one (D2): the one captured
  and shown to them, nothing else.
- **A summary rating** (D3): up or down, an optional comment, and which provider and
  prompt wrote the summary. The summary itself only if the user ticked "include it".

Each one gets a reference (``FB-3F9A2C``) the user can quote in an email. At most
``DAILY_MAX`` a day leave one install.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Config
from app.diagnostics import scrub
from app.diagnostics.client import Attachment
from app.log import get

log = get(__name__)

KINDS = ("idea", "problem", "praise", "other", "summary_rating")
DAILY_MAX = 20
MAX_MESSAGE = 5000
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[A-Za-z]{2,}$")


class FeedbackError(ValueError):
    """The feedback cannot be sent as given (a bad email, too much of it, no DSN)."""


@dataclass
class Feedback:
    kind: str
    message: str = ""
    email: str = ""
    details: bool = True
    screenshot: bytes | None = None
    #: Only for a summary rating.
    rating: str | None = None
    meeting: dict[str, Any] = field(default_factory=dict)
    summary_html: str | None = None


class FeedbackSender:
    def __init__(
        self,
        config: Config,
        *,
        dsn: str | None,
        version: str,
        commit: str | None,
        home: Path,
        send: Callable[[str, dict[str, Any], list[Attachment]], bool] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self.dsn = dsn
        self.version = version
        self.commit = commit
        self.home = home / "diagnostics"
        self._send = send
        self.now = now or (lambda: datetime.now(UTC))

    @property
    def available(self) -> bool:
        return bool(self.dsn)

    # ------------------------------------------------------------------ building

    def build(self, feedback: Feedback) -> tuple[dict[str, Any], list[Attachment]]:
        """The exact payload and files that ``send`` would send."""
        if feedback.kind not in KINDS:
            raise FeedbackError(f"unknown kind {feedback.kind!r}")
        message = feedback.message.strip()
        if len(message) > MAX_MESSAGE:
            raise FeedbackError(f"feedback is limited to {MAX_MESSAGE} characters")
        email = feedback.email.strip()
        if email and not _EMAIL.match(email):
            raise FeedbackError("that does not look like an email address")
        if feedback.kind == "summary_rating" and feedback.rating not in ("up", "down"):
            raise FeedbackError("a summary rating is up or down")
        if feedback.kind != "summary_rating" and not message:
            raise FeedbackError("write something first")
        event_id = uuid.uuid4().hex
        reference = f"FB-{event_id[:6].upper()}"
        context: dict[str, Any] = {"message": message or f"Summary rated {feedback.rating}"}
        if email:
            context["contact_email"] = email
        tags: dict[str, str] = {
            "kind": feedback.kind,
            "reference": reference,
            "ui_language": str(self.config.get("ui.language") or "en"),
        }
        if feedback.details:
            tags.update(scrub.platform_tags())
            tags["recent_crashes"] = ", ".join(self._recent_crashes()) or "none"
        if feedback.kind == "summary_rating":
            tags["rating"] = str(feedback.rating)
            for key in ("provider", "prompt_version", "summary_language", "transcript_size"):
                if feedback.meeting.get(key):
                    tags[key] = scrub.scrub_text(str(feedback.meeting[key]), homes=[])[:60]
        payload = {
            "event_id": event_id,
            "timestamp": self.now().isoformat(),
            "platform": "other",
            "level": "info",
            "release": f"upshot@{self.version}",
            "dist": self.commit or "source",
            "tags": tags,
            "contexts": {"feedback": context},
        }
        files: list[Attachment] = []
        if feedback.screenshot:
            files.append(Attachment("upshot-window.png", "image/png", feedback.screenshot))
        if feedback.kind == "summary_rating" and feedback.summary_html:
            files.append(
                Attachment("summary.html", "text/html", feedback.summary_html.encode("utf-8"))
            )
        return payload, files

    def preview(self, feedback: Feedback) -> dict[str, Any]:
        payload, files = self.build(feedback)
        return {
            "payload": payload,
            "attachments": [{"filename": f.filename, "bytes": len(f.data)} for f in files],
        }

    # ------------------------------------------------------------------ sending

    def send(self, feedback: Feedback) -> dict[str, Any]:
        """Send it, or keep it for when the network is back. The reference either way."""
        if not self.available:
            raise FeedbackError("this copy cannot send feedback (it runs from source)")
        if not self._within_daily_limit():
            raise FeedbackError("that is enough feedback for today; thank you")
        payload, files = self.build(feedback)
        sent = False
        try:
            if self._send is not None:
                sent = self._send("feedback", payload, files)
            else:
                from app.diagnostics.client import SentryClient

                assert self.dsn
                sent = SentryClient(self.dsn, self.home / "outbox").send("feedback", payload, files)
        except Exception:  # never lose the user's words to an error here
            log.exception("could not send feedback")
        log.info("feedback %s %s", payload["tags"]["reference"], "sent" if sent else "kept")
        return {"reference": payload["tags"]["reference"], "sent": sent}

    def _within_daily_limit(self) -> bool:
        path = self.home / "feedback-today.json"
        today = self.now().date().isoformat()
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            record = {}
        count = int(record.get("count", 0)) if record.get("day") == today else 0
        if count >= DAILY_MAX:
            return False
        self.home.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"day": today, "count": count + 1}), encoding="utf-8")
        return True

    def _recent_crashes(self) -> list[str]:
        """The crashes this install reported today, by type and place only."""
        try:
            sent = json.loads((self.home / "sent.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [str(key)[:80] for key in list(sent)[-3:]] if isinstance(sent, dict) else []


def transcript_size(chars: int) -> str:
    """How long a transcript was, coarsely: never its length to the character."""
    for limit, label in ((5_000, "<5k"), (20_000, "5k-20k"), (60_000, "20k-60k")):
        if chars < limit:
            return label
    return "60k+"
