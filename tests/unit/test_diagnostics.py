"""Crash reports: consent, the allowlist, the outbox, native crashes, and privacy (D87)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.config import Config
from app.diagnostics import native, scrub
from app.diagnostics.client import OUTBOX_MAX, SentryClient
from app.diagnostics.reporter import CrashReporter, report, set_active

DSN = "https://0123456789abcdef0123456789abcdef@o1.ingest.example.io/42"
#: Everything a report must never carry (C7).
TITLE = "פגישת תקציב רבעונית עם דנה"
SLUG = f"2026-10-05_1944_0bac74_{TITLE.replace(' ', '-')}"
EMAIL = "dana.cohen@example.co.il"
SENTENCE = "we agreed to move the launch to March and tell the board on Sunday"
SECRET = "sk-ant-api03-" + "A" * 40


class Sent:
    def __init__(self) -> None:
        self.items: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, kind: str, payload: dict[str, Any]) -> bool:
        self.items.append((kind, payload))
        return True


def reporter(
    tmp_path: Path, *, consent: str = "on", dsn: str | None = DSN, day: int = 5
) -> tuple[CrashReporter, Sent]:
    config = Config.load(file=tmp_path / "app_config.json")
    config.set("diagnostics.crash_reports", consent)
    sent = Sent()
    clock = {"now": datetime(2026, 10, day, 12, tzinfo=UTC)}
    made = CrashReporter(
        config,
        dsn=dsn,
        version="0.2.0",
        commit="abcdef123456",
        frozen=True,
        home=tmp_path,
        send=sent,
        background=False,
        now=lambda: clock["now"],
    )
    made.clock = clock  # type: ignore[attr-defined]
    return made, sent


def crash(message: str) -> BaseException:
    def deep(meeting_title: str, api_key: str) -> None:  # locals that must never leave
        raise ValueError(message)

    try:
        deep(TITLE, SECRET)
    except ValueError as exc:
        return exc
    raise AssertionError("unreachable")


# ------------------------------------------------------------------ scrubbing


@pytest.mark.parametrize(
    ("text", "gone", "kept"),
    [
        (r"C:\Users\someone\AppData\Local\upshot\x.db locked", "someone", r"C:\Users\<user>"),
        ("/home/someone/projects/upshot/a.py", "someone", "/home/<user>"),
        (f"no such file {SLUG}/audio.wav", TITLE.split()[0], "<meeting>"),
        (f"invite from {EMAIL} failed", EMAIL, "<email>"),
        (
            "GET https://api.example.com/v1/users/42?token=abc failed",
            "users/42",
            "https://api.example.com/…",
        ),
        (f"bad key {SECRET}", SECRET, "<redacted>"),
        (f"could not parse '{SENTENCE}'", SENTENCE, "<text>"),
        (f"meeting {TITLE} not found", TITLE.split()[0], "meeting <text> not found"),
    ],
)
def test_the_scrubber_removes_what_identifies_or_quotes(text: str, gone: str, kept: str) -> None:
    out = scrub.scrub_text(text, homes=[])
    assert gone not in out and kept in out


def test_a_short_identifier_in_quotes_is_kept_and_long_text_is_cut() -> None:
    assert scrub.scrub_text("KeyError: 'version'") == "KeyError: 'version'"
    assert len(scrub.scrub_text("x" * 5000, homes=[])) <= scrub.MAX_TEXT


def test_the_home_folders_become_a_tilde(tmp_path: Path) -> None:
    home = str(tmp_path / "home" / "Dana")
    assert scrub.scrub_text(f"{home}/meetings missing", homes=[home]) == "~/meetings missing"


def test_frames_carry_names_and_lines_but_no_paths_or_locals() -> None:
    exc = crash("boom")
    frames = scrub.frames_from_traceback(exc.__traceback__)
    assert frames[-1]["function"] == "deep" and isinstance(frames[-1]["lineno"], int)
    assert set(frames[-1]) == {"module", "function", "lineno", "in_app"}
    assert frames[-1]["module"] == __name__ and frames[-1]["in_app"] is False


def test_a_chained_exception_reports_both_innermost_last() -> None:
    try:
        try:
            raise KeyError("inner")
        except KeyError as inner:
            raise RuntimeError("outer") from inner
    except RuntimeError as exc:
        values = scrub.exception_values(exc)
    assert [v["type"] for v in values] == ["KeyError", "RuntimeError"]


# ------------------------------------------------------------------ consent


@pytest.mark.parametrize(("consent", "dsn"), [("unset", DSN), ("off", DSN), ("on", None)])
def test_nothing_is_sent_without_a_yes_and_a_dsn(
    tmp_path: Path, consent: str, dsn: str | None
) -> None:
    made, sent = reporter(tmp_path, consent=consent, dsn=dsn)
    assert made.report_exception(crash("x"), where="test") is None
    assert sent.items == [] and made.last_report() is None
    assert made.config.get("diagnostics.install_id") is None, "no id made without a report"


def test_an_unknown_answer_counts_as_not_answered(tmp_path: Path) -> None:
    made, _ = reporter(tmp_path, consent="maybe")
    assert made.consent == "unset" and made.enabled is False


# ------------------------------------------------------------------ the event


def test_the_event_has_exactly_the_allowlisted_fields(tmp_path: Path) -> None:
    made, sent = reporter(tmp_path)
    event = made.report_exception(crash("disk full"), where="stage:transcribe")
    assert event is not None and sent.items == [("event", event)]
    assert set(event) == {
        "event_id", "timestamp", "platform", "release", "dist", "environment",
        "user", "tags", "exception", "level",
    }  # fmt: skip
    assert event["release"] == "upshot@0.2.0" and event["dist"] == "abcdef123456"
    assert set(event["user"]) == {"id"} and len(event["user"]["id"]) == 32
    assert event["tags"]["where"] == "stage:transcribe"
    assert "server_name" not in json.dumps(event)
    assert made.last_report() == event


def test_the_install_id_is_made_once_and_kept(tmp_path: Path) -> None:
    made, _ = reporter(tmp_path)
    first = made.install_id()
    assert Config.load(file=tmp_path / "app_config.json").get("diagnostics.install_id") == first
    assert made.install_id() == first


def test_the_same_crash_is_reported_once_a_day(tmp_path: Path) -> None:
    made, sent = reporter(tmp_path)
    assert made.report_exception(crash("a"), where="t") is not None
    assert made.report_exception(crash("b"), where="t") is None, "same type, same place"
    try:
        raise KeyError("other")
    except KeyError as other:
        assert made.report_exception(other, where="t") is not None, "a different crash"
    made.clock["now"] += timedelta(days=1)  # type: ignore[attr-defined]
    assert made.report_exception(crash("c"), where="t") is not None, "the next day"
    assert len(sent.items) == 3


def test_report_goes_through_the_active_reporter_and_never_raises(tmp_path: Path) -> None:
    made, sent = reporter(tmp_path)
    set_active(made)
    try:
        report(crash("x"), where="uncaught")
        assert len(sent.items) == 1
        made._send = lambda *_: (_ for _ in ()).throw(OSError("no network"))  # type: ignore[assignment]
        report(KeyError("y"), where="uncaught")  # must not raise
    finally:
        set_active(None)
    report(crash("z"), where="uncaught")  # no reporter: nothing, quietly


# ------------------------------------------------------------------ C7: no meeting content


def test_no_meeting_content_or_identity_ever_reaches_a_report(tmp_path: Path) -> None:
    """C7. A crash whose message is full of a meeting, and whose frames hold a title and
    a key in their locals: the report carries none of it."""
    made, _ = reporter(tmp_path)
    home = str(Path.home())
    message = (
        f"cannot open {home}/AppData/Local/upshot/meetings/{SLUG}/transcript.json "
        f"for {EMAIL}: '{SENTENCE}' (key {SECRET})"
    )
    made.report_exception(crash(message), where="stage:summarize")
    try:
        raise OSError(f"[Errno 13] Permission denied: 'C:\\Users\\Dana\\Documents\\{TITLE}.docx'")
    except OSError as exc:
        made.report_exception(exc, where="uncaught")
    sent = json.dumps([made.last_report()], ensure_ascii=False)
    everything = (tmp_path / "diagnostics" / "last-report.json").read_text(encoding="utf-8")
    for forbidden in (
        TITLE,
        *TITLE.split(),
        EMAIL,
        SENTENCE,
        SECRET,
        home,
        "Dana",
        "transcript.json",
    ):
        assert forbidden not in sent and forbidden not in everything, forbidden


# ------------------------------------------------------------------ native crashes (C4)


APP = r"C:\Users\Dana\AppData\Local\Programs\Upshot\_internal\app"
DUMP = f"""Fatal Python error: Segmentation fault

