"""``meta.json`` — a meeting's processing record, on disk beside its audio.

What the pipeline found and decided (state, timing, language detection, the echo model,
prompt versions, the rating...) is kept here as well as in the database. Names are not
(D89): the title, the calendar meeting, participants, speaker names and the description
live only in the database, which is backed up, so a rename or a reassignment is one update
there and no file says something the database no longer does.
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


#: What a person reads or edits about a meeting: the database's alone (D89). Also scrubbed
#: from a meta.json written before.
NAME_FIELDS = frozenset(
    {
        "title",
        "title_source",
        "description",
        "speaker_names",
        "calendar_json",
        "calendar_account_id",
        "evidence_json",
        "planned_start",
        "planned_end",
    }
)


def mirror(meeting: Meeting, **extra: Any) -> dict[str, Any]:
    payload = {k: v for k, v in read(meeting.path).items() if k not in NAME_FIELDS}
    payload.update({k: v for k, v in meeting.as_dict().items() if k not in NAME_FIELDS})
    payload.update({k: v for k, v in extra.items() if k not in NAME_FIELDS})
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
