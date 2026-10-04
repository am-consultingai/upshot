"""``/api/legal``: the Terms the interface must show, and accepting them (D83)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.legal.terms import bundled
from tests.fixtures.api import build_harness


@pytest.fixture
def api(tmp_path: Path, app_home: Path):  # type: ignore[no-untyped-def]
    return build_harness(tmp_path)


def test_a_fresh_install_shows_the_terms_until_they_are_accepted(api) -> None:  # type: ignore[no-untyped-def]
    client = api.client()
    state = client.get("/api/legal").json()
    assert state["gate"] is True and state["accepted_version"] is None
    assert state["current"]["version"] == bundled().version

    document = client.get("/api/legal/terms").json()
    assert document["version"] == bundled().version
    assert '<section id="agreement">' in document["html"]

    accepted = client.post("/api/legal/accept", json={"version": bundled().version})
    assert accepted.status_code == 200
    assert accepted.json()["gate"] is False
    assert client.get("/api/legal").json()["accepted_via"] == "app"


def test_an_unknown_version_cannot_be_accepted(api) -> None:  # type: ignore[no-untyped-def]
    client = api.client()
    assert client.post("/api/legal/accept", json={"version": "2099-01-01"}).status_code == 404
    assert client.get("/api/legal/terms", params={"version": "2099-01-01"}).status_code == 404


def test_accepting_needs_the_csrf_header_like_every_change(api) -> None:  # type: ignore[no-untyped-def]
    client = api.client()
    del client.headers["x-csrf-token"]
    response = client.post("/api/legal/accept", json={"version": bundled().version})
    assert response.status_code == 403
