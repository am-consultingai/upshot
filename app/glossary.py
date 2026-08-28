"""The glossary: people, product names, acronyms, Hebrew↔English jargon.

Used twice — as Whisper's ``initial_prompt`` (biases decoding) and as a correction pass
before summarization. The highest-leverage quality knob for Hebrew technical meetings.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from app.db.dao import Dao, GlossaryTerm
from app.log import get

log = get(__name__)

CHARS_PER_TOKEN = 3.0  # conservative for Hebrew, which runs 2–4 tokens per word


@dataclass(frozen=True)
class Entry:
    term: str
    kind: str | None = None
    aliases: tuple[str, ...] = ()
    note: str | None = None

    def as_prompt_fragment(self) -> str:
        if self.aliases:
            return f"{self.term} ({', '.join(self.aliases)})"
        return self.term


def load_yaml(path: Path) -> list[Entry]:
    if not path.exists():
        return []
    import yaml

    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    entries: list[Entry] = []
    if isinstance(payload, dict):
        payload = payload.get("terms", [])
    for item in payload:
        if isinstance(item, str):
            entries.append(Entry(item))
            continue
        if not isinstance(item, dict) or "term" not in item:
            continue
        aliases = item.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [part.strip() for part in aliases.split(",") if part.strip()]
        entries.append(
            Entry(
                term=str(item["term"]),
                kind=item.get("kind"),
                aliases=tuple(str(a) for a in aliases),
                note=item.get("note"),
            )
        )
    return entries


def from_db(dao: Dao) -> list[Entry]:
    return [_from_row(row) for row in dao.glossary()]


def _from_row(row: GlossaryTerm) -> Entry:
    aliases = tuple(part.strip() for part in (row.aliases or "").split(",") if part.strip())
    return Entry(row.term, row.kind, aliases, row.note)


def merge(*sources: Iterable[Entry]) -> list[Entry]:
    seen: dict[str, Entry] = {}
    for source in sources:
        for entry in source:
            seen.setdefault(entry.term.lower(), entry)
    return sorted(seen.values(), key=lambda entry: entry.term.lower())


def initial_prompt(
    entries: Sequence[Entry],
    *,
    participants: Sequence[str] = (),
    previous_sentence: str | None = None,
    max_tokens: int = 200,
) -> str | None:
    """Glossary terms, attendee names, and the previous chunk's trailing sentence.

    Capped at ``max_tokens`` — a prompt longer than that costs decode quality rather than
    buying it.
    """
    parts: list[str] = []
    parts.extend(str(name) for name in participants if str(name).strip())
    parts.extend(entry.as_prompt_fragment() for entry in entries)
    budget = int(max_tokens * CHARS_PER_TOKEN)
    text = ""
    for part in parts:
        candidate = f"{text}, {part}" if text else part
        if len(candidate) > budget:
            break
        text = candidate
    if previous_sentence:
        tail = previous_sentence.strip()
        if tail and len(text) + len(tail) + 2 <= budget + len(tail):
            text = f"{text}. {tail}" if text else tail
    return text or None


def apply_corrections(text: str, entries: Sequence[Entry]) -> str:
    """Alias → canonical term, applied before summarization."""
    corrected = text
    for entry in entries:
        for alias in entry.aliases:
            if not alias:
                continue
            corrected = corrected.replace(alias, entry.term)
    return corrected
