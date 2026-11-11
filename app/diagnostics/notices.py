"""The error window: a meeting that failed for a reason Upshot does not recognise.

When a stage fails for good, the user is shown what went wrong in a window of its own,
not only in the meeting's red box (owner, 2026-11-10). What happens to the crash report
follows the user's answer in setup (D87):

- **on**: the report is sent as before, and the window says so, with its reference;
- **off** or not answered: the window shows the report it would send, built by the same
  code that sends, and sends it only if the user presses Send.

A failure Upshot recognises — a CLI to install or update, a key to fix, a provider that
refused — is not one of these: the meeting page already says what to do about it.

The notices are kept in ``<app home>/diagnostics/error-notices.json`` until closed, so
one shown while nobody was looking is still there after a restart. The message shown is
the job's own error, on this computer only; what may leave it is the report alone.
"""

from __future__ import annotations

import functools
import json
import os
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app import paths
from app.errors import PermanentError
from app.log import get

log = get(__name__)

#: How many notices are kept; older ones go, shown or not.
KEEP = 20

_lock = threading.Lock()


#: Failures whose message says what to do about them: install or repair something, fix a
#: key or a sign-in, fill in the mail settings, or accept that the provider declined.
EXPLAINED = frozenset({"setup", "auth", "config", "refusal", "content_filter", "safety"})


def recognised(exc: BaseException) -> bool:
    """A failure the meeting page already explains, with a way out: no window for it. A
    model that could not produce valid output (``schema``) is not one of these."""
    return isinstance(exc, PermanentError) and exc.category in EXPLAINED


def _path(home: Path | None = None) -> Path:
    return (home or paths.app_home()) / "diagnostics" / "error-notices.json"


def _read(home: Path | None = None) -> list[dict[str, Any]]:
    try:
        data = json.loads(_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def _write(items: list[dict[str, Any]], home: Path | None = None) -> None:
    """Whole or not at all: a crash mid-write must not lose every notice. Windows refuses
    the replace while another process has the file open (an indexer, an antivirus scan):
    tried again briefly, as ``app.prepare`` does."""
    path = _path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(items[-KEEP:], ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(10):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.05)


def _never_raises(default: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """The window is a courtesy: a notice that cannot be kept or dropped must never stop
    a stage from being marked failed, the next stage from being queued, or a delete from
    being reported as done. Logged, and ``default`` returned."""

    def wrap(function: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(function)
        def guarded(*args: Any, **kwargs: Any) -> Any:
            try:
                return function(*args, **kwargs)
            except Exception:
                log.exception("error notices: %s failed", function.__name__)
                return default

        return guarded

    return wrap


def fingerprint(exc: BaseException, stage: str = "") -> str:
    """The same error, the same key: its type and the innermost Upshot frame, as the
    reporter counts crashes, at the same stage. Meetings stopped by one error share one
    window."""
    from app.diagnostics import scrub
    from app.diagnostics.reporter import _fingerprint

    try:
        return f"{stage}|{_fingerprint(scrub.exception_values(exc))}"
    except Exception:  # a notice must never fail for want of a key
        return f"{stage}|{type(exc).__name__}"


@_never_raises(None)
def record(
    *,
    meeting_id: str,
    stage: str,
    message: str,
    at: str,
    exc: BaseException,
    sent: dict[str, Any] | None,
    home: Path | None = None,
) -> dict[str, Any] | None:
    """Keep a notice for a stage that failed for good; ``sent`` is the report already sent,
    if the user agreed to reports. ``None`` for a failure the page already explains."""
    if recognised(exc):
        return None
    from app.diagnostics import reporter as reporting

    active = reporting.active()
    report: dict[str, Any]
    if sent is not None:
        report = {"status": "sent", "reference": str(sent.get("event_id", ""))[:8]}
    elif active is not None and active.enabled:
        # Agreed to, and this crash was already reported today (one a day per crash).
        report = {"status": "sent", "reference": ""}
    elif active is not None and active.available:
        event = active.prepare_exception(exc, where=f"stage:{stage}")
        report = {"status": "ask", "event": event} if event else {"status": "none"}
    else:
        report = {"status": "none"}  # a build that cannot send
    notice = {
        "id": uuid.uuid4().hex[:12],
        "fingerprint": fingerprint(exc, stage),
        "meeting_id": meeting_id,
        "stage": stage,
        "message": message[:2000],
        "at": at,
        "report": report,
    }
    with _lock:
        items = [item for item in _read(home) if item.get("meeting_id") != meeting_id]
        items.append(notice)
        _write(items, home)
    log.info("error notice %s for %s (%s): report %s", notice["id"], meeting_id, stage,
             report["status"])  # fmt: skip
    return notice


@_never_raises([])
def pending(home: Path | None = None) -> list[dict[str, Any]]:
    """The notices not closed yet, oldest first."""
    with _lock:
        return _read(home)


def close(
    notice_id: str,
    *,
    send: bool,
    with_ids: frozenset[str] = frozenset(),
    home: Path | None = None,
) -> dict[str, Any] | None:
    """Close a notice, and with it the other notices of the same error the window showed
    (``with_ids``: never one recorded after it was drawn); with ``send``, first send the
    report it showed — one report, whatever the number of meetings, and only for a notice
    that asked. The notice as it ended, or ``None`` when there is no such notice.

    The report goes before the notice is removed, and a send that fails says so rather
    than raising: the user's Send is never lost to a file Windows holds open."""
    with _lock:
        found = next((item for item in _read(home) if item.get("id") == notice_id), None)
    if found is None:
        return None
    report = found.get("report") or {}
    if send and report.get("status") == "ask" and isinstance(report.get("event"), dict):
        from app.diagnostics import reporter as reporting

        active = reporting.active()
        event = report["event"]
        try:
            went = active is not None and active.send_prepared(event)
        except Exception:
            log.exception("error notice %s: the report could not be sent", notice_id)
            went = False
        if went:
            found["report"] = {"status": "sent", "reference": str(event.get("event_id", ""))[:8]}
            log.info("error notice %s: report sent on the user's word", notice_id)
        else:
            # Kept, so Send can be pressed again when it comes back.
            return {**found, "report": {"status": "not_sent"}}
    key = found.get("fingerprint")
    try:
        with _lock:
            items = _read(home)
            closing = {notice_id} | {
                str(item.get("id"))
                for item in items
                if item.get("id") in with_ids and item.get("fingerprint") == key
            }
            _write([item for item in items if item.get("id") not in closing], home)
    except Exception:  # the report went; a notice that lingers is the lesser harm
        log.exception("error notice %s: could not be removed", notice_id)
    return found


@_never_raises(None)
def forget_meeting(meeting_id: str, *, stage: str | None = None, home: Path | None = None) -> None:
    """A meeting deleted (every notice of it, and the error text it keeps on disk), or a
    ``stage`` of it that went through after all (that stage's notice)."""
    with _lock:
        items = _read(home)
        kept = [
            item
            for item in items
            if item.get("meeting_id") != meeting_id
            or (stage is not None and item.get("stage") != stage)
        ]
        if len(kept) != len(items):
            _write(kept, home)


@_never_raises(None)
def drop_meetings(meeting_ids: set[str], home: Path | None = None) -> None:
    """Drop the notices of these meetings, found to no longer exist (removed by a path
    that could not tidy up, or from another library): their error text must not outlive
    them. Only these: a notice recorded meanwhile for another meeting stays."""
    if not meeting_ids:
        return
    with _lock:
        items = _read(home)
        kept = [item for item in items if str(item.get("meeting_id")) not in meeting_ids]
        if len(kept) != len(items):
            _write(kept, home)
