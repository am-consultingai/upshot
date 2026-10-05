"""app/version.py: a frozen build knows its version and commit from build_info.json."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import paths, version


@pytest.fixture(autouse=True)
def fresh() -> None:
    version.build_info.cache_clear()


def test_from_source_the_package_version_and_no_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(paths, "resource", lambda *parts: tmp_path.joinpath(*parts))
    info = version.build_info()
    assert info.version == version._package_version()
    assert info.commit is None
    assert info.as_dict()["frozen"] is False


def test_the_stamp_build_ps1_writes_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "app").mkdir()
    # PowerShell may write a BOM; the stamp must still read.
    stamp = {"version": "0.1.0", "commit": "6b916986d092", "built": "2026-09-23T21:30:00Z"}
    (tmp_path / "app" / "build_info.json").write_text(json.dumps(stamp), encoding="utf-8-sig")
    monkeypatch.setattr(paths, "resource", lambda *parts: tmp_path.joinpath(*parts))
    info = version.build_info()
    assert (info.version, info.commit, info.built) == (
        "0.1.0",
        "6b916986d092",
        "2026-09-23T21:30:00Z",
    )


DSN = "https://0123456789abcdef0123456789abcdef@o1.ingest.example.io/42"


def test_the_official_build_reads_its_dsns_and_never_shows_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "app").mkdir()
    stamp = {"version": "0.2.0", "sentry": {"dsn": DSN, "frontend_dsn": DSN.replace("/42", "/43")}}
    (tmp_path / "app" / "build_info.json").write_text(json.dumps(stamp), encoding="utf-8-sig")
    monkeypatch.setattr(paths, "resource", lambda *parts: tmp_path.joinpath(*parts))
    info = version.build_info()
    assert info.sentry_dsn == DSN
    assert info.sentry_frontend_dsn == DSN.replace("/42", "/43")
    shown = info.as_dict()
    assert shown["reports"] is True
    assert DSN not in json.dumps(shown)


@pytest.mark.parametrize(
    "sentry",
    [None, {}, {"dsn": ""}, {"dsn": "not a dsn"}, {"dsn": DSN + "/extra"}, {"dsn": 42}, [DSN]],
)
def test_a_build_without_a_valid_dsn_reports_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sentry: object
) -> None:
    (tmp_path / "app").mkdir()
    stamp = {"version": "0.2.0", "sentry": sentry}
    (tmp_path / "app" / "build_info.json").write_text(json.dumps(stamp), encoding="utf-8")
    monkeypatch.setattr(paths, "resource", lambda *parts: tmp_path.joinpath(*parts))
    info = version.build_info()
    assert info.sentry_dsn is None
    assert info.as_dict()["reports"] is False


def test_from_source_there_is_no_dsn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "resource", lambda *parts: tmp_path.joinpath(*parts))
    assert version.build_info().sentry_dsn is None


def test_a_broken_stamp_is_not_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "build_info.json").write_text("[1, 2", encoding="utf-8")
    monkeypatch.setattr(paths, "resource", lambda *parts: tmp_path.joinpath(*parts))
    assert version.build_info().commit is None
