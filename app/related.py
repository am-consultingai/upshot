"""Which other meetings this one is part of a thread with (D53).

A meeting is rarely a single event: the pricing review follows last week's pricing
review, the commitment made on Monday is chased on Thursday, the same four people meet
about the same customer. Nothing in the library said so, and "what did we say about this
last time" meant searching by memory.

Four signals, each one a reason the page can show in words rather than a bare score:

- ``shared_actions`` — an action item here is substantially the same as one there (the
  normalised text is identical, or the word sets overlap by a Jaccard of 0.5 or more).
  The strongest signal: a commitment carried from one meeting to the next *is* a thread.
- ``same_series`` — the same calendar title. Weekly one-on-ones and standups are series
  whether or not Google calls them recurring.
- ``same_people`` — two or more of the same invited people (or, when either meeting had
  only one other person, that same person). Names come from the calendar snapshot, so a
  meeting with no invitation never matches on people.
- ``mentions`` — a distinctive word said in both: at least five letters, not a stopword
  in English or Hebrew, and rare across the rest of the library. One word only, the rarest,
  because a list of shared words reads as noise and one rare word reads as the topic.

Everything is computed on request from what is already stored. There is no index to keep
in step, and a library of a few hundred meetings is a few hundred small reads.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.db.dao import ActionItem, Dao, Meeting
from app.pipeline.states import MeetingState

MAX_RELATED = 5
JACCARD_THRESHOLD = 0.5
MIN_TERM_LETTERS = 5

WORD_RE = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?", re.UNICODE)

#: Words of five letters or more that say nothing about what a meeting was about. The
#: rarity test does the rest once a library is large; this list is what keeps a library of
#: three meetings from being "related" by the word "actually".
_STOPWORD_TEXT = """
    about above actually after again against almost along already although always among
    another anyone anything around because become before being below between both cannot
    could didn't doesn't during either enough every everyone everything first going gonna
    great having hello maybe might never other others people perhaps please pretty quite
    rather really right second seems should since something sometimes still sure thank
    thanks their theirs there these thing things think those though three through today
    tomorrow under until using wanna we're where whether which while would yeah yesterday
    you're yours okay alright basically definitely probably meeting meetings minute minutes
    question questions point points agree agreed start started stuff kind sorta little
    later next week weeks month months years follow make makes made doing done
    אנחנו עכשיו בעצם שאנחנו צריכים הזאת כאילו יכולים לעשות משהו באמת אותו שלנו אתכם
    איתם למשל בדיוק הדברים דברים פגישה הפגישה בסדר אפשר צריך שצריך שאני אתם אותם אולי
    שלכם שלהם עוד אבל גם כמו מאוד ממש יודע יודעת חושב חושבת אומר אומרת רוצה רוצים
    שהוא שהיא שהם הזה זאת היום מחר אתמול השבוע שבוע חודש לראות לדבר להגיד אמרתי
    בהחלט כלומר כנראה תודה סבבה אוקיי יאללה נכון ככה איפה מתי למה כמה בגלל אחרי לפני
"""
STOPWORDS = frozenset(_STOPWORD_TEXT.split())


def tokens(text: str) -> set[str]:
    return {word.casefold() for word in WORD_RE.findall(text)}


def _content(text: str) -> set[str]:
    """The words of an action item that carry its meaning."""
    return {word for word in tokens(text) if len(word) > 2 and word not in STOPWORDS}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _letters(word: str) -> int:
    return sum(1 for char in word if char.isalpha())


def _distinctive(text: str) -> set[str]:
    return {
        word
        for word in tokens(text)
        if _letters(word) >= MIN_TERM_LETTERS and word not in STOPWORDS
    }


@dataclass
class Related:
    meeting: Meeting
    score: float = 0.0
    reasons: list[dict[str, Any]] = field(default_factory=list)

    def as_api(self) -> dict[str, Any]:
        return {
            "id": self.meeting.id,
            "title": self.meeting.title,
            "started_at": self.meeting.started_at,
            "duration_s": self.meeting.duration_s,
            "reasons": self.reasons,
        }


def _people(meeting: Meeting) -> set[str]:
    from app.meetings import calendar_payload

    names = calendar_payload(meeting).get("participants") or []
    return {" ".join(str(name).split()).casefold() for name in names if str(name).strip()}


def _people_display(meeting: Meeting) -> dict[str, str]:
    from app.meetings import calendar_payload

    names = calendar_payload(meeting).get("participants") or []
    return {" ".join(str(n).split()).casefold(): " ".join(str(n).split()) for n in names}


def _series(meeting: Meeting) -> str:
    from app.meetings import calendar_payload

    title = calendar_payload(meeting).get("title")
    return " ".join(str(title).split()).casefold() if title else ""


def shared_actions(mine: list[ActionItem], theirs: list[ActionItem]) -> int:
    """How many of this meeting's items are substantially one of theirs."""
    count = 0
    their_words = [(item.what.casefold(), _content(item.what)) for item in theirs]
    for item in mine:
        norm = " ".join(item.what.split()).casefold()
        words = _content(item.what)
        if any(
            norm == " ".join(other.split()) or jaccard(words, other_words) >= JACCARD_THRESHOLD
            for other, other_words in their_words
        ):
            count += 1
    return count


