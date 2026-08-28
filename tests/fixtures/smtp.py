"""FakeSmtp — a real ``aiosmtpd`` server on a loopback port.

The only SMTP target in the suite. Selected by pointing ``delivery.smtp.host``/``port``
at it, so the production send path is what runs.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from email import message_from_bytes
from email.message import Message
from typing import Any


@dataclass
class Received:
    peer: Any
    mail_from: str
    rcpt_tos: list[str]
    message: Message

    def part(self, subtype: str) -> str | None:
        for part in self.message.walk():
            if part.get_content_maintype() == "text" and part.get_content_subtype() == subtype:
                payload = part.get_payload(decode=True)
                return payload.decode(part.get_content_charset() or "utf-8")
        return None

    @property
    def subject(self) -> str:
        from email.header import decode_header, make_header

        return str(make_header(decode_header(self.message["Subject"] or "")))


@dataclass
class FakeSmtp:
    port: int = 0
    host: str = "127.0.0.1"
    received: list[Received] = field(default_factory=list)
    refuse: bool = False
    _controller: Any = None

    class _Handler:
        def __init__(self, owner: FakeSmtp) -> None:
            self.owner = owner

        async def handle_DATA(self, server: Any, session: Any, envelope: Any) -> str:
            if self.owner.refuse:
                return "451 Requested action aborted: local error in processing"
            self.owner.received.append(
                Received(
                    peer=session.peer,
                    mail_from=envelope.mail_from,
                    rcpt_tos=list(envelope.rcpt_tos),
                    message=message_from_bytes(envelope.content),
                )
            )
            return "250 Message accepted for delivery"

    def start(self) -> FakeSmtp:
        import socket

        from aiosmtpd.controller import Controller

        try:  # a controller needs a loop policy in the main thread
            asyncio.get_event_loop_policy().get_event_loop()
        except RuntimeError:  # pragma: no cover
            asyncio.set_event_loop(asyncio.new_event_loop())
        if not self.port:
            # aiosmtpd's readiness probe dials the port it was given, so port 0 never
            # becomes ready — pick a free port ourselves.
            with socket.socket() as probe:
                probe.bind((self.host, 0))
                self.port = int(probe.getsockname()[1])
        controller = Controller(self._Handler(self), hostname=self.host, port=self.port)
        controller.start()
        self._controller = controller
        return self

    def stop(self) -> None:
        if self._controller is not None:
            self._controller.stop()
            self._controller = None

    def __enter__(self) -> FakeSmtp:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()
