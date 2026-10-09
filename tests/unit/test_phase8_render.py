"""Rendering a free-form summary.

The template that used to lay out nine fixed fields is gone, and with it the golden
files and the tests that pinned its markup. What is left to check is narrower and, for a
page that shows model-authored HTML, more important: that the document survives intact,
that the two things which turn a document into code do not, and that direction still
follows the summary's language.
"""

from __future__ import annotations

from typing import Any

from app.pipeline.stages.render import (
    cite_moments,
    direction_for,
    plaintext,
    render_free,
    sanitize,
)

DESIGNED: dict[str, Any] = {
    "title": "סטטוס שבועי",
    "summary_html": (
        '<section class="card" style="padding:12px">'
        "<h1>סטטוס שבועי</h1>"
        '<table style="width:100%"><tr><td>Dana</td><td>migration</td></tr></table>'
        "<p>נדחה לשבוע הבא</p>"
        "</section>"
    ),
}


def test_the_document_is_passed_through_as_written() -> None:
    """The whole point of free-form: nothing here re-lays-out what the prompt produced."""
    page = render_free(DESIGNED, language="he").ui
    assert '<section class="card" style="padding:12px">' in page
    assert '<table style="width:100%">' in page, "layout and inline CSS survive"
    assert "<h1>סטטוס שבועי</h1>" in page


def test_only_script_and_handlers_are_removed() -> None:
    """This HTML is opened in a browser, so exactly two things come out and no more."""
    dirty = '<p onclick="steal()">a</p><script>alert(1)</script><b style="color:red">b</b>'
    clean = sanitize(dirty)
    assert "<script" not in clean and "alert(1)" not in clean
    assert "onclick" not in clean
    assert '<b style="color:red">b</b>' in clean, "styling is not a security concern"


def test_direction_follows_the_summary_language() -> None:
    assert direction_for("he") == "rtl"
    assert direction_for("en") == "ltr"
    assert 'dir="rtl"' in render_free(DESIGNED, language="he").ui
    assert 'dir="ltr"' in render_free(DESIGNED, language="en").ui


def test_plaintext_strips_markup_for_the_email_alternative() -> None:
    """No structure left to walk, so the text part is the markup stripped out."""
    text = plaintext(DESIGNED, "he")
    assert "<" not in text and ">" not in text
    assert "סטטוס שבועי" in text and "נדחה לשבוע הבא" in text


def test_ui_and_email_are_the_same_document() -> None:
    """There is no separate drafted email any more — the summary is the email."""
    rendered = render_free(DESIGNED, language="he")
    assert rendered.ui == rendered.email


STARTS = [0, 4_200, 61_500, 125_000]


def cited(html: str, *, duration_ms: int | None = 180_000) -> str:
    return cite_moments(html, starts=STARTS, duration_ms=duration_ms)


def test_a_citation_snaps_back_to_the_start_of_the_turn_it_falls_in() -> None:
    assert cited('<li data-at-ms="62000">x</li>') == '<li data-at-ms="61500">x</li>'
    assert cited("<li data-at-ms='125000'>x</li>") == '<li data-at-ms="125000">x</li>'
    assert cited('<p class="a" data-at-ms=4300>x</p>') == '<p class="a" data-at-ms="4200">x</p>'


def test_a_citation_past_the_last_turn_lands_on_the_last_turn() -> None:
    assert cited('<li data-at-ms="170000">x</li>') == '<li data-at-ms="125000">x</li>'


def test_a_citation_that_is_not_a_whole_number_is_dropped() -> None:
    for value in ("soon", "12:34", "61.5", "", "1e5"):
        assert cited(f'<li data-at-ms="{value}">x</li>') == "<li>x</li>"


def test_a_negative_citation_is_dropped() -> None:
    assert cited('<li data-at-ms="-1">x</li>') == "<li>x</li>"


def test_a_citation_beyond_the_end_of_the_meeting_is_dropped() -> None:
    assert cited('<li data-at-ms="180001">x</li>') == "<li>x</li>"
    assert cited('<li data-at-ms="180000">x</li>') == '<li data-at-ms="125000">x</li>'


def test_without_a_transcript_a_valid_citation_is_kept_as_written() -> None:
    html = '<li data-at-ms="62000">x</li>'
    assert cite_moments(html, starts=None, duration_ms=None) == html


def test_a_summary_without_citations_renders_exactly_as_before() -> None:
    plain = render_free(DESIGNED, language="he").ui
    assert render_free(DESIGNED, language="he", starts=STARTS, duration_ms=1000).ui == plain


def test_text_that_mentions_the_attribute_is_left_alone() -> None:
    html = "<p>we discussed data-at-ms=5 in the markup</p>"
    assert cited(html) == html


def test_render_checks_every_citation_in_the_page() -> None:
    notes = {"summary_html": '<ul><li data-at-ms="62000">a</li><li data-at-ms="x">b</li></ul>'}
    page = render_free(notes, language="en", starts=STARTS, duration_ms=180_000).ui
    assert '<li data-at-ms="61500">a</li><li>b</li>' in page
