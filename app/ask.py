"""Ask this meeting — or this meeting and the ones it is a thread with (D54).

A summary answers the questions its writer thought of. The one the reader has three weeks
later ("what number did Dana give for churn?") is usually in the transcript and nowhere
in the notes. This sends the transcript and the question to the same model the summary
came from and asks for an answer with the moments it rests on, so every answer can be
checked by clicking through to the recording.

Deliberately small: no embeddings, no retrieval index, no conversation history. A
meeting's transcript fits in one hosted-model request, and three related ones mostly do;
where they do not, the context is cut to a budget rather than chunked and searched,
because an answer from a truncated transcript is visibly incomplete, while an answer
from a badly retrieved chunk is confidently wrong.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.config import Config
from app.db.dao import Dao, Meeting
from app.llm.client import LlmClient, make_client, system_blocks
from app.log import get

log = get(__name__)

#: Related meetings included under ``scope="related"``.
MAX_RELATED = 3

#: Characters of transcript per request, by provider. A character is roughly a third of a
#: token in English and less in Hebrew, so these stay well inside each context window
#: while leaving room for the answer. Anything unlisted is assumed to be a small local model.
CHAR_BUDGET: dict[str, int] = {
    "anthropic": 400_000,
    "claude-subscription": 400_000,
    "codex-subscription": 300_000,
    "gemini": 400_000,
    "openai": 300_000,
}
DEFAULT_CHAR_BUDGET = 24_000

ASK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer"],
    "properties": {
        "answer": {"type": "string"},
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["at_ms"],
                "properties": {
                    "meeting_id": {"type": ["string", "null"]},
                    "at_ms": {"type": "integer", "minimum": 0},
                },
            },
        },
    },
}

INSTRUCTIONS = """\
You answer questions about recorded meetings. You are given one or more transcripts, each
under a line "=== Meeting <id> — <title> (<date>)", with every turn written as
"[mm:ss] SPEAKER: text". Answer the question from those transcripts only.

- If the transcripts do not answer it, say so plainly. Never fill a gap from general
  knowledge, and never invent a number, a name or a date.
- Answer in the language the question was asked in. Keep names, product names and
  technical terms verbatim.
- Be brief: a sentence or two, or a short list when the answer is a list.
- Cite the turns your answer rests on: for each, the meeting id from its header and the
  turn's time in milliseconds from the start ([02:05] is 125000).

