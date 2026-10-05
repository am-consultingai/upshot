"""Send an event or a piece of feedback to Sentry, or keep it for later (D87).

One HTTPS POST of an *envelope* (``https://develop.sentry.dev/sdk/envelopes/``) to the
project the DSN names, authenticated by the DSN's public key, which only lets a program
send. Every item is written to ``<app home>/diagnostics/outbox/`` first and removed once
Sentry accepts it, so a report made offline goes out when the network is back. The outbox
keeps at most ``OUTBOX_MAX`` items: a machine offline for a month does not fill its disk.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.log import get

log = get(__name__)

OUTBOX_MAX = 20
HTTP_TIMEOUT_S = 15.0


@dataclass(frozen=True)
class Dsn:
    key: str
    host: str
    project: str

    @classmethod
    def parse(cls, dsn: str) -> Dsn:
        parsed = urlparse(dsn)
        if parsed.scheme != "https" or not parsed.username or not parsed.hostname:
            raise ValueError("not a Sentry DSN")
        return cls(parsed.username, parsed.hostname, parsed.path.strip("/"))

    @property
    def endpoint(self) -> str:
        return f"https://{self.host}/api/{self.project}/envelope/"

    @property
    def auth(self) -> str:
        return f"Sentry sentry_version=7, sentry_key={self.key}, sentry_client=upshot/1"


def envelope(item_type: str, payload: dict[str, Any]) -> bytes:
    event_id = str(payload.get("event_id") or uuid.uuid4().hex)
    sent_at = datetime.now(UTC).isoformat()
    lines = [
        json.dumps({"event_id": event_id, "sent_at": sent_at}),
        json.dumps({"type": item_type}),
        json.dumps(payload, ensure_ascii=False),
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


class SentryClient:
    def __init__(self, dsn: str, outbox: Path, *, http: httpx.Client | None = None) -> None:
        self.dsn = Dsn.parse(dsn)
        self.outbox = outbox
        self._http = http

    def send(self, item_type: str, payload: dict[str, Any]) -> bool:
        """Keep it, then try to deliver everything kept. True when this one went."""
        path = self._keep(item_type, payload)
        return path in self._deliver()

    def flush(self) -> int:
        """Deliver what the outbox holds, oldest first. How many Sentry accepted."""
        return len(self._deliver())

    def _deliver(self) -> set[Path]:
        accepted: set[Path] = set()
        if not self.outbox.is_dir():
            return accepted
        for path in sorted(self.outbox.glob("*.json")):
            try:
                kept = json.loads(path.read_text(encoding="utf-8"))
                body = envelope(kept["type"], kept["payload"])
            except (OSError, ValueError, KeyError):
                path.unlink(missing_ok=True)  # a broken item would block the rest for ever
                continue
            try:
                response = self._client().post(
                    self.dsn.endpoint,
                    content=body,
                    headers={
                        "Content-Type": "application/x-sentry-envelope",
                        "X-Sentry-Auth": self.dsn.auth,
                    },
                )
            except httpx.HTTPError as exc:
                log.info("diagnostics: not sent now (%s); kept for later", type(exc).__name__)
                return accepted
            if response.status_code == 429 or response.status_code >= 500:
                log.info("diagnostics: Sentry answered %s; kept for later", response.status_code)
                return accepted
            # Anything else is final: accepted, or refused for good (a 4xx will not change).
            path.unlink(missing_ok=True)
            if response.status_code < 300:
                accepted.add(path)
            else:
                log.info("diagnostics: Sentry refused a report (%s)", response.status_code)
        return accepted

    def _keep(self, item_type: str, payload: dict[str, Any]) -> Path:
        self.outbox.mkdir(parents=True, exist_ok=True)
        kept = sorted(self.outbox.glob("*.json"))
        for old in kept[: max(0, len(kept) - OUTBOX_MAX + 1)]:
            old.unlink(missing_ok=True)
        path = self.outbox / f"{time.time_ns()}-{item_type}.json"
        path.write_text(json.dumps({"type": item_type, "payload": payload}), encoding="utf-8")
        return path

    def _client(self) -> httpx.Client:
        if self._http is None:
            from app.version import build_info

            self._http = httpx.Client(
                timeout=HTTP_TIMEOUT_S, headers={"User-Agent": f"Upshot/{build_info().version}"}
            )
        return self._http
