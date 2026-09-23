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


def test_a_broken_stamp_is_not_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "build_info.json").write_text("[1, 2", encoding="utf-8")
    monkeypatch.setattr(paths, "resource", lambda *parts: tmp_path.joinpath(*parts))
    assert version.build_info().commit is None
