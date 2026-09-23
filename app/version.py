"""Which build this is: the version and the commit it was built from.

``packaging/build.ps1`` writes ``app/build_info.json`` just before PyInstaller runs,
and the spec bundles it, so a frozen build knows its own version and commit without
git. From source there is no such file (it is not committed): the version comes from
the installed package metadata and the commit is ``None``. A bug report that says
which build it came from is worth more than one that does not.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib import metadata

from app import paths


@dataclass(frozen=True)
class BuildInfo:
    version: str
    commit: str | None
    built: str | None
    frozen: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "commit": self.commit,
            "built": self.built,
            "frozen": self.frozen,
        }


def _package_version() -> str:
    try:
        return metadata.version("upshot")
    except metadata.PackageNotFoundError:
        return "0.0.0"


@lru_cache(maxsize=1)
def build_info() -> BuildInfo:
    stamped = paths.resource("app", "build_info.json")
    try:
        data = json.loads(stamped.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return BuildInfo(
        version=str(data.get("version") or _package_version()),
        commit=str(data["commit"]) if data.get("commit") else None,
        built=str(data["built"]) if data.get("built") else None,
        frozen=paths.is_frozen(),
    )
