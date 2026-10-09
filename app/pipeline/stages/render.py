"""notes.json → two HTML variants (§10). Deterministic, offline, no LLM.

Direction follows the **summary** language, not the meeting's: a Hebrew meeting
summarised into English produces an LTR document.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import nh3

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


#: Model-authored HTML goes straight into a page the user opens, and the model reads
#: speech and invite text that outsiders write. So the document is cleaned by an
#: allowlist: structure, text, tables and inline styling stay, because the free-form
#: design is made of them; anything that runs code, frames another page or makes a
#: request of its own (images included) goes. Links survive only as in-page anchors.
_TAGS = frozenset(
    {
        *("h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "li", "br", "hr"),
        *("strong", "em", "b", "i", "u", "s", "code", "pre", "blockquote"),
        *("span", "div", "section", "article", "header", "footer"),
        *("small", "sub", "sup", "mark", "abbr", "time", "figure", "figcaption"),
        *("dl", "dt", "dd", "details", "summary", "a"),
        *("table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "colgroup", "col"),
    }
)
_ATTRIBUTES = {
    "*": {"style", "class", "dir", "lang", "title"},
    "a": {"href"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
    "col": {"span"},
    "colgroup": {"span"},
    "time": {"datetime"},
    "details": {"open"},
}
#: Gone with their content, not just their tags: text inside these is not the document's.
_DROPPED_WITH_CONTENT = frozenset(
    {
        *("script", "style", "iframe", "frame", "frameset", "object", "embed", "applet"),
        *("svg", "math", "noscript", "template", "textarea", "select", "title"),
    }
)
#: Inline CSS that can make a request or run code. Comments are taken out before the
#: check, and a backslash (a CSS escape can spell any of these) drops the declaration.
_CSS_COMMENT = re.compile(r"/\*.*?(\*/|$)", re.DOTALL)
_CSS_UNSAFE = re.compile(
    r"\\|url\s*\(|image-set\s*\(|image\s*\(|cross-fade\s*\(|src\s*\(|expression\s*\("
    r"|javascript:|@import|behavior|-moz-binding",
    re.IGNORECASE,
)


def _clean_style(value: str) -> str | None:
    kept = [
        declaration.strip()
        for declaration in _CSS_COMMENT.sub("", value).split(";")
        if declaration.strip() and not _CSS_UNSAFE.search(declaration)
    ]
    return "; ".join(kept) or None


def _attribute(element: str, attribute: str, value: str) -> str | None:
    if attribute == "style":
        return _clean_style(value)
    if attribute == "href":
        # In-page anchors only: an outside link is how a summary would phish.
        return value if value.strip().startswith("#") else None
    return value


def sanitize(html: str) -> str:
    """The summary, reduced to the allowlist above. Run on render and again on read."""
    return nh3.clean(
        html,
        tags=set(_TAGS),
        clean_content_tags=set(_DROPPED_WITH_CONTENT),
        attributes={tag: set(names) for tag, names in _ATTRIBUTES.items()},
        attribute_filter=_attribute,
        # The next change cites a moment on each bullet (``data-at-ms``).
        generic_attribute_prefixes={"data-"},
        url_schemes=set(),
        link_rel=None,
        strip_comments=True,
    )


def read_summary(path: Path) -> str:
    """A rendered summary from disk, cleaned again: older files predate the allowlist."""
    return sanitize(path.read_text(encoding="utf-8"))


def render_free(notes: dict[str, Any], *, language: str) -> Rendered:
    """Free-form: the prompt wrote the document, so nothing here relays it out."""
    body = sanitize(str(notes.get("summary_html", "")))
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
    rendered = render_free(notes, language=language)
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