Reply with a JSON object: {"answer": "...", "citations": [{"meeting_id": "...", "at_ms": 0}]}.
"""


class AskError(RuntimeError):
    """The provider could not answer. Carries a message fit for the page."""


@dataclass(frozen=True)
class Line:
    at_ms: int
    speaker: str
    text: str


def transcript_lines(dao: Dao, meeting: Meeting) -> list[Line]:
    """The meeting's turns, with the names the user gave its speaker slots.

    ``transcript.json`` where it exists (its segments are the finest grain, so a citation
    lands on the sentence), else the indexed turns.
    """
    names = meeting.speakers
    path = meeting.path / "transcript.json"
    lines: list[Line] = []
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            for segment in payload.get("segments") or []:
                text = str(segment.get("text") or "").strip()
                if not text:
                    continue
                speaker = str(segment.get("speaker") or "?")
                lines.append(
                    Line(
                        round(float(segment.get("start") or 0) * 1000),
                        names.get(speaker, speaker),
                        " ".join(text.split()),
                    )
                )
        except (ValueError, TypeError, AttributeError) as exc:
            log.info("transcript.json unreadable for %s (%s); using the index", meeting.id, exc)
            lines = []
    if not lines:
        lines = [
            Line(turn.at_ms, names.get(turn.speaker, turn.speaker), " ".join(turn.text.split()))
            for turn in dao.turns(meeting.id)
            if turn.text.strip()
        ]
    lines.sort(key=lambda line: line.at_ms)
    return lines


def stamp(at_ms: int) -> str:
    total = max(0, at_ms) // 1000
    return f"{total // 60:02d}:{total % 60:02d}"


def section(meeting: Meeting, lines: list[Line], budget: int) -> str:
    """One labelled transcript, cut at a line boundary to fit ``budget`` characters."""
    header = f"=== Meeting {meeting.id} — {meeting.title or 'Untitled'} ({meeting.started_at[:10]})"
    out = [header]
    used = len(header)
    for line in lines:
        text = f"[{stamp(line.at_ms)}] {line.speaker}: {line.text}"
        if used + len(text) + 1 > budget:
            out.append("[… the rest of this transcript was cut to fit …]")
            break
        out.append(text)
        used += len(text) + 1
    return "\n".join(out)


@dataclass
class Answer:
    answer: str
    citations: list[dict[str, Any]]
    scope: str

    def as_api(self) -> dict[str, Any]:
        return {"answer": self.answer, "citations": self.citations, "scope": self.scope}


def ask(
    dao: Dao,
    config: Config,
    meeting: Meeting,
    question: str,
    *,
    scope: str = "meeting",
    client: LlmClient | None = None,
) -> Answer:
    """Answer ``question`` from this meeting's transcript (and its thread's, if asked)."""
    question = " ".join(question.split())
    meetings = [meeting]
    if scope == "related":
        from app.related import related

        meetings += [found.meeting for found in related(dao, meeting.id, limit=MAX_RELATED)]
    transcripts = {m.id: transcript_lines(dao, m) for m in meetings}
    # A related meeting with nothing transcribed adds a header and no evidence.
    meetings = [m for m in meetings if m.id == meeting.id or transcripts[m.id]]

    if client is None:
        # A sensitive meeting never leaves the machine, and that holds when it is only
        # *related* to the one being asked about: one sensitive transcript in the context
        # makes the whole request local.
        client = make_client(config, sensitive=any(m.sensitive for m in meetings))
    budget = CHAR_BUDGET.get(client.name, DEFAULT_CHAR_BUDGET)
    # The meeting asked about gets the larger share; related ones split the rest.
    shares = [budget] if len(meetings) == 1 else [budget // 2] + [
        (budget // 2) // (len(meetings) - 1)
    ] * (len(meetings) - 1)
    context = "\n\n".join(
        section(m, transcripts[m.id], share) for m, share in zip(meetings, shares, strict=True)
    )
    user = f"{context}\n\nQuestion: {question}"
    try:
        result = client.complete_json(
            system_blocks=system_blocks(INSTRUCTIONS, None),
            user=user,
            schema=ASK_SCHEMA,
            max_tokens=int(config.get("llm.ask_max_tokens", 2000)),
        )
    except Exception as exc:
        log.warning("ask failed on %s: %s", client.name, exc)
        raise AskError(f"{client.name} could not answer: {exc}"[:400]) from exc

    answer = str(result.data.get("answer") or "").strip()
    return Answer(answer, _citations(result.data, meeting.id, transcripts), scope)


def _citations(
    data: dict[str, Any], default_id: str, transcripts: dict[str, list[Line]]
) -> list[dict[str, Any]]:
    """The model's citations, checked against what it was actually shown.

    A citation to a meeting that was not in the context is dropped; one to a moment
    between turns is snapped to the turn it falls in, and carries that turn's text so the
    page can quote it without fetching the transcript.
    """
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for raw in data.get("citations") or []:
        if not isinstance(raw, dict):
            continue
        at_ms = raw.get("at_ms")
        if isinstance(at_ms, bool) or not isinstance(at_ms, int | float):
            continue
        meeting_id = str(raw.get("meeting_id") or default_id)
        lines = transcripts.get(meeting_id)
        if not lines:
            continue
        line = _line_at(lines, int(at_ms))
        key = (meeting_id, line.at_ms)
        if key in seen:
            continue
        seen.add(key)
        out.append({"meeting_id": meeting_id, "at_ms": line.at_ms, "text": line.text})
    return out


def _line_at(lines: list[Line], at_ms: int) -> Line:
    """The last turn starting at or before ``at_ms``; the first turn if none does."""
    chosen = lines[0]
    for line in lines:
        if line.at_ms <= at_ms:
            chosen = line
        else:
            break
    return chosen
