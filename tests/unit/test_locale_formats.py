"""Dates and times as Windows shows them, and a meeting's default name."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from app.locale_formats import Formats, default_meeting_title, format_pattern

WHEN = datetime(2026, 9, 30, 14, 5, 9)  # a Wednesday


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("dd/MM/yyyy", "30/09/2026"),
        ("M/d/yyyy", "9/30/2026"),
        ("yyyy-MM-dd", "2026-09-30"),
        ("dd.MM.yy", "30.09.26"),
        ("HH:mm", "14:05"),
        ("h:mm tt", "2:05 PM"),
        ("hh:mm:ss", "02:05:09"),
        ("dddd d MMMM", "Wednesday 30 September"),
        ("'Week of' d/M", "Week of 30/9"),
    ],
)
def test_windows_patterns(pattern: str, expected: str) -> None:
    assert format_pattern(WHEN, pattern) == expected


def test_the_default_name_is_weekday_date_and_time() -> None:
    assert default_meeting_title(WHEN, "en", Formats("dd/MM/yyyy", "HH:mm")) == (
        "Wednesday 30/09/2026 14:05"
    )
    assert default_meeting_title(WHEN, "en", Formats("M/d/yyyy", "h:mm tt")) == (
        "Wednesday 9/30/2026 2:05 PM"
    )
    assert default_meeting_title(WHEN, "he", Formats("dd/MM/yyyy", "HH:mm")) == (
        "יום רביעי 30/09/2026 14:05"
    )


@pytest.mark.parametrize(
    ("language", "pattern", "expected"),
    [
        ("de", "dddd, d. MMMM", "Mittwoch, 30. September"),
        ("es", "dddd d 'de' MMMM", "Miércoles 30 de septiembre"),
        ("fr", "dddd d MMMM", "Mercredi 30 septembre"),
    ],
)
def test_every_interface_language_names_days_and_months(
    language: str, pattern: str, expected: str
) -> None:
    assert format_pattern(WHEN, pattern, language) == expected


def test_every_interface_language_has_day_and_month_names() -> None:
    from app.config import _ENUMS
    from app.locale_formats import MONTHS, WEEKDAYS

    for language in _ENUMS["ui.language"]:
        assert len(WEEKDAYS[language]) == 7
        assert len(MONTHS[language]) == 12


def test_seconds_never_reach_the_name() -> None:
    assert default_meeting_title(WHEN, "en", Formats("dd/MM/yyyy", "HH:mm:ss")).endswith(" 14:05")


def test_an_unnamed_meeting_gets_it_and_a_number_on_a_clash(tmp_path: Path) -> None:
    from app.clock import FakeClock
    from tests.fixtures.meetings import harness

    h = harness(tmp_path)
    service = h.meetings if hasattr(h, "meetings") else None
    if service is None:
        from app.meetings import MeetingService

        service = MeetingService(h.config, h.dao, h.queue, clock=h.clock)
    assert isinstance(h.clock, FakeClock)
    first = service.create(source="manual")
    second = service.create(source="manual")
    third = service.create(source="manual")
    assert first.title_source == "default"
    assert second.title == f"{first.title} (2)"
    assert third.title == f"{first.title} (3)"
    named = service.create(source="manual", title="Budget review")
    assert (named.title, named.title_source) == ("Budget review", None)


def test_anything_else_replaces_the_default_name() -> None:
    from app.meetings import AUTOMATIC_TITLES, DEFAULT_TITLE

    assert DEFAULT_TITLE in AUTOMATIC_TITLES, "a calendar match or the summary may rename it"


@pytest.mark.parametrize(
    ("langid", "language"),
    [(0x0409, "en"), (0x0809, "en"), (0x040D, "he"), (0x0407, "de"), (0x0C0A, "es"),
     (0x080A, "es"), (0x040C, "fr"), (0x0C0C, "fr"), (0x0410, None), (0x0419, None)],
)  # fmt: skip
def test_a_windows_language_maps_to_its_interface_language(
    langid: int, language: str | None
) -> None:
    from app.locale_formats import language_for_langid

    assert language_for_langid(langid) == language
