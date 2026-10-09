"""notes.json → two HTML variants (§10). Deterministic, offline, no LLM.

Direction follows the **summary** language, not the meeting's: a Hebrew meeting
summarised into English produces an LTR document.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app import meta
from app.asr import languages
from app.asr.models import DEFAULT_LANGUAGE
from app.log import get
from app.pipeline.artifacts import up_to_date
from app.pipeline.context import StageContext
from app.pipeline.stages.summarize import load_notes, notes_path

log = get(__name__)

UI_NAME = "summary.html"
EMAIL_NAME = "summary.email.html"

#: The page turns round for these (``app/asr/languages.py``). The page carries no chrome of
#: its own: headings and labels around it are the interface's, in the interface language;
#: only the notes are in the meeting's language.
RTL_LANGUAGES = languages.RTL_LANGUAGES


def direction_for(language: str) -> str:
    return languages.direction_for(language)


@dataclass(frozen=True)
class Rendered:
    ui: str
    email: str


#: Model-authored HTML goes straight into a page the user opens, so the two things that
#: turn a document into code come out first. Everything else — structure, styling,
#: tables, inline CSS — is left exactly as written, because that is the whole point.
_SCRIPT = re.compile(r"<script\b.*?</script\s*>", re.IGNORECASE | re.DOTALL)
_HANDLER = re.compile(r"\son[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)


def sanitize(html: str) -> str:
    return _HANDLER.sub("", _SCRIPT.sub("", html))


#: A point the summary took from one turn carries that turn's start (D94): the page shows
#: it as a control that plays the recording from there.
CITE_ATTR = "data-at-ms"
_TAG = re.compile(r"<[a-zA-Z][^>]*>")
_CITE = re.compile(r"\s" + CITE_ATTR + r"\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)
_INTEGER = re.compile(r"\d+")


def cite_moments(html: str, *, starts: Sequence[int] | None, duration_ms: int | None) -> str:
    """Every ``data-at-ms`` the model wrote, checked against the recording it cites.

    A citation that is not a whole number of milliseconds, or points outside the
    recording, is removed: a wrong moment costs the reader more trust than none. One that
    stands is moved back to the start of the turn it falls in (the latest turn at or
    before it), so a click lands on a line the transcript actually shows. ``starts`` are
    the turns' starts in ms, ascending; ``None`` when there is no transcript to snap to.
    """

    def checked(match: re.Match[str]) -> str:
        raw = match.group(1).strip("\"'").strip()
        if not _INTEGER.fullmatch(raw):
            return ""
        value = int(raw)
        if duration_ms is not None and value > duration_ms:
            return ""
        if starts:
            # Before the first turn there is nothing earlier to land on: the first line.
            value = starts[max(0, bisect_right(starts, value) - 1)]
        return f' {CITE_ATTR}="{value}"'

    def tag(match: re.Match[str]) -> str:
        return _CITE.sub(checked, match.group(0))

    if CITE_ATTR not in html.lower():
        return html
    return _TAG.sub(tag, html)


def render_free(
    notes: dict[str, Any],
    *,
    language: str,
    starts: Sequence[int] | None = None,
    duration_ms: int | None = None,
) -> Rendered:
    """Free-form: the prompt wrote the document, so nothing here relays it out."""
    body = sanitize(str(notes.get("summary_html", "")))
    body = cite_moments(body, starts=starts, duration_ms=duration_ms)
    direction = direction_for(language)
    page = f'<div dir="{direction}" lang="{language}" class="ma-free">{body}</div>'
    return Rendered(ui=page, email=page)


def output_paths(folder: Path) -> tuple[Path, Path]:
    return Path(folder) / UI_NAME, Path(folder) / EMAIL_NAME


def plaintext(notes: dict[str, Any], language: str) -> str:
    """A text/plain alternative for the email, from whatever HTML the prompt produced.

    Crude on purpose: tags out, entities in, blank lines collapsed. There is no structure
    left to walk — that was the point of removing the schema — so there is nothing
    cleverer to do than strip the markup.
    """
    import html as html_module

    body = sanitize(str(notes.get("summary_html", "")))
    body = re.sub(r"<(br|/p|/div|/h[1-6]|/li|/tr)\s*>", "\n", body, flags=re.IGNORECASE)
    text = html_module.unescape(re.sub(r"<[^>]+>", "", body))
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line) + "\n"


def turn_starts(folder: Path) -> list[int] | None:
    """Where each line of the transcript begins, in ms: what a citation snaps to."""
    from app.pipeline.stages.assemble import load_turns, transcript_paths

    if not transcript_paths(folder)[0].exists():
        return None
    try:
        return sorted({turn.at_ms for turn in load_turns(folder)})
    except (OSError, ValueError) as exc:
        log.warning("could not read the transcript to check the summary's citations: %s", exc)
        return None


def duration_ms(ctx: StageContext) -> int | None:
    seconds = ctx.meeting.duration_s
    return int(seconds * 1000) if seconds else None


def run(ctx: StageContext) -> None:
    folder = ctx.folder
    source = notes_path(folder)
    ui_path, email_path = output_paths(folder)
    if not ctx.force and up_to_date(ui_path, [source]) and up_to_date(email_path, [source]):
        log.info("summary HTML is current; skipping")
        return
    if not source.exists():
        raise FileNotFoundError(f"{source} is missing — run the summarize stage first")

    notes = load_notes(folder)
    language = ctx.meeting.summary_language or ctx.config.summary_language
    if language == "auto":
        language = ctx.meeting.language or DEFAULT_LANGUAGE
    rendered = render_free(
        notes, language=language, starts=turn_starts(folder), duration_ms=duration_ms(ctx)
    )
    ui_path.write_text(rendered.ui, encoding="utf-8")
    email_path.write_text(rendered.email, encoding="utf-8")
    # The searchable copy (D61): search and the assistant read the summary from here.
    ctx.dao.index_summary(ctx.meeting.id, plaintext(notes, language))
    meta.mirror(ctx.refresh(), summary_language=language, rendered=True)
    ctx.metrics.update({"summary_language": language, "dir": direction_for(language)})
    log.info("rendered %s (%s)", ui_path.name, direction_for(language))


def backfill_search(dao: Any) -> int:
    """Copy the summary of every meeting that has none in the search index yet.

    Meetings summarized before summaries were searchable (2026-09-26) have only their
    files. Each is read once: a meeting with no summary gets an empty copy, which marks it
    as checked. Returns how many summaries were found.
    """
    found = 0
    for meeting in dao.meetings_without_summary_text():
        text = ""
        try:
            if notes_path(Path(meeting.folder)).exists():
                text = plaintext(load_notes(Path(meeting.folder)), meeting.language or "")
        except (OSError, ValueError) as exc:
            log.warning("could not read the summary of %s for search: %s", meeting.id, exc)
        dao.index_summary(meeting.id, text)
        found += bool(text.strip())
    return found
