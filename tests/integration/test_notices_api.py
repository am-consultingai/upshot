"""``/api/legal/notices``: the third-party components and their licences, for the app's page."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import paths
from app.legal import notices
from tests.fixtures.api import build_harness

ENTRIES = [
    {"name": "FFmpeg (Upshot's audio-only build)", "version": "", "licence": "LGPL-2.1-or-later",
     "kind": "other", "note": "ffmpeg.exe", "source": "https://example.invalid/src",
     "texts": ["GNU LESSER GENERAL PUBLIC LICENSE", "zlib licence"]},
    {"name": "numpy", "version": "2.5.2", "licence": "BSD-3-Clause", "kind": "python",
     "note": "", "source": "", "texts": ["BSD text"]},
]  # fmt: skip


@pytest.fixture
def api(tmp_path: Path, app_home: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    path = tmp_path / "THIRD-PARTY-NOTICES.json"
    path.write_text(json.dumps({"entries": ENTRIES}), encoding="utf-8")
    monkeypatch.setenv(notices.NOTICES_ENV, str(path))
    return build_harness(tmp_path)


def test_the_list_carries_every_entry_but_not_the_texts(api) -> None:  # type: ignore[no-untyped-def]
    body = api.client().get("/api/legal/notices").json()
    assert body["available"] is True
    assert [(e["id"], e["name"], e["licence"]) for e in body["entries"]] == [
        (0, "FFmpeg (Upshot's audio-only build)", "LGPL-2.1-or-later"),
        (1, "numpy", "BSD-3-Clause"),
    ]
    assert "texts" not in body["entries"][0]
    assert body["entries"][0]["source"] == "https://example.invalid/src"


def test_one_entry_s_texts_come_on_request(api) -> None:  # type: ignore[no-untyped-def]
    client = api.client()
    assert client.get("/api/legal/notices/0").json() == {
        "id": 0, "texts": ["GNU LESSER GENERAL PUBLIC LICENSE", "zlib licence"],
    }  # fmt: skip
    assert client.get("/api/legal/notices/2").status_code == 404
    assert client.get("/api/legal/notices/-1").status_code == 404


def test_a_build_without_notices_says_so(tmp_path: Path, app_home: Path,
                                         monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    monkeypatch.setenv(notices.NOTICES_ENV, str(tmp_path / "missing.json"))
    monkeypatch.setattr(notices, "paths", type("P", (), {
        "is_frozen": staticmethod(lambda: False),
        "resource": staticmethod(lambda *p: tmp_path / "nowhere" / p[-1]),
    }))  # fmt: skip
    body = build_harness(tmp_path).client().get("/api/legal/notices").json()
    assert body == {"available": False, "entries": []}


def test_the_installed_app_ignores_the_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"entries": ENTRIES}), encoding="utf-8")
    monkeypatch.setenv(notices.NOTICES_ENV, str(other))
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    found = notices.notices_path()
    assert found != other
    assert found is None or found.parent in (paths.resource(), paths.resource("vendor"))


def test_a_corrupt_file_reads_as_no_list(tmp_path: Path, app_home: Path,
                                         monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    client = None
    for body in ("{not json", "[1, 2]", '{"entries": "x"}'):
        path = tmp_path / "THIRD-PARTY-NOTICES.json"
        path.write_text(body, encoding="utf-8")
        monkeypatch.setenv(notices.NOTICES_ENV, str(path))
        notices._load.cache_clear()
        client = client or build_harness(tmp_path).client()
        assert client.get("/api/legal/notices").json() == {"available": False, "entries": []}, body
        assert client.get("/api/legal/notices/0").status_code == 404
