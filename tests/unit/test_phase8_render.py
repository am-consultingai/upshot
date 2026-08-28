from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

import pytest

from app.pipeline.stages.render import direction_for, plaintext, render_html, stylesheet

NOTES_HE: dict[str, Any] = {
    "title": "סטטוס שבועי",
    "tldr": ["דחינו את הרילי‏ס לשבוע הבא", "יש באג פתוח ב-migration"],
    "participants": [{"name": "ME", "track": "ME"}, {"name": "Dana Levi", "track": "THEM"}],
    "topics": [
        {
            "heading": "Deployment",
            "points": ["השירות הועבר ל-Kubernetes", "ה-migration עדיין נכשל"],
            "quotes": [{"who": "THEM", "text": "אז נדחה את זה לשבוע הבא", "at_ms": 65000}],
        }
    ],
    "decisions": [
        {
            "what": "לדחות את הרילי‏ס",
            "rationale": "יש באג פתוח",
            "who_decided": "ME",
            "at_ms": 65000,
        }
    ],
    "action_items": [
        {"who": "ME", "what": "לעדכן ביום חמישי", "due": "2026-09-03", "confidence": 0.9}
    ],
    "open_questions": ["מי בודק את ה-migration?"],
    "risks": ["הזמן קצר"],
    "follow_up_email": {"subject": "סיכום פגישה", "body_md": "- נדחה"},
}

NOTES_EN: dict[str, Any] = {
    "title": "Weekly sync",
    "tldr": ["We delayed the release", "The migration bug is still open"],
    "participants": [{"name": "ME", "track": "ME"}],
    "topics": [
        {
            "heading": "Deployment",
            "points": ["Service moved to Kubernetes"],
            "quotes": [{"who": "THEM", "text": "let us delay to next week", "at_ms": 65000}],
        }
    ],
    "decisions": [
        {"what": "Delay the release", "rationale": "open bug", "who_decided": "ME", "at_ms": 65000}
    ],
    "action_items": [{"who": "ME", "what": "update on Thursday", "due": None, "confidence": 0.9}],
    "open_questions": [],
    "risks": [],
    "follow_up_email": {"subject": "Notes", "body_md": "- delayed"},
}

MEETING = {"started_at": "2026-08-28T14:00:00+03:00", "duration_s": 2820}


class Elements(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))


def parse(html: str) -> Elements:
    parser = Elements()
    parser.feed(html)
    return parser


def test_render_golden_ui(golden) -> None:  # type: ignore[no-untyped-def]
    golden("summary_he.html", render_html(NOTES_HE, language="he", meeting=MEETING).ui)


def test_render_golden_email(golden) -> None:  # type: ignore[no-untyped-def]
    golden("summary_he.email.html", render_html(NOTES_HE, language="he", meeting=MEETING).email)


def test_render_golden_ui_ltr(golden) -> None:  # type: ignore[no-untyped-def]
    golden("summary_en.html", render_html(NOTES_EN, language="en", meeting=MEETING).ui)


def test_render_golden_email_ltr(golden) -> None:  # type: ignore[no-untyped-def]
    golden("summary_en.email.html", render_html(NOTES_EN, language="en", meeting=MEETING).email)


def test_email_has_no_style_block() -> None:
    email = render_html(NOTES_HE, language="he", meeting=MEETING).email
    assert "<style" not in email
    skip = {"html", "head", "meta", "title", "br", "body"}
    visible = [(tag, attrs) for tag, attrs in parse(email).tags if tag not in skip]
    assert visible
    for tag, attrs in visible:
        assert "style" in attrs, f"<{tag}> has no inlined style"
    body = next(attrs for tag, attrs in parse(email).tags if tag == "body")
    assert body.get("style")


def test_no_external_resources() -> None:
    for html in (
        render_html(NOTES_HE, language="he", meeting=MEETING).ui,
        render_html(NOTES_HE, language="he", meeting=MEETING).email,
    ):
        assert not re.search(r'(src|href)\s*=\s*"(https?:)?//', html)
        assert "http://" not in html and "https://" not in html
        assert "@import" not in html


def test_rtl_attributes() -> None:
    html = render_html(NOTES_HE, language="he", meeting=MEETING).ui
    assert '<html dir="rtl" lang="he">' in html
    email = render_html(NOTES_HE, language="he", meeting=MEETING).email
    quotes = [attrs for tag, attrs in parse(email).tags if tag == "blockquote"]
    assert quotes, "the mixed Hebrew/English quote is rendered"
    assert "unicode-bidi:plaintext" in (quotes[0].get("style") or "").replace(" ", "")


def test_ltr_attributes() -> None:
    html = render_html(NOTES_EN, language="en", meeting=MEETING).ui
    assert '<html dir="ltr" lang="en">' in html
    assert "TL;DR" in html


def test_direction_follows_summary_not_meeting() -> None:
    """A Hebrew meeting summarised into English is an LTR document."""
    html = render_html(NOTES_EN, language="en", meeting={**MEETING, "language": "he"}).ui
    assert 'dir="ltr"' in html
    assert direction_for("he") == "rtl" and direction_for("en") == "ltr"


def test_no_physical_css_properties() -> None:
    css = stylesheet()
    banned = (
        "padding-left",
        "padding-right",
        "margin-left",
        "margin-right",
        "border-left",
        "border-right",
        "text-align: left",
        "text-align: right",
        "left:",
        "right:",
    )
    for token in banned:
        assert token not in css, f"{token} is physical; use the logical property"
    assert "padding-inline" in css and "margin-inline" in css and "text-align: start" in css


def test_at_ms_present() -> None:
    for notes in (NOTES_HE, NOTES_EN):
        rendered = render_html(notes, language="en", meeting=MEETING)
        for html in (rendered.ui, rendered.email):
            assert html.count('data-at-ms="65000"') == 2, "the quote and the decision both seek"


def test_at_ms_absent_when_unknown() -> None:
    notes = {
        **NOTES_EN,
        "decisions": [{"what": "no timestamp", "who_decided": "ME"}],
        "topics": [{"heading": "H", "points": ["p"], "quotes": [{"who": "ME", "text": "q"}]}],
    }
    assert "data-at-ms" not in render_html(notes, language="en").ui


def test_empty_sections_render() -> None:
    notes = {
        "title": "Empty",
        "tldr": ["a", "b"],
        "topics": [],
        "decisions": [],
        "action_items": [],
    }
    html = render_html(notes, language="en").ui
    assert html.count("Nothing recorded.") == 3


def test_plaintext_alternative() -> None:
    text = plaintext(NOTES_EN, "en")
    assert "Weekly sync" in text
    assert "- We delayed the release" in text
    assert "update on Thursday" in text
    assert "<" not in text


@pytest.mark.parametrize("language", ["he", "en"])
def test_labels_are_localised(language: str) -> None:
    html = render_html(NOTES_EN, language=language).ui
    expected = "תקציר" if language == "he" else "TL;DR"
    assert expected in html
