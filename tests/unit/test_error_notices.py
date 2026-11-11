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


# ------------------------------------------------------------------ review round 1


def _boom() -> BaseException:
    try:
        raise RuntimeError("Claude Code exited 1: boom")
    except RuntimeError as exc:
        return exc


def _record(meeting_id: str, exc: BaseException, stage: str = "summarize") -> dict[str, Any]:
    notice = notices.record(
        meeting_id=meeting_id, stage=stage, message=str(exc), at="now", exc=exc, sent=None
    )
    assert notice is not None
    return notice


def test_meetings_stopped_by_the_same_error_share_one_window_and_one_report(
    tmp_path: Path, active: None
) -> None:
    reporter, sent = _reporter(tmp_path, "off")
    set_active(reporter)
    error = _boom()
    first = _record("a", error)
    second = _record("b", error)
    third = _record("c", error)
    other = _record("d", ValueError("something else"))
    later = _record("e", error, stage="deliver")  # the same error at another stage
    assert len(notices.pending()) == 5
    assert first["fingerprint"] == second["fingerprint"] != later["fingerprint"]

    notices.close(first["id"], send=True, with_ids=frozenset({second["id"], third["id"]}))
    assert len(sent) == 1, "one report for the three meetings"
    assert [item["id"] for item in notices.pending()] == [other["id"], later["id"]]


def test_a_close_never_takes_a_notice_the_window_did_not_show(tmp_path: Path, active: None) -> None:
    reporter, _ = _reporter(tmp_path, "off")
    set_active(reporter)
    error = _boom()
    shown = _record("a", error)
    unseen = _record("b", error)  # recorded after the window was drawn
    notices.close(shown["id"], send=False)
    assert [item["id"] for item in notices.pending()] == [unseen["id"]]


def test_a_notices_file_that_cannot_be_written_never_stops_the_pipeline(
    h: Harness, tmp_path: Path, active: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows refuses the replace while another process holds the file: the meeting is
    still marked failed, with its error, and a later success still moves on."""

    def locked(*_args: Any, **_kwargs: Any) -> None:
        raise PermissionError("the file is in use")

    monkeypatch.setattr(notices.os, "replace", locked)
    monkeypatch.setattr(notices.time, "sleep", lambda _s: None)
    meeting_id = _fail_for_good(h)
    meeting = h.dao.require_meeting(meeting_id)
    assert meeting.state == "FAILED" and meeting.error
    notices.forget_meeting(meeting_id)  # no exception


def test_a_stage_that_goes_through_in_the_worker_clears_its_notice(h: Harness) -> None:
    meeting = h.meeting()
    h.stages.fail_times = 99
    h.queue.enqueue(meeting.id, "flaky")
    for _ in range(5):
        h.worker.run_once()
        h.clock.advance(3700)
    assert len(notices.pending()) == 1
    h.stages.fail_times = 0
    h.queue.retry(meeting.id, "flaky")
    h.worker.run_once()
    assert notices.pending() == []


def test_send_on_a_notice_that_did_not_ask_sends_nothing(tmp_path: Path, active: None) -> None:
    reporter, sent = _reporter(tmp_path, "on")
    set_active(reporter)
    notice = notices.record(
        meeting_id="m", stage="summarize", message="x", at="now", exc=_boom(),
        sent={"event_id": "0123456789abcdef"},
    )  # fmt: skip
    assert notice is not None
    notices.close(notice["id"], send=True)
    assert sent == []


def test_a_notice_replaced_by_a_newer_failure_is_gone(tmp_path: Path, active: None) -> None:
    reporter, sent = _reporter(tmp_path, "off")
    set_active(reporter)
    old = _record("m", _boom())
    _record("m", ValueError("again"))
    assert notices.close(old["id"], send=True) is None
    assert sent == []


def test_a_send_that_cannot_go_says_so() -> None:
    notice = _record("m", _boom())
    notice_file = notices.pending()[0]
    notice_file["report"] = {"status": "ask", "event": {"event_id": "x"}}
    notices._write([notice_file])
    closed = notices.close(notice["id"], send=True)
    assert closed is not None and closed["report"] == {"status": "not_sent"}
    assert [item["id"] for item in notices.pending()] == [notice["id"]], "kept: Send again"


def test_a_stage_that_goes_through_clears_only_its_own_notice() -> None:
    _record("m", _boom(), stage="deliver")
    notices.forget_meeting("m", stage="summarize")
    assert len(notices.pending()) == 1
    notices.forget_meeting("m", stage="deliver")
    assert notices.pending() == []


def test_a_report_sent_by_hand_counts_for_the_day(tmp_path: Path, active: None) -> None:
    """Turning reports on later the same day must not send the same crash again."""
    reporter, sent = _reporter(tmp_path, "off")
    set_active(reporter)
    error = _boom()
    notices.close(_record("m", error)["id"], send=True)
    assert len(sent) == 1
    reporter.config.set("diagnostics.crash_reports", "on")
    assert reporter.report_exception(error, where="stage:summarize") is None


def test_dropping_gone_meetings_never_takes_a_notice_recorded_meanwhile() -> None:
    _record("gone", _boom())
    seen = notices.pending()  # the list route reads...
    _record("new", ValueError("meanwhile"))  # ...the worker records another...
    notices.drop_meetings({str(item["meeting_id"]) for item in seen})  # ...the route prunes
    assert [item["meeting_id"] for item in notices.pending()] == ["new"]


def test_a_send_that_raises_is_not_lost_and_says_so(
    tmp_path: Path, active: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    reporter, _ = _reporter(tmp_path, "off")
    set_active(reporter)
    notice = _record("m", _boom())

    def locked(_event: dict[str, Any]) -> bool:
        raise PermissionError("sent.json is in use")

    monkeypatch.setattr(reporter, "send_prepared", locked)
    closed = notices.close(notice["id"], send=True)
    assert closed is not None and closed["report"] == {"status": "not_sent"}
    assert [item["id"] for item in notices.pending()] == [notice["id"]], "kept: Send again"


def test_a_missing_speech_model_opens_no_window() -> None:
    from app.asr.models import ModelNotInstalled

    assert notices.recognised(ModelNotInstalled("hebrew", "C:/models"))


@pytest.mark.parametrize("category", ["setup", "auth", "refusal", "unknown", "SAFETY", "OTHER"])
def test_a_provider_that_declined_whatever_its_category_opens_no_window(category: str) -> None:
    assert notices.recognised(PermanentError("declined", category=category))


def test_a_model_that_could_not_produce_valid_output_opens_the_window() -> None:
    assert not notices.recognised(PermanentError("no valid JSON", category="schema"))
