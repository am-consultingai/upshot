"""The Terms as one Markdown file, and the three forms they are shown in.

``app/legal/terms.md`` is the only copy anyone edits. The website's ``terms.html``, the
installer's licence page (``packaging/terms.txt``), the manifest installed copies poll
(``site/legal/terms.json``) and the screen in the app are all rendered from it here, so
the version a user accepts in the installer is word for word the one on the website.

The Markdown is a deliberately small subset, the one a lawyer's edits stay inside:

- a front-matter block (``version``, ``effective``, ``material``, ``summary``);
- ``# Title`` once, ``## N. Heading {#anchor}`` per section;
- paragraphs, ``- `` bullet lists, ``**bold**`` and ``[text](url)`` links.

Anything else is passed through as text rather than guessed at.
"""

from __future__ import annotations

import hashlib
import html
import re
import textwrap
from dataclasses import dataclass, field
from datetime import date

_FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_SECTION = re.compile(r"^## (.+?)(?:\s*\{#([a-z0-9-]+)\})?\s*$")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class TermsError(ValueError):
    """A terms document that cannot be trusted: no version, a bad date, an empty body."""


@dataclass(frozen=True)
class Section:
    title: str
    anchor: str
    #: Blocks in order: ("p", text) or ("ul", [item, ...]). Inline Markdown is kept.
    blocks: tuple[tuple[str, object], ...]


@dataclass(frozen=True)
class Terms:
    version: str
    effective: str
    material: bool
    summary: str
    title: str
    sections: tuple[Section, ...]
    #: The file exactly as read, which the manifest's checksum covers.
    source: str = field(repr=False)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.source.encode("utf-8")).hexdigest()

    def effective_on(self, today: date) -> bool:
        return date.fromisoformat(self.effective) <= today

    # ------------------------------------------------------------------ renderings

    def html(self, *, heading_level: int = 2) -> str:
        """The sections as HTML, each a ``<section id=anchor>``: the site page and the app."""
        h = f"h{heading_level}"
        out: list[str] = []
        for section in self.sections:
            out.append(f'  <section id="{section.anchor}">')
            out.append(f"    <{h}>{html.escape(section.title)}</{h}>")
            for kind, body in section.blocks:
                if kind == "p":
                    out.append(f"    <p>{_inline_html(str(body))}</p>")
                else:
                    out.append("    <ul>")
                    out.extend(f"      <li>{_inline_html(item)}</li>" for item in _items(body))
                    out.append("    </ul>")
            out.append("  </section>")
        return "\n".join(out) + "\n"

    def toc_html(self) -> str:
        """The contents list, by section title without its number."""
        rows = [
            f'      <li><a href="#{s.anchor}">{html.escape(_unnumbered(s.title))}</a></li>'
            for s in self.sections
        ]
        return "\n".join(rows) + "\n"

    def text(self, width: int = 76) -> str:
        """Plain text for the installer's licence page: no markup, wrapped, CRLF-free."""
        lines = [self.title, "=" * len(self.title), ""]
        lines.append(f"Version {self.version}, effective {self.effective}.")
        lines.append("")
        for section in self.sections:
            lines.append(section.title.upper())
            lines.append("")
            for kind, body in section.blocks:
                if kind == "p":
                    lines.extend(textwrap.wrap(_inline_text(str(body)), width))
                else:
                    for item in _items(body):
                        lines.extend(
                            textwrap.wrap(
                                _inline_text(item),
                                width,
                                initial_indent="  - ",
                                subsequent_indent="    ",
                            )
                        )
                lines.append("")
        return "\n".join(lines).rstrip() + "\n"


def parse(source: str) -> Terms:
    """Read a terms document, refusing one the app could not show or record honestly."""
    source = source.replace("\r\n", "\n")
    front = _FRONT.match(source)
    if not front:
        raise TermsError("the terms have no front matter")
    meta: dict[str, str] = {}
    for line in front.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    version = meta.get("version", "")
    effective = meta.get("effective", "")
    for name, value in (("version", version), ("effective", effective)):
        if not _DATE.match(value):
            raise TermsError(f"the terms' {name} must be a date (YYYY-MM-DD), not {value!r}")
        date.fromisoformat(value)  # a real date, not 2026-13-40
    material = meta.get("material", "false").lower() in ("true", "yes", "1")

    title = ""
    sections: list[Section] = []
    current: tuple[str, str] | None = None
    blocks: list[tuple[str, object]] = []
    paragraph: list[str] = []
    items: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(("p", " ".join(paragraph)))
            paragraph.clear()

    def flush_items() -> None:
        if items:
            blocks.append(("ul", tuple(items)))
            items.clear()

    def flush_section() -> None:
        flush_paragraph()
        flush_items()
        if current is not None:
            sections.append(Section(current[0], current[1], tuple(blocks)))
        blocks.clear()

    for raw in source[front.end() :].splitlines():
        line = raw.rstrip()
        if line.startswith("# "):
            title = line[2:].strip()
            continue
        heading = _SECTION.match(line)
        if heading:
            flush_section()
            anchor = heading.group(2) or _slug(heading.group(1))
            current = (heading.group(1).strip(), anchor)
            continue
        if not line.strip():
            flush_paragraph()
            flush_items()
            continue
        if line.startswith("- "):
            flush_paragraph()
            items.append(line[2:].strip())
        elif items and raw.startswith("  "):
            items[-1] += " " + line.strip()
        else:
            flush_items()
            paragraph.append(line.strip())
    flush_section()

    if not title or not sections:
        raise TermsError("the terms have no title or no sections")
    anchors = [s.anchor for s in sections]
    if len(set(anchors)) != len(anchors):
        raise TermsError("two sections of the terms share an anchor")
    return Terms(
        version=version,
        effective=effective,
        material=material,
        summary=meta.get("summary", ""),
        title=title,
        sections=tuple(sections),
        source=source,
    )


def _items(body: object) -> tuple[str, ...]:
    return tuple(str(item) for item in body) if isinstance(body, (list, tuple)) else ()


def _inline_html(text: str) -> str:
    """Escape, then turn the two inline forms the subset allows into markup."""
    parts: list[str] = []
    last = 0
    for link in _LINK.finditer(text):
        parts.append(_bold_html(html.escape(text[last : link.start()], quote=False)))
        href = link.group(2)
        external = href.startswith("http")
        attrs = ' target="_blank" rel="noreferrer"' if external else ""
        label = _bold_html(html.escape(link.group(1), quote=False))
        parts.append(f'<a href="{html.escape(href)}"{attrs}>{label}</a>')
        last = link.end()
    parts.append(_bold_html(html.escape(text[last:], quote=False)))
    return "".join(parts)


def _bold_html(escaped: str) -> str:
    return _BOLD.sub(r"<strong>\1</strong>", escaped)


def _inline_text(text: str) -> str:
    """Plain text: a link keeps its address unless the text already is it."""

    def link(match: re.Match[str]) -> str:
        label, href = match.group(1), match.group(2)
        target = href.removeprefix("mailto:")
        return label if label in (target, href) or target.endswith(label) else f"{label} ({target})"

    return _BOLD.sub(r"\1", _LINK.sub(link, text))


def _unnumbered(title: str) -> str:
    return re.sub(r"^\d+\.\s*", "", title)


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _unnumbered(title).lower()).strip("-")
