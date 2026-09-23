"""What "by Thursday" means: a spoken due date resolved to a calendar date.

The summarizer is asked for ``due_at`` directly, and a good model gets it right. This is
for everything else: a model that returned only the words, a local model that returned
nothing usable, and every action item stored before ``due_at`` existed — those are
resolved lazily, at read time, against the date of the meeting they were said in.

Why not a library: chrono-node is JavaScript, ``dateparser`` is large and resolves
"Thursday" to the *previous* one by default, and neither reads Hebrew weekdays in the
forms people actually say them ("ביום חמישי", "עד חמישי", "מחרתיים"). The phrases a
commitment's deadline comes in are few, and a table of them is easier to test than a
general parser is to configure.

The one rule that matters more than coverage: **never guess**. A date that is wrong is
worse than no date, because the inbox sorts and flags by it and the reader trusts the
flag. Anything not recognised, and anything that names two different days, is None.

Conventions, which are Israeli because the user is:

- Weeks start on Sunday; the working week ends on Friday. "End of week" is that Friday.
- Numeric dates are day-first: 24/9 is the 24th of September.
- A weekday means its next occurrence strictly *after* the meeting day. "Thursday" said
  on a Thursday means a week later — on the day itself, people say "today".
"""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

# Sunday-based index: Sunday 0 … Saturday 6.
_EN_WEEKDAYS: dict[str, int] = {
    "sunday": 0, "sun": 0,
    "monday": 1, "mon": 1,
    "tuesday": 2, "tue": 2, "tues": 2,
    "wednesday": 3, "wed": 3, "weds": 3,
    "thursday": 4, "thu": 4, "thur": 4, "thurs": 4,
    "friday": 5, "fri": 5,
    "saturday": 6, "sat": 6,
}  # fmt: skip

_HE_WEEKDAYS: dict[str, int] = {
    "ראשון": 0,
    "שני": 1,
    "שלישי": 2,
    "רביעי": 3,
    "חמישי": 4,
    "שישי": 5,
    "שבת": 6,
}

_EN_MONTHS: dict[str, int] = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}  # fmt: skip

_HE_MONTHS: dict[str, int] = {
    "ינואר": 1,
    "פברואר": 2,
    "מרץ": 3,
    "מרס": 3,
    "אפריל": 4,
    "מאי": 5,
    "יוני": 6,
    "יולי": 7,
    "אוגוסט": 8,
    "ספטמבר": 9,
    "אוקטובר": 10,
    "נובמבר": 11,
    "דצמבר": 12,
}

_NUMBER_WORDS: dict[str, int] = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "couple": 2, "a couple": 2,
    "a couple of": 2,
    "אחד": 1, "שני": 2, "שניים": 2, "שלושה": 3, "שלוש": 3, "ארבעה": 4, "ארבע": 4,
    "חמישה": 5, "חמש": 5, "שישה": 6, "שש": 6, "שבעה": 7, "שבע": 7, "עשרה": 10, "עשר": 10,
}  # fmt: skip

#: How far in the past a month-and-day may fall before it is read as next year's. "Jan 5"
#: said in late December is next January; "Sep 20" said on Sep 23 is a deadline that has
#: just been missed, and saying so is more honest than moving it a year.
ROLL_FORWARD_DAYS = 60

# A Hebrew word may carry a one- or two-letter prefix: ו (and), ב (on/in), ל (to/for),
# ש (that), ה (the), and combinations such as וב / ול / שב.
_HE_PREFIX = r"(?:[ובלשה]{1,2})?"
_B = r"(?<![\w])"  # a word start that works for Hebrew as well as English
_E = r"(?![\w])"

