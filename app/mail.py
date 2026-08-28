"""SMTP delivery, stdlib only (§11).

Plain SMTP with an app password — no Gmail API, no OAuth, no consent screen. That
decoupling is what lets calendar drop out of V1 entirely.
"""

from __future__ import annotations

import smtplib
from collections.abc import Sequence
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

from app.config import Config
from app.errors import PermanentError, RecoverableError
from app.log import get

log = get(__name__)


@dataclass(frozen=True)
class Outgoing:
    subject: str
    to: tuple[str, ...]
    text: str
    html: str
    from_addr: str
    attachments: tuple[Path, ...] = ()

    def as_message(self) -> EmailMessage:
        message = EmailMessage()
        message["Subject"] = self.subject
        message["From"] = self.from_addr
        message["To"] = ", ".join(self.to)
        message.set_content(self.text)
        message.add_alternative(self.html, subtype="html")
        for path in self.attachments:
            message.add_attachment(
                path.read_bytes(),
                maintype="text",
                subtype="markdown",
                filename=path.name,
            )
        return message


class Mailer:
    """Sends, and records what left the machine (SECURITY-AND-AUTH.md §9)."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.egress: list[dict[str, object]] = []

    @property
    def host(self) -> str:
        return str(self.config.get("delivery.smtp.host", ""))

    @property
    def port(self) -> int:
        return int(self.config.get("delivery.smtp.port", 587))

    @property
    def from_addr(self) -> str:
        return str(
            self.config.get("delivery.smtp.from_addr")
            or self.config.get("delivery.smtp.user")
            or ""
        )

    def recipients(self, override: Sequence[str] | None = None) -> tuple[str, ...]:
        if override:
            return tuple(override)
        configured = self.config.get("delivery.recipients") or []
        if configured:
            return tuple(str(item) for item in configured)
        return (self.from_addr,) if self.from_addr else ()

    def send(self, outgoing: Outgoing, *, meeting_id: str | None = None) -> None:
        if not self.host:
            raise PermanentError("no SMTP host configured", category="config")
        if not outgoing.to:
            raise PermanentError("no recipients for this meeting", category="config")
        message = outgoing.as_message()
        payload = message.as_bytes()
        try:
            with smtplib.SMTP(self.host, self.port, timeout=30) as server:
                if bool(self.config.get("delivery.smtp.starttls", True)):
                    server.starttls()
                user = str(self.config.get("delivery.smtp.user", ""))
                password = self.config.secret("smtp", env="MA_SMTP_PASSWORD")
                if user and password:
                    server.login(user, password)
                server.send_message(message)
        except smtplib.SMTPAuthenticationError as exc:
            raise PermanentError(f"SMTP rejected the credentials: {exc}", category="auth") from exc
        except (smtplib.SMTPException, OSError) as exc:
            raise RecoverableError(f"SMTP delivery failed: {exc}") from exc
        self.egress.append(
            {
                "meeting_id": meeting_id,
                "destination": f"smtp://{self.host}:{self.port}",
                "bytes": len(payload),
                "recipients": len(outgoing.to),
            }
        )
        log.info(
            "sent %d bytes to %d recipient(s) via %s:%d (meeting %s)",
            len(payload),
            len(outgoing.to),
            self.host,
            self.port,
            meeting_id,
        )
