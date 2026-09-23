"""The due-date resolver: what "by Thursday" means, in English and Hebrew (D50).

The rule the whole module is built on is tested as hard as the phrases are: anything
unrecognised or ambiguous is None. A wrong date is worse than none, because the inbox
sorts and flags by it and the reader believes the flag.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.due import anchor_date, end_of_week, next_weekday, parse_iso_date, resolve_due, week_start

# Wednesday 23 September 2026. The week runs Sunday 20 to Saturday 26.
WED = date(2026, 9, 23)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Days
        ("today", date(2026, 9, 23)),
        ("tonight", date(2026, 9, 23)),
        ("EOD", date(2026, 9, 23)),
        ("by end of day", date(2026, 9, 23)),
        ("tomorrow", date(2026, 9, 24)),
        ("tomorrow morning", date(2026, 9, 24)),
        ("day after tomorrow", date(2026, 9, 25)),
        # Weekdays: the next occurrence strictly after the meeting day
        ("Thursday", date(2026, 9, 24)),
        ("by Thursday", date(2026, 9, 24)),
        ("Thu", date(2026, 9, 24)),
        ("thurs", date(2026, 9, 24)),
        ("next Thursday", date(2026, 9, 24)),
        ("Sunday", date(2026, 9, 27)),
        ("Monday", date(2026, 9, 28)),
        ("Tuesday", date(2026, 9, 29)),
        ("Wednesday", date(2026, 9, 30)),  # said on a Wednesday: a week later
        ("EOD Thursday", date(2026, 9, 24)),  # a time of day does not make it two days
        ("Thursday next week", date(2026, 10, 1)),
        # Weeks and months
        ("this week", date(2026, 9, 25)),
        ("end of week", date(2026, 9, 25)),
        ("by the end of the week", date(2026, 9, 25)),
        ("EOW", date(2026, 9, 25)),
        ("next week", date(2026, 10, 2)),
        ("end of next week", date(2026, 10, 2)),
        ("end of month", date(2026, 9, 30)),
        ("EOM", date(2026, 9, 30)),
        # Relative
        ("in 3 days", date(2026, 9, 26)),
        ("within two weeks", date(2026, 10, 7)),
        ("in a week", date(2026, 9, 30)),
        ("in a fortnight", date(2026, 10, 7)),
        ("3 business days", date(2026, 9, 28)),  # Thu, Sun, Mon: the Israeli week
        # Explicit dates
        ("2026-10-01", date(2026, 10, 1)),
        ("Sep 24", date(2026, 9, 24)),
        ("24 Sep", date(2026, 9, 24)),
        ("September 24", date(2026, 9, 24)),
        ("Sept 24th", date(2026, 9, 24)),
        ("the 24th of September", date(2026, 9, 24)),
        ("24/9", date(2026, 9, 24)),  # day-first
        ("24.9", date(2026, 9, 24)),
        ("1/10", date(2026, 10, 1)),
        ("24/9/2027", date(2027, 9, 24)),
        ("24.9.27", date(2027, 9, 24)),
        ("Jan 5", date(2027, 1, 5)),  # long past this year: next year's
        ("Sep 20", date(2026, 9, 20)),  # just missed: said so, not moved a year
        # Hebrew
        ("היום", date(2026, 9, 23)),
        ("הערב", date(2026, 9, 23)),
        ("עד סוף היום", date(2026, 9, 23)),
        ("מחר", date(2026, 9, 24)),
        ("מחרתיים", date(2026, 9, 25)),
        ("יום ראשון", date(2026, 9, 27)),
        ("יום שני", date(2026, 9, 28)),
        ("יום שלישי", date(2026, 9, 29)),
        ("יום רביעי", date(2026, 9, 30)),
        ("יום חמישי", date(2026, 9, 24)),
        ("יום שישי", date(2026, 9, 25)),
        ("יום שבת", date(2026, 9, 26)),
        ("ראשון", date(2026, 9, 27)),
        ("שני", date(2026, 9, 28)),
        ("שלישי", date(2026, 9, 29)),
        ("רביעי", date(2026, 9, 30)),
        ("חמישי", date(2026, 9, 24)),
        ("שישי", date(2026, 9, 25)),
        ("שבת", date(2026, 9, 26)),
        ("ביום חמישי", date(2026, 9, 24)),
        ("עד יום חמישי", date(2026, 9, 24)),
        ("עד חמישי", date(2026, 9, 24)),
        ("השבוע", date(2026, 9, 25)),
        ("עד סוף השבוע", date(2026, 9, 25)),
        ("שבוע הבא", date(2026, 10, 2)),
        ("בשבוע הבא", date(2026, 10, 2)),
        ("סוף החודש", date(2026, 9, 30)),
        ("בעוד 3 ימים", date(2026, 9, 26)),
        ("בעוד שבוע", date(2026, 9, 30)),
        ("בעוד יומיים", date(2026, 9, 25)),
        ("בעוד שני ימים", date(2026, 9, 25)),  # "two days", not Monday
        ("24 בספטמבר", date(2026, 9, 24)),
        ("ב-24.9", date(2026, 9, 24)),
    ],
)
def test_resolves(text: str, expected: date) -> None:
    assert resolve_due(text, WED) == expected


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "   ",
        "asap",
        "soon",
        "when possible",
        "before the launch",
        "Q4",
        "next month",  # a month is not a deadline; guessing its last day would be a guess
        "Tuesday or Wednesday",  # two answers are not an answer
        "tomorrow or Friday",
        "v1.2",
        "at 10:30",
        "31/2",  # no such day
        "2026-13-01",
        "בהקדם",
    ],
)
def test_never_guesses(text: str | None) -> None:
    assert resolve_due(text, WED) is None


def test_specific_beats_vague() -> None:
    """"This week, by Thursday" means Thursday: the weekday is the more specific part."""
    assert resolve_due("this week, by Thursday", WED) == date(2026, 9, 24)


def test_end_of_week_late_in_the_week() -> None:
    """Weeks start on Sunday. On Friday the week ends today; on Saturday, too."""
    assert resolve_due("this week", date(2026, 9, 25)) == date(2026, 9, 25)
    assert resolve_due("this week", date(2026, 9, 26)) == date(2026, 9, 26)
    assert resolve_due("this week", date(2026, 9, 20)) == date(2026, 9, 25)


def test_next_week_from_a_saturday() -> None:
    assert resolve_due("next week", date(2026, 9, 26)) == date(2026, 10, 2)


def test_end_of_month_in_february() -> None:
    assert resolve_due("end of month", date(2027, 2, 10)) == date(2027, 2, 28)


def test_week_helpers() -> None:
    assert week_start(WED) == date(2026, 9, 20)
    assert end_of_week(WED) == date(2026, 9, 25)
    assert next_weekday(WED, 3) == date(2026, 9, 30)
    assert next_weekday(WED, 4) == date(2026, 9, 24)


def test_parse_iso_date_is_strict() -> None:
    assert parse_iso_date("2026-09-24") == date(2026, 9, 24)
    for bad in (None, "", "2026-9-24", "24/9/2026", "2026-02-30", "tomorrow", "2026-09-24T10:00"):
        assert parse_iso_date(bad) is None


def test_anchor_date() -> None:
    assert anchor_date("2026-09-23T14:00:00") == date(2026, 9, 23)
    assert anchor_date("2026-09-23T14:00:00+03:00") is not None
    assert anchor_date(None) is None
    assert anchor_date("not a date") is None