_ISO_RE = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
# Day-first. Not after a Latin letter or a digit, so "v1.2" and "10:30" are not dates;
# a Hebrew prefix letter is allowed, because "ב-24.9" and "עד 24/9" are how it is written.
_NUMERIC_RE = re.compile(
    r"(?<![A-Za-z0-9_.:/])(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2}|\d{4}))?(?![\d:/]|\.\d)"
)
_EN_MONTH_NAMES = "|".join(sorted(_EN_MONTHS, key=len, reverse=True))
_HE_MONTH_NAMES = "|".join(sorted(_HE_MONTHS, key=len, reverse=True))
_MONTH_DAY_RE = re.compile(
    rf"{_B}(?P<month>{_EN_MONTH_NAMES})\.?\s+(?:the\s+)?(?P<day>\d{{1,2}})(?:st|nd|rd|th)?"
    rf"(?:,?\s+(?P<year>\d{{4}}))?(?!\d)",
    re.IGNORECASE,
)
_DAY_MONTH_RE = re.compile(
    rf"(?<!\d)(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?"
    rf"(?:{_HE_PREFIX})(?P<month>{_EN_MONTH_NAMES}|{_HE_MONTH_NAMES})\.?{_E}"
    rf"(?:,?\s+(?P<year>\d{{4}}))?",
    re.IGNORECASE,
)
_EN_WEEKDAY_RE = re.compile(
    rf"{_B}(?P<name>{'|'.join(sorted(_EN_WEEKDAYS, key=len, reverse=True))}){_E}",
    re.IGNORECASE,
)
_HE_WEEKDAY_RE = re.compile(
    rf"{_B}{_HE_PREFIX}(?:יום\s+)?(?P<name>{'|'.join(_HE_WEEKDAYS)}){_E}"
    # "שני ימים" is "two days", not Monday.
    r"(?!\s+(?:ימים|שבועות))"
)
_IN_N_RE = re.compile(
    r"(?:\b(?:in|within)\s+)?"
    r"(?P<n>\d{1,3}|a couple of|a couple|couple|an|a|one|two|three|four|five|six|seven|"
    r"eight|nine|ten)\s+(?P<unit>days?|weeks?|business days?|working days?)\b",
    re.IGNORECASE,
)
_HE_NUMBERS = "|".join(k for k in _NUMBER_WORDS if not k.isascii())
_HE_IN_N_RE = re.compile(
    rf"{_B}(?:בעוד|תוך)\s+(?:(?P<n>\d{{1,3}}|{_HE_NUMBERS})\s+)?"
    r"(?P<unit>ימים|יום|שבועות|שבוע|יומיים|שבועיים)" + _E
)
_FORTNIGHT_RE = re.compile(r"\b(?:in\s+)?a\s+fortnight\b", re.IGNORECASE)

_TODAY_RE = re.compile(
    rf"\b(?:today|tonight|this evening|eod|cob|end of (?:the )?day|close of business)\b"
    rf"|{_B}{_HE_PREFIX}(?:היום|הערב|סוף היום){_E}",
    re.IGNORECASE,
)
_TOMORROW_RE = re.compile(rf"\btomorrow\b|{_B}{_HE_PREFIX}מחר{_E}", re.IGNORECASE)
_DAY_AFTER_RE = re.compile(
    rf"\bday after tomorrow\b|{_B}{_HE_PREFIX}מחרתיים{_E}", re.IGNORECASE
)
_NEXT_WEEK_RE = re.compile(
    rf"\bnext week\b|\bend of next week\b|{_B}{_HE_PREFIX}שבוע הבא{_E}"
    rf"|{_B}{_HE_PREFIX}סוף השבוע הבא{_E}",
    re.IGNORECASE,
)
_THIS_WEEK_RE = re.compile(
    rf"\b(?:this week|end of (?:the |this )?week|eow|week'?s end)\b"
    rf"|{_B}{_HE_PREFIX}(?:השבוע|סוף השבוע|סוף שבוע){_E}",
    re.IGNORECASE,
)
_END_OF_MONTH_RE = re.compile(
    rf"\b(?:end of (?:the |this )?month|eom|month[- ]end|this month)\b"
    rf"|{_B}{_HE_PREFIX}(?:סוף החודש|החודש|סוף חודש){_E}",
    re.IGNORECASE,
)


def _sunday_index(day: date) -> int:
    return (day.weekday() + 1) % 7


def week_start(anchor: date) -> date:
    """The Sunday that begins the anchor's week."""
    return anchor - timedelta(days=_sunday_index(anchor))


def end_of_week(anchor: date) -> date:
    """Friday of the anchor's week; the anchor itself when that Friday has passed."""
    friday = week_start(anchor) + timedelta(days=5)
    return friday if friday >= anchor else anchor


def next_weekday(anchor: date, index: int) -> date:
    """The next day with this Sunday-based index, strictly after the anchor."""
    ahead = (index - _sunday_index(anchor)) % 7
    return anchor + timedelta(days=ahead or 7)


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _roll(candidate: date | None, anchor: date) -> date | None:
    """A month-and-day with no year: this year, unless that is long past."""
    if candidate is None:
        return None
    if candidate < anchor - timedelta(days=ROLL_FORWARD_DAYS):
        return _safe_date(candidate.year + 1, candidate.month, candidate.day)
    return candidate


def _year(text: str | None) -> int | None:
    if not text:
        return None
    value = int(text)
    return value + 2000 if value < 100 else value


