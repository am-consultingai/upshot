"""scripts/source_archive.py: the LGPL and MPL components' source, beside the installer (S6)."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "source_archive", ROOT / "scripts" / "source_archive.py"
)
assert _spec and _spec.loader
archive = importlib.util.module_from_spec(_spec)
sys.modules["source_archive"] = archive
_spec.loader.exec_module(archive)

SDIST = b"soxr sdist bytes"
WHEEL = b"pystray wheel bytes"
FFMPEG_SRC = b"ffmpeg sources zip"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


LOCK = {
    "package": [
        {
            "name": "soxr",
            "version": "1.1.0",
            "sdist": {
                "url": "https://files.example/soxr-1.1.0.tar.gz",
                "hash": f"sha256:{sha(SDIST)}",
            },
            "wheels": [
                {
                    "url": "https://files.example/soxr-1.1.0-cp313-cp313-win_amd64.whl",
                    "hash": "sha256:00",
                }
            ],
        },
        {
            "name": "pystray",
            "version": "0.19.5",
            "wheels": [
                {
                    "url": "https://files.example/pystray-0.19.5-py2.py3-none-any.whl",
                    "hash": f"sha256:{sha(WHEEL)}",
                }
            ],
        },
        {
            "name": "numpy",
            "version": "2.5.2",
            "wheels": [
                {
                    "url": "https://files.example/numpy-2.5.2-cp313-cp313-win_amd64.whl",
                    "hash": "sha256:00",
                }
            ],
        },
    ]
}


def entry(name: str, version: str, licence: str, kind: str = "python") -> dict:  # type: ignore[type-arg]
    return {"name": name, "version": version, "licence": licence, "kind": kind}


@pytest.fixture
def vendor(tmp_path: Path) -> Path:
    folder = tmp_path / "vendor"
    folder.mkdir()
    (folder / "ffmpeg.source.txt").write_text("ffmpeg-8.1.3-audio-1 61a9", encoding="utf-8")
    (folder / "release-ffmpeg.SHA256SUMS").write_text(
        f"{sha(b'exe')}  ffmpeg.exe\n{sha(FFMPEG_SRC)}  ffmpeg-sources.zip\n", encoding="utf-8"
    )
    return folder


def test_only_lgpl_and_mpl_entries_are_sourced_each_from_its_pinned_file(vendor: Path) -> None:
    entries = [
        entry("soxr", "1.1.0", "LGPL-2.1-or-later"),
        entry("pystray", "0.19.5", "LGPL-3.0-or-later"),
        entry("numpy", "2.5.2", "BSD-3-Clause"),
        entry(archive.notices.FFMPEG, "", "LGPL-2.1-or-later", kind="other"),
        entry("Frank Ruhl Libre and IBM Plex Sans Hebrew fonts", "", "OFL-1.1", kind="other"),
    ]
    sources = archive.plan(entries, LOCK, vendor)
    assert [(s.name, s.file) for s in sources] == [
        ("soxr", "python/soxr-1.1.0.tar.gz"),  # the sdist, not the compiled wheel
        ("pystray", "python/pystray-0.19.5-py2.py3-none-any.whl"),  # no sdist: the pure wheel
        (archive.notices.FFMPEG, "ffmpeg/ffmpeg-8.1.3-audio-1-sources.zip"),
    ]
    assert sources[2].url.endswith("/ffmpeg-8.1.3-audio-1/ffmpeg-sources.zip")
    assert sources[2].sha256 == sha(FFMPEG_SRC)


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        (
            [entry("soxr", "1.0.0", "LGPL-2.1-or-later")],
            "the build froze 1.0.0, uv.lock pins 1.1.0",
        ),
        ([entry("numpy", "2.5.2", "MPL-2.0")], "no sdist and no pure-Python wheel"),
        ([entry("left-pad", "1.0", "MPL-2.0")], "not in uv.lock"),
        ([entry("some-js", "1.0", "LGPL-3.0-only", kind="javascript")], "no recipe"),
    ],
)
def test_a_component_without_a_way_to_its_source_stops_the_build(
    vendor: Path,
    entries: list,
    message: str,  # type: ignore[type-arg]
) -> None:
    with pytest.raises(archive.SourceError, match=message):
        archive.plan(entries, LOCK, vendor)


def test_ffmpeg_needs_the_release_marker(tmp_path: Path) -> None:
    with pytest.raises(archive.SourceError, match="no release marker"):
        archive.plan(
            [entry(archive.notices.FFMPEG, "", "LGPL-2.1-or-later", "other")], LOCK, tmp_path
        )


def test_the_archive_holds_each_verified_file_and_a_readme(tmp_path: Path, vendor: Path) -> None:
    sources = archive.plan(
        [
            entry("soxr", "1.1.0", "LGPL-2.1-or-later"),
            entry(archive.notices.FFMPEG, "", "LGPL-2.1-or-later", "other"),
        ],
        LOCK,
        vendor,
    )
    served = {sources[0].url: SDIST, sources[1].url: FFMPEG_SRC}
    out = tmp_path / "Upshot-0.3.0-sources.zip"
    archive.build(out, sources, get=served.__getitem__)
    with zipfile.ZipFile(out) as built:
        assert built.read("python/soxr-1.1.0.tar.gz") == SDIST
        assert built.read("ffmpeg/ffmpeg-8.1.3-audio-1-sources.zip") == FFMPEG_SRC
        readme = built.read("README.txt").decode()
    assert "soxr 1.1.0, LGPL-2.1-or-later" in readme and sha(SDIST) in readme


def test_a_download_that_does_not_match_its_hash_leaves_no_archive(
    tmp_path: Path, vendor: Path
) -> None:
    sources = archive.plan([entry("soxr", "1.1.0", "LGPL-2.1-or-later")], LOCK, vendor)
    out = tmp_path / "Upshot-0.3.0-sources.zip"
    with pytest.raises(archive.SourceError, match="SHA-256"):
        archive.build(out, sources, get=lambda url: b"tampered")
    assert not out.exists()
