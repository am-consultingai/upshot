"""``meta.json`` — the meeting record mirrored to disk.

The disk layout is recoverable on its own (DESIGN.md §9): everything the database knows
about a meeting is also in its folder.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.db.dao import Meeting

META_NAME = "meta.json"


def path_for(folder: Path) -> Path:
    return Path(folder) / META_NAME


def read(folder: Path) -> dict[str, Any]:
    path = path_for(folder)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def write(folder: Path, payload: dict[str, Any]) -> Path:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    target = path_for(folder)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    return target


def update(folder: Path, **fields: Any) -> dict[str, Any]:
    payload = read(folder)
    payload.update(fields)
    write(folder, payload)
    return payload


def mirror(meeting: Meeting, **extra: Any) -> dict[str, Any]:
    payload = read(meeting.path)
    payload.update(meeting.as_dict())
    payload.update(extra)
    write(meeting.path, payload)
    return payload


def add_review_reason(folder: Path, reason: str) -> list[str]:
    payload = read(folder)
    reasons = []
    if reason not in reasons:
        reasons.append(reason)
    payload["review_reasons"] = reasons
    write(folder, payload)
    return reasons


def review_reasons(folder: Path) -> list[str]:
    return list(read(folder).get("review_reasons", []))
