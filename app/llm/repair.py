"""Ask for JSON, validate it, feed the error back.

Only the Anthropic Messages API enforces our schema server-side. Every other provider —
the Claude Code CLI, Ollama, OpenAI's json_object mode, Gemini — returns text that is
*probably* JSON, so they all share this loop rather than each inventing one.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from app.errors import PermanentError
from app.llm.schema import ValidationError, validate
from app.log import get

log = get(__name__)

FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def strip_fence(text: str) -> str:
    """Models like to wrap JSON in a markdown fence even when told not to."""
    match = FENCE.match(text)
    return match.group(1) if match else text.strip()


def extract_json(text: str) -> Any:
    """Parse the payload, tolerating a fence or a sentence either side of it."""
    candidate = strip_fence(text)
    try:
        return json.loads(candidate)
    except ValueError:
        pass
    start, end = candidate.find("{"), candidate.rfind("}")
    if start >= 0 and end > start:
        return json.loads(candidate[start : end + 1])
    raise ValueError("no JSON object in the response")


def schema_instruction(schema: dict[str, Any]) -> str:
    """The schema, as a prompt — for providers that cannot enforce it themselves."""
    return (
        "Reply with a single JSON object and nothing else: no prose, no markdown fence.\n"
        "It must satisfy this JSON Schema exactly:\n"
        f"{json.dumps(schema, ensure_ascii=False)}"
    )


def complete_with_repair(
    send: Callable[[str], str],
    prompt: str,
    schema: dict[str, Any],
    *,
    max_repairs: int = 2,
    provider: str = "llm",
) -> tuple[dict[str, Any], int]:
    """Call ``send`` until it returns schema-valid JSON. Returns (payload, attempts)."""
    message = prompt
    last_error = ""
    for attempt in range(max_repairs + 1):
        raw = send(message)
        try:
            data = extract_json(raw)
            validate(data, schema)
        except (ValueError, ValidationError) as exc:
            last_error = str(exc)
            log.warning("%s returned invalid output (attempt %d): %s", provider, attempt + 1, exc)
            message = (
                f"{prompt}\n\nYour previous answer was rejected: {last_error}\n"
                "Return only JSON that satisfies the schema."
            )
            continue
        if not isinstance(data, dict):
            last_error = "the response was not a JSON object"
            continue
        return data, attempt + 1
    raise PermanentError(
        f"{provider} could not produce schema-valid JSON after {max_repairs + 1} attempts: "
        f"{last_error}",
        category="schema",
    )
