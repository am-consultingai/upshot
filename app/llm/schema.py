"""``notes.json`` — what the summarizer writes, and the only shape still imposed.

The nine-field schema that used to live here is gone: it fixed the sections, and a
prompt asking for a differently shaped document could not have any visible effect.
What remains is the JSON envelope, kept only because it is what makes an answer
extractable across every provider — everything inside ``summary_html`` is the
prompt's to decide.
"""

from __future__ import annotations

from typing import Any

#: One item of the only list the application reads out of a summary. Everything here
#: is optional except the two fields that make it actionable, and nothing in it
#: constrains the document: the model writes ``summary_html`` however it likes and
#: then hands back the commitments it just wrote down.
ACTION_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["who", "what"],
    "properties": {
        "who": {"type": "string", "minLength": 1},
        "what": {"type": "string", "minLength": 1},
        # Nullable, because the normalised form written back into notes.json spells an
        # absent due date and an unknown timestamp as null rather than dropping the key
        # — and notes.json is validated against this same schema.
        "due": {"type": ["string", "null"]},
        "at_ms": {"type": ["integer", "null"], "minimum": 0},
    },
}

FREE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary_html"],
    "properties": {
        "summary_html": {"type": "string", "minLength": 1},
        "title": {"type": "string"},
        # Additive, and optional on purpose: a summary written before this existed
        # must keep opening, and a model that ignores the request must still produce
        # a usable document. See D47.
        "action_items": {"type": "array", "items": ACTION_ITEM_SCHEMA},
    },
}


def action_items(payload: Any) -> list[dict[str, Any]]:
    """The action items out of a validated envelope, defensively.

    A provider that half-honours the request — a string where an object belongs, a
    missing ``what`` — should cost its own item and nothing else. The summary is the
    product; this list is a bonus on top of it, and it must never be the reason a
    meeting has no notes.
    """
    if not isinstance(payload, dict):
        return []
    raw = payload.get("action_items")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        who = str(item.get("who") or "").strip()
        what = str(item.get("what") or "").strip()
        if not what:
            continue
        at_ms = item.get("at_ms")
        out.append(
            {
                "who": who or "?",
                "what": what,
                "due": str(item.get("due") or "").strip() or None,
                "at_ms": int(at_ms) if isinstance(at_ms, int | float) else None,
            }
        )
    return out


class ValidationError(ValueError):
    """The model's output does not satisfy the schema."""


def validate(payload: Any, schema: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate against the schema, raising :class:`ValidationError` on any failure."""
    import jsonschema

    try:
        jsonschema.validate(payload, schema or FREE_SCHEMA)
    except jsonschema.ValidationError as exc:
        raise ValidationError(
            f"{'/'.join(str(p) for p in exc.absolute_path)}: {exc.message}"
        ) from exc
    if not isinstance(payload, dict):
        raise ValidationError("notes must be a JSON object")
    return payload


def is_valid(payload: Any, schema: dict[str, Any] | None = None) -> bool:
    try:
        validate(payload, schema)
    except ValidationError:
        return False
    return True
