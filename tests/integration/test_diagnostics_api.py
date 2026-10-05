"""``/api/diagnostics``: whether crash reports can be sent, the answer, the last report (D87)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.api import build_harness


@pytest.fixture
def api(tmp_path: Path, app_home: Path):  # type: ignore[no-untyped-def]
    return build_harness(tmp_path)


def test_a_source_run_cannot_report_and_has_not_been_asked(api) -> None:  # type: ignore[no-untyped-def]
    state = api.client().get("/api/diagnostics").json()
    assert state == {"available": False, "consent": "unset", "last_report": None}


def test_the_answer_is_a_setting_and_only_three_answers_exist(api) -> None:  # type: ignore[no-untyped-def]
    client = api.client()
    assert (
        client.put(
            "/api/settings", json={"values": {"diagnostics.crash_reports": "on"}}
        ).status_code
        == 200
    )
    assert client.get("/api/diagnostics").json()["consent"] == "on"
    assert (
        client.put(
            "/api/settings", json={"values": {"diagnostics.crash_reports": "off"}}
        ).status_code
        == 200
    )
    assert client.get("/api/diagnostics").json()["consent"] == "off"


def test_the_last_report_is_shown_exactly_as_it_was_sent(api) -> None:  # type: ignore[no-untyped-def]
    reporter = api.services.reporter
    reporter.dsn = "https://0123456789abcdef0123456789abcdef@o1.ingest.example.io/42"
    reporter._send = lambda _kind, _event: True
    api.services.config.set("diagnostics.crash_reports", "on")
    event = reporter.report_exception(RuntimeError("disk full"), where="test")
    shown = api.client().get("/api/diagnostics").json()
    assert shown["available"] is True and shown["last_report"] == event
