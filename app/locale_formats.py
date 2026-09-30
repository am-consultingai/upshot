"""Dates and times the way Windows shows them to this user.

Windows keeps the user's choices under ``HKCU\\Control Panel\\International``: the short
date (``sShortDate``, e.g. ``dd/MM/yyyy`` or ``M/d/yyyy``) and the short time
(``sShortTime``, e.g. ``HH:mm`` or ``h:mm tt``). The meeting's default name and the
meeting-details times follow them, so Upshot writes a date the way the rest of the
machine does. Elsewhere (development), ``dd/MM/yyyy`` and ``HH:mm``.

The patterns are .NET-style: ``d dd ddd dddd M MM MMM MMMM y yy yyyy h hh H HH m mm s ss
t tt``, with text in single quotes taken literally.
"""

from __future__ import annotations

import functools
import re
import sys
from dataclasses import dataclass
from datetime import datetime

DEFAULT_SHORT_DATE = "dd/MM/yyyy"
DEFAULT_SHORT_TIME = "HH:mm"

WEEKDAYS = {
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    "he": ("יום שני", "יום שלישי", "יום רביעי", "יום חמישי", "יום שישי", "שבת", "יום ראשון"),
}
MONTHS = {
    "en": ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"),
    "he": ("ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי", "אוגוסט",
           "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"),
}  # fmt: skip

_TOKEN = re.compile(r"'[^']*'|d{1,4}|M{1,4}|y{1,5}|h{1,2}|H{1,2}|m{1,2}|s{1,2}|t{1,2}")


@dataclass(frozen=True)
class Formats:
    short_date: str = DEFAULT_SHORT_DATE
    short_time: str = DEFAULT_SHORT_TIME

    def as_dict(self) -> dict[str, str]:
        return {"short_date": self.short_date, "short_time": self.short_time}


@functools.cache
def windows_formats() -> Formats:
    """The user's short date and time patterns. Read once; the defaults off Windows."""
    if sys.platform != "win32":
        return Formats()
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\International") as key:

            def read(name: str, default: str) -> str:
                try:
                    return str(winreg.QueryValueEx(key, name)[0]) or default
                except OSError:
                    return default

            return Formats(
                read("sShortDate", DEFAULT_SHORT_DATE), read("sShortTime", DEFAULT_SHORT_TIME)
            )
    except OSError:
        return Formats()


def format_pattern(when: datetime, pattern: str, language: str = "en") -> str:
    """``when`` written with a Windows (.NET-style) pattern."""
    days = WEEKDAYS.get(language, WEEKDAYS["en"])
    months = MONTHS.get(language, MONTHS["en"])
    hour12 = when.hour % 12 or 12

    def one(token: str) -> str:
        if token.startswith("'"):
            return token[1:-1]
        return {
            "d": str(when.day), "dd": f"{when.day:02d}",
            "ddd": days[when.weekday()][:3], "dddd": days[when.weekday()],
            "M": str(when.month), "MM": f"{when.month:02d}",
            "MMM": months[when.month - 1][:3], "MMMM": months[when.month - 1],
            "y": str(when.year % 100), "yy": f"{when.year % 100:02d}",
            "yyy": str(when.year), "yyyy": str(when.year), "yyyyy": f"{when.year:05d}",
            "h": str(hour12), "hh": f"{hour12:02d}",
            "H": str(when.hour), "HH": f"{when.hour:02d}",
            "m": str(when.minute), "mm": f"{when.minute:02d}",
            "s": str(when.second), "ss": f"{when.second:02d}",
            "t": "AP"[when.hour >= 12], "tt": ("AM", "PM")[when.hour >= 12],
        }.get(token, token)  # fmt: skip

    out, at = [], 0
    for match in _TOKEN.finditer(pattern):
        out.append(pattern[at : match.start()])
        out.append(one(match.group(0)))
        at = match.end()
    out.append(pattern[at:])
    return "".join(out)


def default_meeting_title(
    when: datetime, language: str = "en", formats: Formats | None = None
) -> str:
    """ "Tuesday 30/09/2026 14:05": the weekday, then the date and time as Windows shows
    them. Always hours and minutes, whatever seconds the short time pattern carries."""
    formats = formats or windows_formats()
    days = WEEKDAYS.get(language, WEEKDAYS["en"])
    time_pattern = re.sub(r"[:.]?s{1,2}", "", formats.short_time)
    date = format_pattern(when, formats.short_date, language)
    time = format_pattern(when, time_pattern, language)
    return f"{days[when.weekday()]} {date} {time}"
