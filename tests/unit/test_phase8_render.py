"""Rendering a free-form summary.

The template that used to lay out nine fixed fields is gone, and with it the golden
files and the tests that pinned its markup. What is left to check is narrower and, for a
page that shows model-authored HTML, more important: that the document survives intact,
that the two things which turn a document into code do not, and that direction still
follows the summary's language.
"""

from __future__ import annotations

from typing import Any

from app.pipeline.stages.render import direction_for, plaintext, render_free, sanitize

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