def _explicit_dates(text: str, anchor: date) -> set[date]:
    found: set[date] = set()
    for match in _ISO_RE.finditer(text):
        parsed = _safe_date(int(match[1]), int(match[2]), int(match[3]))
        if parsed:
            found.add(parsed)
    stripped = _ISO_RE.sub(" ", text)
    for match in _NUMERIC_RE.finditer(stripped):
        day, month, year = int(match[1]), int(match[2]), _year(match[3])
        if year is not None:
            parsed = _safe_date(year, month, day)
        else:
            parsed = _roll(_safe_date(anchor.year, month, day), anchor)
        if parsed:
            found.add(parsed)
    for regex in (_MONTH_DAY_RE, _DAY_MONTH_RE):
        for match in regex.finditer(stripped):
            name = match["month"].casefold()
            named = _EN_MONTHS.get(name) or _HE_MONTHS.get(name)
            if not named:
                continue
            month, day = named, int(match["day"])
            year = _year(match["year"])
            parsed = (
                _safe_date(year, month, day)
                if year is not None
                else _roll(_safe_date(anchor.year, month, day), anchor)
            )
            if parsed:
                found.add(parsed)
    return found


def _weekdays(text: str) -> set[int]:
    names = {_EN_WEEKDAYS[m["name"].casefold()] for m in _EN_WEEKDAY_RE.finditer(text)}
    names |= {_HE_WEEKDAYS[m["name"]] for m in _HE_WEEKDAY_RE.finditer(text)}
    return names


def _business_days(anchor: date, count: int) -> date:
    """Count forward over Sunday–Thursday working days, the Israeli week."""
    day = anchor
    while count > 0:
        day += timedelta(days=1)
        if _sunday_index(day) <= 4:
            count -= 1
    return day


def _offsets(text: str, anchor: date) -> set[date]:
    """Relative deadlines: "in 3 days", "within two weeks", "בעוד שבוע"."""
    found: set[date] = set()
    for match in _IN_N_RE.finditer(text):
        raw = match["n"].casefold()
        count = int(raw) if raw.isdigit() else _NUMBER_WORDS.get(raw)
        if count is None:
            continue
        unit = match["unit"].casefold()
        if unit.startswith(("business", "working")):
            found.add(_business_days(anchor, count))
        else:
            found.add(anchor + timedelta(days=count * (7 if unit.startswith("week") else 1)))
    for match in _HE_IN_N_RE.finditer(text):
        unit, raw = match["unit"], match["n"]
        if unit in ("יומיים", "שבועיים"):
            found.add(anchor + timedelta(days=2 if unit == "יומיים" else 14))
            continue
        count = 1 if raw is None else (int(raw) if raw.isdigit() else _NUMBER_WORDS.get(raw))
        if count is None:
            continue
        found.add(anchor + timedelta(days=count * (7 if unit.startswith("שבוע") else 1)))
    if _FORTNIGHT_RE.search(text):
        found.add(anchor + timedelta(days=14))
    return found


def resolve_due(text: str | None, anchor: date) -> date | None:
    """The calendar date a spoken deadline means, said on ``anchor``; None if unsure.

    Specific beats vague: an explicit date or a weekday outranks "this week", which is
    why "this week, by Thursday" is Thursday and not Friday. Two different specific
    answers ("Tuesday or Wednesday") are not an answer at all.
    """
    if not text:
        return None
    clean = " ".join(str(text).split())
    if not clean:
        return None

    specific: set[date] = set(_explicit_dates(clean, anchor))

    next_week = bool(_NEXT_WEEK_RE.search(clean))
    for index in _weekdays(clean):
        if next_week:
            # "Thursday next week": that weekday in the week after this one, not the
            # first Thursday after today — said on a Monday, those differ by a week.
            specific.add(week_start(anchor) + timedelta(days=7 + index))
        else:
            specific.add(next_weekday(anchor, index))

    if _DAY_AFTER_RE.search(clean):
        specific.add(anchor + timedelta(days=2))
    elif _TOMORROW_RE.search(clean):
        specific.add(anchor + timedelta(days=1))
    specific |= _offsets(clean, anchor)
    # "Today" is also how a time of day is said — "EOD Thursday", "Thursday end of day" —
    # so it only names the day when nothing more specific did.
    if not specific and _TODAY_RE.search(clean):
        specific.add(anchor)

    if len(specific) == 1:
        return next(iter(specific))
    if len(specific) > 1:
        return None

    if next_week:
        return week_start(anchor) + timedelta(days=7 + 5)
    if _THIS_WEEK_RE.search(clean):
        return end_of_week(anchor)
    if _END_OF_MONTH_RE.search(clean):
        last = calendar.monthrange(anchor.year, anchor.month)[1]
        return anchor.replace(day=last)
    return None


def parse_iso_date(text: str | None) -> date | None:
    """A strict YYYY-MM-DD, or None. What the API and the model's ``due_at`` must be."""
    if not text or not isinstance(text, str):
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text.strip()):
        return None
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        return None


def anchor_date(started_at: str | None) -> date | None:
    """The local calendar day a meeting happened on: what its deadlines count from."""
    if not started_at:
        return None
    from datetime import datetime

    try:
        when = datetime.fromisoformat(started_at)
    except ValueError:
        return None
    return (when.astimezone() if when.tzinfo else when).date()
