"""Citations the model writes, checked before the user sees them (D61, D62).

The model marks a claim with ``[[m:<meeting_id>@<at_ms>]]`` (a moment) or
``[[m:<meeting_id>]]`` (the meeting as a whole). Each marker is checked here as the
answer streams: the meeting must exist, and a moment is snapped to the transcript line
it falls in, exactly as "Ask this meeting" does (``ask._line_at``). What the user gets
in its place is ``[n](#cite-n)`` — a numbered link the panel draws as a chip — and a
``data-citation`` part carrying the quote, the speaker, the meeting and the time. A
marker that does not check out is dropped, never shown as a broken reference.

A marker can arrive split across stream chunks, so anything from an unclosed ``[`` to
the end of a chunk is held back until the rest arrives.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.ask import Line, _line_at, transcript_lines
from app.db.dao import Dao

MARKER = re.compile(r"\[\[m:([A-Za-z0-9_.\-]+)(?:@(\d+))?\]\]")
#: Follow-up questions the model offers at the end of an answer (D62), taken out of the
#: text and sent as their own part: ``[[suggest: one | two | three]]``.
SUGGEST = re.compile(r"\s*\[\[suggest:(.*?)\]\]\s*", re.DOTALL)
#: Longer than any real marker: a held-back tail this long was never one.
MAX_HELD = 96
MAX_HELD_SUGGEST = 600


@dataclass
class Citer:
    dao: Dao
    held: str = ""
    citations: list[dict[str, Any]] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    _by_key: dict[tuple[str, int | None], int] = field(default_factory=dict)
    _lines: dict[str, list[Line]] = field(default_factory=dict)

    def feed(self, text: str) -> tuple[str, list[dict[str, Any]]]:
        """Text in, (text to show now, new citations) out."""
        text = self.held + text
        self.held = ""
        cut = _hold_from(text)
        if cut is not None:
            text, self.held = text[:cut], text[cut:]
        return self._replace(text)

    def flush(self) -> tuple[str, list[dict[str, Any]]]:
        """The end of the answer: whatever was held back is text after all."""
        text, self.held = self.held, ""
        return self._replace(text)

    def _replace(self, text: str) -> tuple[str, list[dict[str, Any]]]:
        new: list[dict[str, Any]] = []

        def offered(match: re.Match[str]) -> str:
            items = [" ".join(item.split()) for item in match.group(1).split("|")]
            self.suggestions = [item for item in items if item][:3]
            return ""

        text = SUGGEST.sub(offered, text)

        def one(match: re.Match[str]) -> str:
            meeting_id = match.group(1)
            at_ms = int(match.group(2)) if match.group(2) is not None else None
            citation = self._check(meeting_id, at_ms)
            if citation is None:
                return ""
            key = (citation["meeting_id"], citation["at_ms"])
            if key not in self._by_key:
                citation["n"] = len(self.citations) + 1
                self._by_key[key] = citation["n"]
                self.citations.append(citation)
                new.append(citation)
            n = self._by_key[key]
            return f"[{n}](#cite-{n})"

        return MARKER.sub(one, text), new

    def _check(self, meeting_id: str, at_ms: int | None) -> dict[str, Any] | None:
        meeting = self.dao.get_meeting(meeting_id)
        if meeting is None:
            return None
        citation: dict[str, Any] = {
            "meeting_id": meeting.id,
            "title": meeting.title or "",
            "started_at": meeting.started_at,
            "at_ms": None,
            "speaker": "",
            "quote": "",
        }
        if at_ms is None:
            return citation
        if meeting.id not in self._lines:
            self._lines[meeting.id] = transcript_lines(self.dao, meeting)
        lines = self._lines[meeting.id]
        if not lines:
            return citation  # a moment in a meeting with no transcript: the meeting itself
        line = _line_at(lines, at_ms)
        citation.update(at_ms=line.at_ms, speaker=line.speaker, quote=line.text)
        return citation


def _hold_from(text: str) -> int | None:
    """Where an unfinished marker might start, if the chunk ends inside one."""
    opened = text.rfind("[[")
    if opened >= 0 and "]]" not in text[opened:]:
        longest = MAX_HELD_SUGGEST if text[opened:].startswith("[[s") else MAX_HELD
        return opened if len(text) - opened <= longest else None
    if text.endswith("["):
        return len(text) - 1
    return None
