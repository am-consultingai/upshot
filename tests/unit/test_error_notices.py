"""The error window (owner, 2026-11-10): a meeting that failed for good for a reason
Upshot does not recognise gets a notice of its own, and the crash report follows the
user's answer: sent and said so when they agreed, shown and sent only on their word when
they did not."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.config import Config
from app.diagnostics import notices
from app.diagnostics.reporter import CrashReporter, set_active
from app.errors import PermanentError
from tests.unit.test_phase2_queue import Harness

DSN = "https://0123456789abcdef0123456789abcdef@o1.ingest.example.io/42"


@pytest.fixture
def h(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


def _reporter(tmp_path: Path, consent: str) -> tuple[CrashReporter, list[dict[str, Any]]]:
    sent: list[dict[str, Any]] = []
    config = Config.load(file=tmp_path / "diag_config.json")
    config.set("diagnostics.crash_reports", consent)
    made = CrashReporter(
        config,
        dsn=DSN,
        home=tmp_path,
        send=lambda _kind, event: sent.append(event) or True,
        background=False,
    )
    return made, sent


@pytest.fixture
def active() -> Iterator[None]:
    yield
    set_active(None)


def _fail_for_good(h: Harness) -> str:
    meeting = h.meeting()
    h.stages.fail_times = 99
    h.queue.enqueue(meeting.id, "flaky")
    for _ in range(5):
        h.worker.run_once()
        h.clock.advance(3700)
    return str(meeting.id)


def test_reports_on_the_report_is_sent_and_the_window_says_so(
    h: Harness, tmp_path: Path, active: None
) -> None:
    reporter, sent = _reporter(tmp_path, "on")
    set_active(reporter)
    meeting_id = _fail_for_good(h)
    assert len(sent) == 1
    [notice] = notices.pending()
    assert notice["meeting_id"] == meeting_id and notice["stage"] == "flaky"
    assert notice["report"] == {"status": "sent", "reference": sent[0]["event_id"][:8]}
    assert notice["message"], "what went wrong, in the window"


def test_reports_off_nothing_leaves_until_the_user_sends_it(
    h: Harness, tmp_path: Path, active: None
) -> None:
    reporter, sent = _reporter(tmp_path, "off")
    set_active(reporter)
    _fail_for_good(h)
    assert sent == [], "off: nothing sent by itself"
    [notice] = notices.pending()
    assert notice["report"]["status"] == "ask"
    shown = notice["report"]["event"]
    assert shown["tags"]["where"] == "stage:flaky"

    closed = notices.close(notice["id"], send=True)
    assert closed is not None and closed["report"]["status"] == "sent"
    assert sent == [shown], "exactly the report the window showed, and nothing else"
    assert notices.pending() == []


def test_dont_send_sends_nothing(h: Harness, tmp_path: Path, active: None) -> None:
    reporter, sent = _reporter(tmp_path, "unset")
    set_active(reporter)
    _fail_for_good(h)
    [notice] = notices.pending()
    assert notice["report"]["status"] == "ask", "not answered counts as off"
    notices.close(notice["id"], send=False)
    assert sent == [] and notices.pending() == []


def test_a_build_that_cannot_send_still_shows_the_error(h: Harness, active: None) -> None:
    _fail_for_good(h)
    [notice] = notices.pending()
    assert notice["report"] == {"status": "none"}


def test_a_failure_the_page_explains_opens_no_window() -> None:
    explained = PermanentError("Claude Code is not installed", category="setup")
    assert (
        notices.record(
            meeting_id="m", stage="summarize", message="x", at="now", exc=explained, sent=None
        )
        is None
    )
    assert notices.pending() == []


def test_one_notice_per_meeting_and_gone_when_it_is_summarized_after_all() -> None:
    for _ in range(2):
        notices.record(
            meeting_id="m", stage="summarize", message="x", at="now",
            exc=RuntimeError("boom"), sent=None,
        )  # fmt: skip
    assert len(notices.pending()) == 1
    notices.forget_meeting("m")
    assert notices.pending() == []


def test_closing_an_unknown_notice_is_none() -> None:
    assert notices.close("nope", send=True) is None
