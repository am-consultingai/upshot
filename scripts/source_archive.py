"""Build the source archive the copyleft components' licences ask for (licence epic, S6).

    uv run python scripts/source_archive.py <Upshot-X.Y.Z-sources.zip>

Run by ``packaging/build.ps1`` after the installer; ``scripts/publish_release.py`` attaches
the archive to the GitHub release beside it, so the source sits in the same place as the
binaries (LGPL-2.1 §6, LGPL-3.0 §4, MPL-2.0 §3.2).

What goes in is read from the build's own ``vendor/THIRD-PARTY-NOTICES.json``: every entry
under an LGPL or MPL licence (``third_party_notices.SOURCE_OFFERED``), at the exact version
that was frozen.

- **A Python package:** its source distribution as ``uv.lock`` pins it (URL and SHA-256).
  Where PyPI has no sdist (pystray), the pinned ``none-any`` wheel stands in: a pure-Python
  wheel is the source.
- **FFmpeg:** ``ffmpeg-sources.zip`` from the release the binary came from, checked against
  that release's ``SHA256SUMS`` (which ``build.ps1`` pins and checks).
- **Eigen** (inside Upshot's sherpa-onnx): ``sherpa-onnx-sources.zip`` from the release the
  wheel came from, which holds Eigen as the build downloaded it, pinned by SHA-256
  (``third_party_notices.SHERPA_SOURCES_SHA256``).
- **Eigen inside onnxruntime:** the Eigen archive onnxruntime builds with and the two patches
  it applies, each pinned by SHA-256 (``third_party_notices.ONNXRUNTIME_EIGEN_FILES``).

An entry with no way to its source stops the build. ``README.txt`` lists each file with its
version, licence, origin and SHA-256.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import re
import sys
import tomllib
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import third_party_notices as notices  # noqa: E402

VENDOR = ROOT / "vendor"
LOCK = ROOT / "uv.lock"
RELEASES = "https://github.com/am-consultingai/upshot/releases/download"


class SourceError(RuntimeError):
    """A component whose licence asks for its source, and no way to it."""


@dataclass(frozen=True)
class Source:
    name: str
    version: str
    licence: str
    url: str
    sha256: str
    file: str  # the path inside the archive


def needs_source(licence: str) -> bool:
    # Any LGPL or MPL in the expression, a dual licence ("MIT OR MPL-2.0") included: shipping
    # the source of one that could be taken as MIT costs nothing.
    return any(family in licence for family in notices.SOURCE_OFFERED)


def _normal(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def python_source(name: str, version: str, licence: str, lock: dict) -> Source:  # type: ignore[type-arg]
    for package in lock.get("package", []):
        if _normal(package["name"]) != _normal(name):
            continue
        if package["version"] != version:
            raise SourceError(
                f"{name}: the build froze {version}, uv.lock pins {package['version']}"
            )
        candidates = [package["sdist"]] if "sdist" in package else []
        candidates += [w for w in package.get("wheels", []) if w["url"].endswith("-none-any.whl")]
        if not candidates:
            raise SourceError(f"{name} {version}: uv.lock has no sdist and no pure-Python wheel")
        pick = candidates[0]
        filename = pick["url"].rsplit("/", 1)[-1]
        return Source(
            name,
            version,
            licence,
            pick["url"],
            pick["hash"].removeprefix("sha256:"),
            f"python/{filename}",
        )
    raise SourceError(f"{name} {version}: not in uv.lock")


def ffmpeg_source(licence: str, vendor: Path) -> Source:
    marker = vendor / "ffmpeg.source.txt"
    sums = vendor / "release-ffmpeg.SHA256SUMS"
    if not marker.is_file() or not sums.is_file():
        raise SourceError(
            "FFmpeg: no release marker or SHA256SUMS in vendor/ (run build.ps1's ffmpeg step)"
        )
    words = marker.read_text(encoding="utf-8").split()
    if not words:
        raise SourceError("FFmpeg: vendor/ffmpeg.source.txt is empty (run build.ps1's ffmpeg step)")
    tag = words[0]
    hashes = {}
    for line in sums.read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, filename = line.split(maxsplit=1)
            hashes[filename.strip()] = digest
    if "ffmpeg-sources.zip" not in hashes:
        raise SourceError(f"FFmpeg: release {tag} lists no ffmpeg-sources.zip")
    return Source(
        notices.FFMPEG,
        tag,
        licence,
        f"{RELEASES}/{tag}/ffmpeg-sources.zip",
        hashes["ffmpeg-sources.zip"],
        f"ffmpeg/{tag}-sources.zip",
    )


def sherpa_source(name: str, version: str, licence: str) -> Source:
    tag = notices.SHERPA_RELEASE
    return Source(
        name,
        version,
        licence,
        f"{RELEASES}/{tag}/sherpa-onnx-sources.zip",
        notices.SHERPA_SOURCES_SHA256,
        f"sherpa-onnx/{tag}-sources.zip",
    )


def onnxruntime_eigen_sources(name: str, version: str, licence: str) -> list[Source]:
    """Eigen as onnxruntime downloads it, and the patches it applies: modified MPL files, so
    the changes are offered with them."""
    return [
        Source(name, version, licence, url, sha256, file)
        for url, sha256, file in notices.ONNXRUNTIME_EIGEN_FILES
    ]


def plan(entries: list[dict], lock: dict, vendor: Path) -> list[Source]:  # type: ignore[type-arg]
    """The sources of each entry whose licence asks for them; SourceError for one without a
    way."""
    sources: list[Source] = []
    for entry in entries:
        if not needs_source(entry["licence"]):
            continue
        if entry["name"] == notices.FFMPEG:
            sources.append(ffmpeg_source(entry["licence"], vendor))
        elif entry["name"] == notices.EIGEN:
            sources.append(sherpa_source(entry["name"], entry["version"], entry["licence"]))
        elif entry["name"] == notices.EIGEN_IN_ONNXRUNTIME:
            sources += onnxruntime_eigen_sources(entry["name"], entry["version"], entry["licence"])
        elif entry.get("kind") == "python":
            sources.append(python_source(entry["name"], entry["version"], entry["licence"], lock))
        else:
            raise SourceError(f"{entry['name']} ({entry['licence']}): no recipe for its source")
    if not sources:  # every real build ships FFmpeg: an empty list is the wrong file
        raise SourceError("no LGPL or MPL component in the notices: not a build's notices file?")
    return sources


def fetch(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=120) as response:
            return response.read()  # type: ignore[no-any-return]
    # URLError, HTTPError and timeouts are OSErrors; a cut connection is an IncompleteRead.
    except (OSError, http.client.HTTPException) as exc:
        raise SourceError(f"{url}: {exc}") from exc


def readme(sources: list[Source]) -> str:
    lines = [
        "Source code of the open-source components in Upshot whose licences ask for it",
        "(LGPL and MPL), at the exact versions this release ships. The licence texts are in",
        "THIRD-PARTY-NOTICES.txt, installed with Upshot and shown in the app under",
        "Settings > About and updates > Open-source licences.",
        "",
        "AM Consulting will also provide this source on request at office@amconsultingai.com,",
        "for at least three years after this release.",
        "",
    ]
    for s in sources:
        lines += [
            f"{s.file}",
            f"  {s.name} {s.version}, {s.licence}",
            f"  from {s.url}",
            f"  sha256 {s.sha256}",
            "",
        ]
    return "\n".join(lines)


def build(out: Path, sources: list[Source], get=fetch) -> None:  # type: ignore[no-untyped-def]
    partial = out.with_name(out.name + ".partial")
    try:
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_STORED) as archive:
            for source in sources:
                data = get(source.url)
                if hashlib.sha256(data).hexdigest() != source.sha256:
                    raise SourceError(f"{source.url}: SHA-256 is not the pinned {source.sha256}")
                archive.writestr(source.file, data)
            archive.writestr("README.txt", readme(sources))
        partial.replace(out)
    except BaseException:
        partial.unlink(missing_ok=True)  # nothing half-written is left beside the installer
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--notices", type=Path, default=VENDOR / "THIRD-PARTY-NOTICES.json")
    args = parser.parse_args(argv)
    try:
        try:
            entries = json.loads(args.notices.read_text(encoding="utf-8"))["entries"]
            lock = tomllib.loads(LOCK.read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError) as exc:
            raise SourceError(f"cannot read {args.notices.name} or uv.lock: {exc}") from exc
        try:
            sources = plan(entries, lock, args.notices.parent)
        except (KeyError, TypeError, ValueError) as exc:  # a malformed entry or SHA256SUMS
            raise SourceError(f"malformed notices or FFmpeg SHA256SUMS: {exc!r}") from exc
        try:
            build(args.out, sources)
        except OSError as exc:  # the output folder, a locked file
            raise SourceError(f"cannot write {args.out}: {exc}") from exc
    except SourceError as exc:
        print(f"source archive: {exc}", file=sys.stderr)
        return 1
    for source in sources:
        print(f"  {source.file}  ({source.name} {source.version}, {source.licence})")
    print(f"{args.out}: {len(sources)} sources")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
