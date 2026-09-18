"""transcript.md → notes.json: window, map, reduce, then the sanity gates (§9)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app import glossary as glossary_module
from app import meta
from app.llm.client import LlmClient, LlmResult, make_client, system_blocks
from app.llm.prompts import load as load_prompt
from app.llm.schema import FREE_SCHEMA
from app.llm.tokens import CachingCounter, TokenCounter
from app.log import get
from app.pipeline.artifacts import up_to_date
from app.pipeline.context import StageContext
from app.pipeline.stages.assemble import transcript_paths

log = get(__name__)

NOTES_NAME = "notes.json"
LONG_MEETING_S = 20 * 60

#: How much transcript one call carries when ``llm.window_tokens`` is left null. 6000 was
#: once the only value, and for a hosted model it is a poor one: a 25 kB Hebrew transcript
#: became two windows plus a merge — three calls of 1.5 to 2 minutes each through Claude
#: Code, where one call of the same length would have done. The merge also costs quality
#: under the free-form prompt, which is asked to fuse two finished HTML documents.
WINDOW_TOKENS_BY_PROVIDER: dict[str, int] = {
    "anthropic": 150_000,
    "claude-subscription": 150_000,
    "gemini": 150_000,
    "openai": 100_000,
}
#: Local models and anything unrecognised: a small context is the safe assumption.
DEFAULT_WINDOW_TOKENS = 6000


def notes_path(folder: Path) -> Path:
    return Path(folder) / NOTES_NAME


@dataclass(frozen=True)
class Window:
    index: int
    start: int
    end: int
    text: str


def split_windows(
    text: str,
    counter: TokenCounter,
    *,
    target_tokens: int = 6000,
    overlap_tokens: int = 300,
) -> list[Window]:
    """Window by **real tokens**, found by binary search over character boundaries."""
    if not text:
        return []
    if counter.count(text) <= target_tokens:
        return [Window(0, 0, len(text), text)]
    windows: list[Window] = []
    start = 0
    while start < len(text):
        end = _boundary(text, start, counter, target_tokens)
        windows.append(Window(len(windows), start, end, text[start:end]))
        if end >= len(text):
            break
        overlap_chars = _overlap_chars(text, end, counter, overlap_tokens)
        next_start = end - overlap_chars
        start = next_start if next_start > start else end
    return windows


def _boundary(text: str, start: int, counter: TokenCounter, target: int) -> int:
    low, high = start + 1, len(text)
    best = start + 1
    while low <= high:
        mid = (low + high) // 2
        if counter.count(text[start:mid]) <= target:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    return min(best, len(text))


def _overlap_chars(text: str, end: int, counter: TokenCounter, overlap_tokens: int) -> int:
    if overlap_tokens <= 0:
        return 0
    low, high = 0, end
    best = 0
    while low <= high:
        mid = (low + high) // 2
        if counter.count(text[end - mid : end]) <= overlap_tokens:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    return best


def window_tokens(ctx: StageContext, client: LlmClient) -> int:
    """An explicit ``llm.window_tokens`` wins; null sizes the window to the provider."""
    configured = ctx.config.get("llm.window_tokens")
    if configured is not None:
        return int(configured)
    return WINDOW_TOKENS_BY_PROVIDER.get(client.name, DEFAULT_WINDOW_TOKENS)


def glossary_block(ctx: StageContext) -> str | None:
    entries = glossary_module.merge(
        glossary_module.from_db(ctx.dao),
        glossary_module.load_yaml(ctx.config.glossary_path),
    )
    if not entries:
        return None
    lines = ["Glossary — keep these spellings verbatim:"]
    lines.extend(f"- {entry.as_prompt_fragment()}" for entry in entries)
    return "\n".join(lines)


def resolve_summary_language(ctx: StageContext) -> str:
    configured = ctx.config.summary_language
    if configured != "auto":
        return configured
    return ctx.meeting.language or ctx.config.default_language


def language_instruction(language: str) -> str:
    name = {"he": "Hebrew", "en": "English"}.get(language, language)
    return (
        f"Write the notes in {name} ({language}). Keep proper nouns, product names and "
        "technical terms verbatim in their original language."
    )


def client_for(ctx: StageContext) -> LlmClient:
    services = ctx.services
    client = getattr(services, "llm", None) if services is not None else None
    if client is not None:
        return client  # type: ignore[no-any-return]
    return make_client(ctx.config, sensitive=bool(ctx.meeting.sensitive))


def system_text(ctx: StageContext) -> tuple[str, str]:
    """The summarizing instructions, and the version to record against them.

    An edited prompt replaces the shipped one rather than appending to it. Appending
    would mean the box in Settings shows something other than what is sent, and a prompt
    you cannot fully see is one you cannot debug.
    """
    shipped = load_prompt("system")
    custom = ctx.config.get("llm.summary_prompt")
    if isinstance(custom, str) and custom.strip():
        text = custom.strip()
        # Hashed, not just "custom": every edit has to be a different version, or a
        # second edit looks identical to the first and the notes are never rebuilt.
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
        return text, f"custom:{digest}"
    return shipped.text, shipped.version


def summarize(ctx: StageContext, transcript: str, *, system: str, system_version: str) -> None:
    """Free-form: the prompt writes the summary, and nothing here rewrites it.

    Structured mode exists because the rest of the application reads the parts — the
    title indexes the meeting, action-item owners drive the review gates, the template
    lays the sections out. All of that is a constraint the prompt cannot see past, so a
    prompt asking for a different shape of document could not have any visible effect.

    Here the only thing still imposed is the JSON envelope, and only because it is what
    makes an answer extractable at all across providers. What is inside ``summary_html``
    is entirely the prompt's: sections, order, wording, layout.
    """
    client = client_for(ctx)
    counter = CachingCounter(client.count_tokens)
    language = resolve_summary_language(ctx)
    glossary = glossary_block(ctx)
    windows = split_windows(
        transcript,
        counter,
        target_tokens=window_tokens(ctx, client),
        overlap_tokens=int(ctx.config.get("llm.window_overlap_tokens", 300)),
    )
    log.info("summarizing %d window(s) in %s (free-form)", len(windows), language)

    envelope = (
        "Reply with a JSON object holding `summary_html` — the complete summary as HTML — "
        "and optionally `title`. Everything about that HTML is yours to decide."
    )
    instruction = language_instruction(language)
    parts: list[str] = []
    usage: list[dict[str, Any]] = []
    for window in windows:
        ctx.checkpoint()
        result = client.complete_json(
            system_blocks=system_blocks(f"{system}\n\n{instruction}\n\n{envelope}", glossary),
            user=window.text,
            schema=FREE_SCHEMA,
            max_tokens=int(ctx.config.get("llm.max_tokens", 16000)),
        )
        usage.append(result.usage)
        parts.append(str(result.data.get("summary_html", "")))
        log.info("window %d/%d done", window.index + 1, len(windows))

    if len(parts) > 1:
        ctx.checkpoint()
        merged = client.complete_json(
            system_blocks=system_blocks(f"{system}\n\n{instruction}\n\n{envelope}", glossary),
            user="Merge these partial summaries of one meeting into a single document:\n\n"
            + "\n\n---\n\n".join(parts),
            schema=FREE_SCHEMA,
            max_tokens=int(ctx.config.get("llm.max_tokens", 16000)),
        )
        usage.append(merged.usage)
        notes = {
            "summary_html": str(merged.data.get("summary_html", "")),
            "title": str(merged.data.get("title", "") or ctx.meeting.title or ""),
        }
    else:
        notes = {"summary_html": parts[0], "title": str(ctx.meeting.title or "")}

    notes_path(ctx.folder).write_text(
        json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ctx.dao.update_meeting(ctx.meeting.id, summary_language=language)
    # No sanity gates: they read action_items and owners, which free-form has no notion
    # of. Nothing here is in a position to judge what the prompt asked for.
    meta.mirror(
        ctx.refresh(),
        prompt_versions={"system": system_version, "format": "free"},
        summary_language=language,
        windows=len(windows),
        llm={"name": client.name, "usage": _sum_usage(usage)},
    )
    log.info("wrote free-form notes (%d chars)", len(notes["summary_html"]))


def run(ctx: StageContext) -> None:
    folder = ctx.folder
    _, transcript_md = transcript_paths(folder)
    output = notes_path(folder)
    system, system_version = system_text(ctx)
    # The prompt is an input to the notes, so it belongs in the staleness test. Comparing
    # against the transcript alone meant editing the prompt and pressing Summarize again
    # did nothing at all: the transcript had not moved, so the stage skipped and the old
    # summary stood.
    recorded = str(meta.read(folder).get("prompt_versions", {}).get("system", ""))
    if ctx.force:
        # Asked for by hand: no staleness test, no comparison against what is on disk.
        # Pressing Summarize is a decision, and second-guessing it is how an edited
        # prompt came to have no visible effect at all.
        log.info("re-summarizing on request")
    elif up_to_date(output, [transcript_md]) and recorded == system_version:
        log.info("notes.json is current; skipping")
        return
    elif recorded and recorded != system_version:
        log.info("prompt changed (%s -> %s); re-summarizing", recorded, system_version)
    if not transcript_md.exists():
        raise FileNotFoundError(f"{transcript_md} is missing — run the assemble stage first")

    transcript = transcript_md.read_text(encoding="utf-8")
    summarize(ctx, transcript, system=system, system_version=system_version)


def _sum_usage(usage: Sequence[dict[str, Any]]) -> dict[str, int]:
    total: dict[str, int] = {}
    for entry in usage:
        for key, value in entry.items():
            if isinstance(value, int):
                total[key] = total.get(key, 0) + value
    return total


def load_notes(folder: Path) -> dict[str, Any]:
    payload = json.loads(notes_path(folder).read_text(encoding="utf-8"))
    return dict(payload)


def result_is_valid(result: LlmResult) -> bool:
    from app.llm.schema import is_valid

    return is_valid(result.data)