def same_people(a: set[str], b: set[str]) -> set[str]:
    """Shared invitees, when there are enough of them to mean something."""
    shared = a & b
    if not shared:
        return set()
    if len(shared) >= 2:
        return shared
    # One shared person is a thread only when one side *was* that one person: a
    # one-on-one with Dana and a twelve-person review Dana attended are not related.
    if len(a) == 1 or len(b) == 1:
        return shared
    return set()


def _texts(dao: Dao, meetings: list[Meeting]) -> dict[str, set[str]]:
    """Each meeting's distinctive words, from its title and its indexed turns."""
    words: dict[str, set[str]] = {m.id: _distinctive(m.title or "") for m in meetings}
    for row in dao.conn.execute("SELECT meeting_id, text FROM transcript_turns").fetchall():
        bucket = words.get(row["meeting_id"])
        if bucket is not None:
            bucket |= _distinctive(row["text"])
    return words


def related(dao: Dao, meeting_id: str, *, limit: int = MAX_RELATED) -> list[Related]:
    """The meetings most plausibly part of the same thread, best first."""
    target = dao.require_meeting(meeting_id)
    everything = dao.list_meetings(limit=100_000)
    others = [
        m
        for m in everything
        if m.id != target.id
        and m.state not in (MeetingState.RECORDING, MeetingState.DISCARDED)
    ]
    if not others:
        return []

    items: dict[str, list[ActionItem]] = {}
    for item in dao.action_items(limit=100_000):
        items.setdefault(item.meeting_id, []).append(item)

    corpus = [target, *others]
    words = _texts(dao, corpus)
    frequency: dict[str, int] = {}
    for bucket in words.values():
        for word in bucket:
            frequency[word] = frequency.get(word, 0) + 1
    # "Few other meetings": at most a quarter of the library, and never fewer than one
    # beyond the pair itself, so a small library can still find a thread.
    rare_limit = max(3, len(corpus) // 4)

    people = _people(target)
    display = _people_display(target)
    series = _series(target)
    out: list[Related] = []
    for other in others:
        found = Related(other)

        count = shared_actions(items.get(target.id, []), items.get(other.id, []))
        if count:
            found.reasons.append({"code": "shared_actions", "count": count})
            found.score += 3.0 + count

        if series and series == _series(other):
            found.reasons.append({"code": "same_series"})
            found.score += 3.0

        shared = same_people(people, _people(other))
        if shared:
            names = sorted(display.get(name, name) for name in shared)
            found.reasons.append({"code": "same_people", "names": names})
            found.score += 1.0 + len(shared) * 0.5

        common = [
            word
            for word in words.get(target.id, set()) & words.get(other.id, set())
            if frequency.get(word, 0) <= rare_limit
        ]
        if common:
            # Rarest first; then the longer word, which is usually the more specific; then
            # alphabetical, so the answer does not change between two identical requests.
            term = min(common, key=lambda word: (frequency[word], -len(word), word))
            found.reasons.append({"code": "mentions", "term": term})
            found.score += 1.0 + 1.0 / frequency[term]

        if found.reasons:
            out.append(found)

    # Best first; among equals, the nearer in time, since the last meeting on a topic is
    # the one most often wanted.
    out.sort(key=lambda r: (-r.score, -_closeness(target, r.meeting)))
    return out[:limit]


def _closeness(target: Meeting, other: Meeting) -> float:
    from datetime import datetime

    try:
        a = datetime.fromisoformat(target.started_at)
        b = datetime.fromisoformat(other.started_at)
        if (a.tzinfo is None) != (b.tzinfo is None):
            return 0.0
        return -abs((a - b).total_seconds())
    except ValueError:
        return 0.0
