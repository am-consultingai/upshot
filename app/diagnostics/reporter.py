"""Crash reports: consent, the event, one per crash per day, and the last one kept (D87).

``diagnostics.crash_reports`` is ``unset`` until the user answers the question in setup,
then ``on`` or ``off``. Only ``on``, in a build that carries a DSN, sends anything.

An event is built from scratch with the fields below and nothing else: Sentry is told the
exception's type and scrubbed message, a stack of module and function names with line
numbers, the build, where it happened, a few coarse facts about the machine, and a random
install id (so Sentry can count how many installs a crash reaches; it is linked to
nothing). Not sent: the machine's name, the user's, the log, local variables, requests,
breadcrumbs, or anything from a meeting.

The same crash (its type and the innermost Upshot frame) is reported at most once a day
per install, so a crash in a loop is one report, not a thousand. The last report sent is
kept in ``<app home>/diagnostics/last-report.json`` for Settings to show.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app import paths
from app.config import Config
from app.diagnostics import scrub
from app.log import get

log = get(__name__)

CONSENT = ("unset", "on", "off")
_active: CrashReporter | None = None


class CrashReporter:
    def __init__(
        self,
        config: Config,
        *,
        dsn: str | None = None,
        frontend_dsn: str | None = None,
        version: str | None = None,
        commit: str | None = None,
        frozen: bool | None = None,
        home: Path | None = None,
        send: Callable[[str, dict[str, Any]], bool] | None = None,
        background: bool = True,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        from app.version import build_info

        info = build_info()
        self.config = config
        self.dsn = dsn if dsn is not None else info.sentry_dsn
        # The front end's own Sentry project (upshot-front): its stacks are JavaScript.
        self.frontend_dsn = frontend_dsn if frontend_dsn is not None else info.sentry_frontend_dsn
        self.version = version or info.version
        self.commit = commit if commit is not None else info.commit
        self.frozen = info.frozen if frozen is None else frozen
        self.home = (home or paths.app_home()) / "diagnostics"
        self._send = send
        self.background = background
        self.now = now or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ consent

    @property
    def available(self) -> bool:
        """This build can send (it carries a DSN). A build from source cannot."""
        return bool(self.dsn)

    @property
    def consent(self) -> str:
        value = str(self.config.get("diagnostics.crash_reports") or "unset")
        return value if value in CONSENT else "unset"

    @property
    def enabled(self) -> bool:
        return self.available and self.consent == "on"

    def install_id(self) -> str:
        """Random, made the first time a report is sent, and linked to nothing."""
        value = str(self.config.get("diagnostics.install_id") or "")
        if len(value) != 32:
            value = uuid.uuid4().hex
            self.config.set("diagnostics.install_id", value)
            self.config.save()
        return value

    # ------------------------------------------------------------------ reporting

    def report_exception(
        self, exc: BaseException, *, where: str, tags: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        """Report an exception, if the user agreed. The event sent, or None."""
        if not self.enabled:
            return None
        values = scrub.exception_values(exc)
        return self._report(
            {"exception": {"values": values}, "level": "error"},
            fingerprint=_fingerprint(values),
            where=where,
            tags=tags,
        )

    def report_native(
        self, frames: list[dict[str, Any]], *, version: str | None
    ) -> dict[str, Any] | None:
        """A crash Python never saw, found at the next start (``app.diagnostics.native``)."""
        if not self.enabled:
            return None
        value = {
            "type": "NativeCrash",
            "module": "upshot",
            "value": "Upshot closed unexpectedly",
            "stacktrace": {"frames": frames},
        }
        return self._report(
            {"exception": {"values": [value]}, "level": "fatal"},
            fingerprint=_fingerprint([value]),
            where="native",
            tags={"crashed_version": version or "?"},
        )

    def report_client(self, *, kind: str, message: str, stack: str) -> dict[str, Any] | None:
        """An error in the interface, posted by the page to the local server (D87, C5).

        The browser never talks to Sentry: it hands the error here, and only what
        ``scrub.client_exception`` keeps of it goes on, to the front end's project.
        """
        if not (self.enabled and self.frontend_dsn):
            return None
        value = scrub.client_exception(message, stack)
        return self._report(
            {"exception": {"values": [value]}, "level": "error"},
            fingerprint=_fingerprint([value]),
            where=f"ui:{kind}"[:40],
            tags=None,
            platform="javascript",
            dsn=self.frontend_dsn,
        )

    def last_report(self) -> dict[str, Any] | None:
        try:
            data = json.loads((self.home / "last-report.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _report(
        self,
        body: dict[str, Any],
        *,
        fingerprint: str,
        where: str,
        tags: dict[str, str] | None,
        platform: str = "python",
        dsn: str | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            if not self._first_today(fingerprint):
                return None
            event = self._event(body, where=where, tags=tags or {}, platform=platform)
            self.home.mkdir(parents=True, exist_ok=True)
            (self.home / "last-report.json").write_text(
                json.dumps(event, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        target = dsn or self.dsn
        if self.background:
            threading.Thread(
                target=self._deliver, args=(event, target), name="crash-report", daemon=True
            ).start()
        else:
            self._deliver(event, target)
        return event

    def _event(
        self, body: dict[str, Any], *, where: str, tags: dict[str, str], platform: str
    ) -> dict[str, Any]:
        """The whole report. Every field here is deliberate; nothing is added after."""
        allowed_tags = {
            **scrub.platform_tags(),
            "where": where,
            "frozen": str(self.frozen).lower(),
            "ui_language": str(self.config.get("ui.language") or "en"),
            "profile": str(self.config.get("profile") or "auto"),
            **{key: scrub.scrub_text(str(value)) for key, value in tags.items()},
        }
        return {
            "event_id": uuid.uuid4().hex,
            "timestamp": self.now().isoformat(),
            "platform": platform,
            "release": f"upshot@{self.version}",
            "dist": self.commit or "source",
            "environment": "production" if self.frozen else "source",
            "user": {"id": self.install_id()},
            "tags": allowed_tags,
            **body,
        }

    def _first_today(self, fingerprint: str) -> bool:
        path = self.home / "sent.json"
        today = self.now().date().isoformat()
        try:
            sent = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            sent = {}
        if not isinstance(sent, dict):
            sent = {}
        if sent.get(fingerprint) == today:
            return False
        sent = {key: day for key, day in sent.items() if day == today}  # older days go
        sent[fingerprint] = today
        self.home.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sent), encoding="utf-8")
        return True

    def _deliver(self, event: dict[str, Any], dsn: str | None) -> None:
        try:
            if self._send is not None:
                self._send("event", event)
                return
            from app.diagnostics.client import SentryClient

            assert dsn
            # One outbox per project: an item kept for later must reach the same one.
            box = "outbox-ui" if dsn == self.frontend_dsn and dsn != self.dsn else "outbox"
            SentryClient(dsn, self.home / box).send("event", event)
        except Exception:  # reporting a crash must never cause one
            log.exception("could not send a crash report")


def _fingerprint(values: list[dict[str, Any]]) -> str:
    """The exception's type and the innermost Upshot frame: the same crash, the same key."""
    last = values[-1]
    frames = last.get("stacktrace", {}).get("frames", [])
    ours = [f for f in frames if f.get("in_app")] or frames
    place = ours[-1].get("module") or ours[-1].get("filename") if ours else None
    where = f"{place}:{ours[-1]['function']}" if ours else "?"
    return f"{last.get('type')}@{where}"


# ---------------------------------------------------------------------- the one in use


def set_active(reporter: CrashReporter | None) -> None:
    """The reporter the excepthooks use (``app.log``). Set by the app at start."""
    global _active
    _active = reporter


def report(exc: BaseException, *, where: str) -> None:
    """Report through the active reporter, never raising: for the excepthooks, a stage
    that has failed for good, and the installer's download."""
    reporter = _active
    if reporter is None:
        return
    try:
        reporter.report_exception(exc, where=where)
    except Exception:
        log.exception("could not report an exception")
