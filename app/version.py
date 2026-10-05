"""Which build this is: the version, the commit it was built from, and where it reports.

``packaging/build.ps1`` writes ``app/build_info.json`` just before PyInstaller runs,
and the spec bundles it, so a frozen build knows its own version and commit without
git. From source there is no such file (it is not committed): the version comes from
the installed package metadata and the commit is ``None``. A bug report that says
which build it came from is worth more than one that does not.

The official build also carries its Sentry DSNs there (D87), copied by ``build.ps1`` from
the gitignored ``packaging/release.local.json``. A DSN only lets a program *send* events,
so shipping it is expected; keeping it out of the repository means a build from source
has none and sends nothing. ``as_dict`` says whether reporting is possible, never the DSN.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import metadata
from typing import Any

from app import paths

#: ``https://<public key>@<host>/<project id>``: anything else is treated as no DSN.
_DSN = re.compile(r"^https://[0-9a-f]{32}@[A-Za-z0-9.-]+/\d+$")


@dataclass(frozen=True)
class BuildInfo:
    version: str
    commit: str | None
    built: str | None
    frozen: bool
    #: The Python side's DSN (crash reports and feedback), or None: nothing is sent.
    sentry_dsn: str | None = None
    #: The front end's DSN, which its SDK sends through the local backend (D87).
    sentry_frontend_dsn: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "commit": self.commit,
            "built": self.built,
            "frozen": self.frozen,
            "reports": self.sentry_dsn is not None,
        }


def _package_version() -> str:
    try:
        return metadata.version("upshot")
    except metadata.PackageNotFoundError:
        return "0.0.0"


def _dsn(value: Any) -> str | None:
    return value if isinstance(value, str) and _DSN.match(value) else None


@lru_cache(maxsize=1)
def build_info() -> BuildInfo:
    stamped = paths.resource("app", "build_info.json")
    try:
        data = json.loads(stamped.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    sentry = data.get("sentry")
    if not isinstance(sentry, dict):
        sentry = {}
    return BuildInfo(
        version=str(data.get("version") or _package_version()),
        commit=str(data["commit"]) if data.get("commit") else None,
        built=str(data["built"]) if data.get("built") else None,
        frozen=paths.is_frozen(),
        sentry_dsn=_dsn(sentry.get("dsn")),
        sentry_frontend_dsn=_dsn(sentry.get("frontend_dsn")),
    )
