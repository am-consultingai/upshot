"""``/api/updates``: what Settings and the tray show about the next version (D87)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.api import build_harness


@pytest.fixture
def api(tmp_path: Path, app_home: Path):  # type: ignore[no-untyped-def]
    return build_harness(tmp_path)


def test_the_state_says_what_is_installed_and_that_a_source_run_does_not_update(api) -> None:  # type: ignore[no-untyped-def]
    state = api.client().get("/api/updates").json()
    assert state["phase"] == "idle" and state["available"] is None
    assert state["channel"] == "stable" and state["auto_install"] is True
    assert state["enabled"] is False, "the suite runs from source, with checks turned off"
    assert state["current"]


def test_the_state_says_whether_this_copy_can_install_and_what_it_waits_for(api) -> None:  # type: ignore[no-untyped-def]
    state = api.client().get("/api/updates").json()
    assert state["install"] == {"can_install": False, "waiting_for": None, "last": None}


def test_install_now_with_nothing_ready_is_not_found(api) -> None:  # type: ignore[no-untyped-def]
    response = api.client().post("/api/updates/install")
    assert response.status_code == 404 and "no update" in response.json()["detail"]


def test_check_now_answers_at_once(api) -> None:  # type: ignore[no-untyped-def]
    updates = api.services.updates
    calls: list[bool] = []
    updates.check_now = lambda **_: calls.append(True) or updates.state()
    response = api.client().post("/api/updates/check")
    assert response.status_code == 200 and response.json()["phase"] == "checking"


def test_check_now_needs_the_csrf_header_like_every_change(api) -> None:  # type: ignore[no-untyped-def]
    client = api.client()
    del client.headers["x-csrf-token"]
    assert client.post("/api/updates/check").status_code == 403


def test_the_system_status_says_whether_this_build_can_report(api) -> None:  # type: ignore[no-untyped-def]
    build = api.client().get("/api/status").json()["build"]
    assert build["reports"] is False, "a run from source has no DSN"
