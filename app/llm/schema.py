"""``notes.json`` — what the summarizer writes, and the only shape still imposed.

The nine-field schema that used to live here is gone: it fixed the sections, and a
prompt asking for a differently shaped document could not have any visible effect.
What remains is the JSON envelope, kept only because it is what makes an answer
extractable across every provider — everything inside ``summary_html`` is the
prompt's to decide.
"""

from __future__ import annotations

from typing import Any

FREE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary_html"],
    "properties": {
        "summary_html": {"type": "string", "minLength": 1},
        "title": {"type": "string"},
    },
}


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
