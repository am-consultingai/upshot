"""transcript.md → notes.json: window, map, reduce, then the sanity gates (§9)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app import glossary as glossary_module
from app import meta
from app.llm.client import LlmClient, LlmResult, make_client, system_blocks
from app.llm.prompts import load as load_prompt
from app.llm.prompts import versions as prompt_versions
from app.llm.schema import MAP_SCHEMA, NOTES_SCHEMA, validate
from app.llm.tokens import CachingCounter, TokenCounter
from app.log import get
from app.pipeline.artifacts import up_to_date
from app.pipeline.context import StageContext
from app.pipeline.stages.assemble import transcript_paths

log = get(__name__)

NOTES_NAME = "notes.json"
LONG_MEETING_S = 20 * 60


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


def run(ctx: StageContext) -> None:
    folder = ctx.folder
    _, transcript_md = transcript_paths(folder)
    output = notes_path(folder)
    if up_to_date(output, [transcript_md]):
        log.info("notes.json is current; skipping")
        return
    if not transcript_md.exists():
        raise FileNotFoundError(f"{transcript_md} is missing — run the assemble stage first")

    transcript = transcript_md.read_text(encoding="utf-8")
    client = client_for(ctx)
    counter = CachingCounter(client.count_tokens)
    language = resolve_summary_language(ctx)
    glossary = glossary_block(ctx)

    windows = split_windows(
        transcript,
        counter,
        target_tokens=int(ctx.config.get("llm.window_tokens", 6000)),
        overlap_tokens=int(ctx.config.get("llm.window_overlap_tokens", 300)),
    )
    log.info("summarizing %d window(s) in %s", len(windows), language)

    map_prompt = load_prompt("map")
    reduce_prompt = load_prompt("reduce")
    system_prompt = load_prompt("system")
    instruction = language_instruction(language)

    partials: list[dict[str, Any]] = []
    usage: list[dict[str, Any]] = []
    for window in windows:
        ctx.checkpoint()
        result = client.complete_json(
            system_blocks=system_blocks(
                f"{system_prompt.text}\n\n{map_prompt.text}\n\n{instruction}", glossary
            ),
            user=window.text,
            schema=MAP_SCHEMA,
            max_tokens=int(ctx.config.get("llm.max_tokens", 16000)),
        )
        partials.append(result.data)
        usage.append(result.usage)

    ctx.checkpoint()
    reduce_input = json.dumps(
        {
            "meeting": {
                "title": ctx.meeting.title,
                "started_at": ctx.meeting.started_at,
                "duration_s": ctx.meeting.duration_s,
                "language": ctx.meeting.language,
            },
            "windows": partials,
        },
        ensure_ascii=False,
    )
    final = client.complete_json(
        system_blocks=system_blocks(
            f"{system_prompt.text}\n\n{reduce_prompt.text}\n\n{instruction}", glossary
        ),
        user=reduce_input,
        schema=NOTES_SCHEMA,
        max_tokens=int(ctx.config.get("llm.max_tokens", 16000)),
    )
    usage.append(final.usage)
    notes = validate(final.data)
    output.write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")

    ctx.dao.update_meeting(ctx.meeting.id, summary_language=language)
    if notes.get("title") and ctx.meeting.title_source in (None, "window", "llm"):
        ctx.dao.update_meeting(ctx.meeting.id, title=notes["title"], title_source="llm")

    reasons = sanity_gates(notes, ctx)
    meta.mirror(
        ctx.refresh(),
        prompt_versions=prompt_versions("system", "map", "reduce"),
        summary_language=language,
        windows=len(windows),
        llm={"name": client.name, "usage": _sum_usage(usage)},
    )
    for reason in reasons:
        meta.add_review_reason(folder, reason)
    if reasons:
        _flag_review(ctx, reasons)
    ctx.metrics.update(
        {
            "windows": len(windows),
            "token_counts": counter.calls,
            "cache_read_tokens": sum(int(u.get("cache_read_input_tokens", 0) or 0) for u in usage),
            "review_reasons": reasons,
        }
    )


def _sum_usage(usage: Sequence[dict[str, Any]]) -> dict[str, int]:
    total: dict[str, int] = {}
    for entry in usage:
        for key, value in entry.items():
            if isinstance(value, int):
                total[key] = total.get(key, 0) + value
    return total


def sanity_gates(notes: dict[str, Any], ctx: StageContext) -> list[str]:
    """Surfaced as NEEDS_REVIEW, never silently accepted (§9.4)."""
    reasons: list[str] = []
    duration = ctx.meeting.duration_s or 0
    if duration > LONG_MEETING_S and not notes.get("action_items"):
        reasons.append(f"no action items on a {duration // 60}-minute meeting")
    if len(notes.get("tldr", [])) < 2:
        reasons.append("the summary has fewer than two TL;DR lines")
    known = {"ME", "THEM"}
    known.update(str(person.get("name", "")) for person in notes.get("participants", []))
    for item in notes.get("action_items", []):
        who = str(item.get("who", "")).strip()
        if who and who not in known:
            reasons.append(f"action item owner {who!r} is not a known participant")
    return reasons


def _flag_review(ctx: StageContext, reasons: Sequence[str]) -> None:
    from app.pipeline.states import MeetingState, is_legal

    meeting = ctx.refresh()
    current = MeetingState(meeting.state)
    if current is MeetingState.NEEDS_REVIEW:
        return
    if is_legal(current, MeetingState.NEEDS_REVIEW):
        ctx.dao.set_state(meeting.id, MeetingState.NEEDS_REVIEW)
        log.warning("meeting %s needs review: %s", meeting.id, "; ".join(reasons))


def load_notes(folder: Path) -> dict[str, Any]:
    payload = json.loads(notes_path(folder).read_text(encoding="utf-8"))
    return dict(payload)


def result_is_valid(result: LlmResult) -> bool:
    from app.llm.schema import is_valid

    return is_valid(result.data)
