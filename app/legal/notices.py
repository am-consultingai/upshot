"""The third-party notices the app shows: every component it ships, with its licence.

``scripts/third_party_notices.py`` writes ``THIRD-PARTY-NOTICES.json`` at build time and the
spec bundles it in ``_internal`` (``paths.resource``); ``vendor/`` holds the same file in a
source checkout that has built it. The list (about 25 KB) is served whole, and each entry's
licence texts (1.4 MB in all) one entry at a time, when the page opens it.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from app import paths

#: Names another file to serve, in a source checkout only (the e2e server's fixture, a
#: developer's own build); the installed app always shows the notices it was built with.
NOTICES_ENV = "UP_NOTICES"
NOTICES_FILE = "THIRD-PARTY-NOTICES.json"


def notices_path() -> Path | None:
    configured = None if paths.is_frozen() else os.environ.get(NOTICES_ENV)
    for candidate in (
        Path(configured) if configured else None,
        paths.resource(NOTICES_FILE),
        paths.resource("vendor", NOTICES_FILE),
    ):
        if candidate is not None and candidate.is_file():
            return candidate
    return None


@lru_cache(maxsize=1)  # one file, re-read only when it changes
def _load(path: Path, stamp: float) -> list[dict[str, Any]] | None:
    """The file's entries; None when it cannot be read as a notices file."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        items = document["entries"]
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if not isinstance(items, list):
        return None
    return [entry for entry in items if isinstance(entry, dict)]


def _entries() -> list[dict[str, Any]] | None:
    path = notices_path()
    try:
        return _load(path, path.stat().st_mtime) if path is not None else None
    except OSError:
        return None


def entries() -> list[dict[str, Any]]:
    return _entries() or []


def summary() -> dict[str, Any]:
    """Every entry without its texts, numbered for :func:`texts`."""
    items = _entries()
    return {
        "available": items is not None,
        "entries": [
            {
                "id": index,
                "name": str(entry.get("name", "")),
                "version": str(entry.get("version", "")),
                "licence": str(entry.get("licence", "")),
                "kind": str(entry.get("kind", "other")),
                "note": str(entry.get("note", "")),
                "source": str(entry.get("source", "")),
            }
            for index, entry in enumerate(items or [])
        ],
    }


def texts(index: int) -> list[str]:
    """Entry ``index``'s licence and notice texts; IndexError when there is none."""
    items = entries()
    if not 0 <= index < len(items):
        raise IndexError(index)
    found = items[index].get("texts", [])
    return [str(text) for text in found] if isinstance(found, list) else [str(found)]
