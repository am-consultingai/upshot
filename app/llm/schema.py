"""``notes.json`` — the contract between the model and everything downstream (§9.3)."""

from __future__ import annotations

from typing import Any

NOTES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "tldr", "topics", "decisions", "action_items"],
    "properties": {
        "title": {"type": "string", "maxLength": 120},
        "tldr": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 6},
        "participants": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "role": {"type": "string"},
                    "track": {"enum": ["ME", "THEM"]},
                },
            },
        },
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["heading", "points"],
                "properties": {
                    "heading": {"type": "string"},
                    "points": {"type": "array", "items": {"type": "string"}},
                    "quotes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "who": {"type": "string"},
                                "text": {"type": "string"},
                                "at_ms": {"type": "integer"},
                            },
                        },
                    },
                },
            },
        },
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "what": {"type": "string"},
                    "rationale": {"type": "string"},
                    "who_decided": {"type": "string"},
                    "at_ms": {"type": "integer"},
                },
            },
        },
        "action_items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "who": {"type": "string"},
                    "what": {"type": "string"},
                    "due": {"type": ["string", "null"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
        },
        "open_questions": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "follow_up_email": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"subject": {"type": "string"}, "body_md": {"type": "string"}},
        },
    },
}

#: The reduced schema each map window fills in.
MAP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["topics", "decisions", "action_items"],
    "properties": {
        "topics": NOTES_SCHEMA["properties"]["topics"],
        "decisions": NOTES_SCHEMA["properties"]["decisions"],
        "action_items": NOTES_SCHEMA["properties"]["action_items"],
        "quotes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "who": {"type": "string"},
                    "text": {"type": "string"},
                    "at_ms": {"type": "integer"},
                },
            },
        },
    },
}


class ValidationError(ValueError):
    """The model's output does not satisfy the schema."""


def validate(payload: Any, schema: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate against the schema, raising :class:`ValidationError` on any failure."""
    import jsonschema

    try:
        jsonschema.validate(payload, schema or NOTES_SCHEMA)
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