Thread 0x00001234 (most recent call first):
  File "{APP}\\asr\\local.py", line 210 in transcribe
  File "{APP}\\pipeline\\worker.py", line 120 in _run
  File "C:\\Python\\Lib\\threading.py", line 1012 in run

Current thread 0x00005678 (most recent call first):
  File "C:\\Python\\Lib\\threading.py", line 300 in wait
"""


def test_a_crash_dump_becomes_frames_without_paths(tmp_path: Path) -> None:
    folder = tmp_path / "diagnostics"
    folder.mkdir()
    (folder / "running.json").write_text(
        json.dumps({"pid": 99999, "version": "0.2.0"}), encoding="utf-8"
    )
    (folder / "fault-99999.log").write_text(DUMP, encoding="utf-8")
    found = native.found(tmp_path, own_pid=1)
    assert found is not None and found["version"] == "0.2.0"
    assert [f["module"] for f in found["frames"]] == [
        "threading",
        "app.pipeline.worker",
        "app.asr.local",
    ]
    assert "Dana" not in json.dumps(found) and "Users" not in json.dumps(found)
    assert not list(folder.iterdir()), "read once, then gone"
    made, _ = reporter(tmp_path)
    event = made.report_native(found["frames"], version=found["version"])
    assert event and event["level"] == "fatal" and event["tags"]["where"] == "native"


def test_a_run_that_just_stopped_is_not_a_crash(tmp_path: Path) -> None:
    folder = tmp_path / "diagnostics"
    folder.mkdir()
    (folder / "running.json").write_text(json.dumps({"pid": 99999}), encoding="utf-8")
    (folder / "fault-99999.log").write_text("", encoding="utf-8")
    assert native.found(tmp_path, own_pid=1) is None
    assert native.found(tmp_path, own_pid=1) is None, "and nothing the second time"


def test_arming_and_a_clean_quit_leave_nothing(tmp_path: Path) -> None:
    native.arm(tmp_path, version="0.2.0")
    assert (tmp_path / "diagnostics" / "running.json").exists()
    native.disarm(tmp_path)
    assert not list((tmp_path / "diagnostics").iterdir())


# ------------------------------------------------------------------ the client


class Sentry:
    def __init__(self) -> None:
        self.bodies: list[bytes] = []
        self.status = 200
        self.offline = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.offline:
            raise httpx.ConnectError("offline", request=request)
        assert request.url == "https://o1.ingest.example.io/api/42/envelope/"
        assert "sentry_key=0123456789abcdef0123456789abcdef" in request.headers["x-sentry-auth"]
        self.bodies.append(request.content)
        return httpx.Response(self.status)

    def client(self, tmp_path: Path) -> SentryClient:
        return SentryClient(
            DSN, tmp_path / "outbox", http=httpx.Client(transport=httpx.MockTransport(self.handle))
        )


def test_an_envelope_is_three_lines_and_leaves_the_outbox_once_accepted(tmp_path: Path) -> None:
    sentry = Sentry()
    assert sentry.client(tmp_path).send("event", {"event_id": "e" * 32, "level": "error"}) is True
    header, item, payload = sentry.bodies[0].decode().strip().split("\n")
    assert json.loads(header)["event_id"] == "e" * 32 and json.loads(item) == {"type": "event"}
    assert json.loads(payload)["level"] == "error"
    assert not list((tmp_path / "outbox").glob("*.json"))


def test_offline_it_is_kept_and_sent_later_and_a_refusal_is_not_retried(tmp_path: Path) -> None:
    sentry = Sentry()
    sentry.offline = True
    client = sentry.client(tmp_path)
    assert client.send("event", {"n": 1}) is False
    assert len(list((tmp_path / "outbox").glob("*.json"))) == 1
    sentry.offline = False
    assert client.flush() == 1 and not list((tmp_path / "outbox").glob("*.json"))
    sentry.status = 400
    assert client.send("event", {"n": 2}) is False
    assert not list((tmp_path / "outbox").glob("*.json")), "a 400 will not change: dropped"


def test_the_outbox_is_bounded(tmp_path: Path) -> None:
    sentry = Sentry()
    sentry.offline = True
    client = sentry.client(tmp_path)
    for n in range(OUTBOX_MAX + 5):
        client.send("event", {"n": n})
    kept = sorted((tmp_path / "outbox").glob("*.json"))
    assert len(kept) == OUTBOX_MAX
    assert json.loads(kept[-1].read_text(encoding="utf-8"))["payload"]["n"] == OUTBOX_MAX + 4


# ------------------------------------------------------------------ the interface (C5)

STACK = f"""TypeError: Cannot read properties of undefined (reading 'title')
    at SummaryView (http://127.0.0.1:8000/assets/index-AbC123.js?v={SLUG}:12:345)
    at renderWithHooks (http://127.0.0.1:8000/assets/vendor-XyZ.js:1:999)
    at http://127.0.0.1:8000/meeting/{SLUG}:3:7"""


def test_a_page_stack_keeps_names_bundles_and_positions_only() -> None:
    value = scrub.client_exception(f"failed to render {TITLE} for {EMAIL}", STACK)
    assert value["type"] == "TypeError"
    frames = value["stacktrace"]["frames"]
    assert [f["function"] for f in frames] == ["?", "renderWithHooks", "SummaryView"]
    assert frames[-1] == {
        "function": "SummaryView", "abs_path": "app:///assets/index-AbC123.js",
        "filename": "assets/index-AbC123.js", "lineno": 12, "colno": 345, "in_app": True,
    }  # fmt: skip
    dumped = json.dumps(value, ensure_ascii=False)
    for forbidden in (TITLE, *TITLE.split(), EMAIL, "127.0.0.1", "?v=", "/meeting/"):
        assert forbidden not in dumped, forbidden


def test_a_page_error_goes_to_the_front_end_project_only_with_consent(tmp_path: Path) -> None:
    made, sent = reporter(tmp_path, consent="unset")
    made.frontend_dsn = DSN.replace("/42", "/43")
    assert made.report_client(kind="render", message="x", stack=STACK) is None
    made.config.set("diagnostics.crash_reports", "on")
    event = made.report_client(kind="render", message="x", stack=STACK)
    assert event and event["platform"] == "javascript" and event["tags"]["where"] == "ui:render"
    assert sent.items == [("event", event)]
    made.frontend_dsn = None
    assert made.report_client(kind="render", message="y", stack="Error: other\n") is None


def _raised_in(module: str, message: str) -> BaseException:
    """An exception raised by code whose module is ``module`` (its frames say so)."""
    namespace: dict[str, Any] = {"__name__": module}
    exec("def fail(m):\n    raise RuntimeError(m)\n", namespace)
    try:
        namespace["fail"](message)
    except RuntimeError as exc:
        return exc
    raise AssertionError("unreachable")


def test_what_an_ai_cli_said_is_withheld_but_what_failed_is_kept(tmp_path: Path) -> None:
    said = f"Claude Code exited 1: {SENTENCE}, and the board meets Sunday"
    made, _ = reporter(tmp_path)
    event = made.report_exception(_raised_in("app.llm.claude_cli", said), where="stage:summarize")
    assert event is not None
    value = event["exception"]["values"][-1]["value"]
    assert value == "Claude Code exited 1: <withheld>"
    elsewhere = scrub.exception_values(_raised_in("app.pipeline.worker", "disk full: C drive"))
    assert elsewhere[-1]["value"] == "disk full: C drive", "only the AI code is withheld"
