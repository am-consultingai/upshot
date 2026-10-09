"""Rendering a free-form summary.

The template that used to lay out nine fixed fields is gone, and with it the golden
files and the tests that pinned its markup. What is left to check is narrower and, for a
page that shows model-authored HTML, more important: that the document survives intact,
that nothing outside the allowlist does (code, frames, outside requests), and that
direction still follows the summary's language.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.pipeline.stages.render import (
    UI_NAME,
    cite_moments,
    direction_for,
    plaintext,
    read_summary,
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


def test_script_and_handlers_are_removed() -> None:
    dirty = '<p onclick="steal()">a</p><script>alert(1)</script><b style="color:red">b</b>'
    clean = sanitize(dirty)
    assert "<script" not in clean and "alert(1)" not in clean
    assert "onclick" not in clean
    assert '<b style="color:red">b</b>' in clean, "styling is not a security concern"


@pytest.mark.parametrize(
    "dirty",
    [
        "<img/src=x/onerror=alert(1)>",
        '<a href="javascript:alert(1)">x</a>',
        '<iframe src="https://example.com"></iframe>',
        '<img src="https://example.com/t.gif">',
    ],
)
def test_the_known_bypasses_come_out(dirty: str) -> None:
    """Each of these survived the regexes that used to be the only cleaning."""
    clean = sanitize(dirty)
    for gone in ("onerror", "alert", "javascript:", "<iframe", "<img", "example.com"):
        assert gone not in clean


def test_a_link_keeps_its_text_and_only_an_in_page_href() -> None:
    assert sanitize('<a href="https://example.com" target="_blank">x</a>') == "<a>x</a>"
    assert sanitize('<a href="#t-90">jump</a>') == '<a href="#t-90">jump</a>'


def test_frames_forms_and_foreign_markup_go_with_their_content() -> None:
    dirty = (
        "<object data=x>o</object><embed src=x><svg><text>s</text></svg><math>m</math>"
        "<style>p{}</style><form><input value=1></form><meta http-equiv=refresh>"
        "<link rel=stylesheet href=x><base href=x><p>kept</p>"
    )
    assert sanitize(dirty) == "<p>kept</p>"


def test_the_designed_structure_survives_intact() -> None:
    designed = (
        '<section dir="rtl" lang="he" class="card" title="t">'
        '<table><thead><tr><th scope="col" colspan="2">h</th></tr></thead>'
        '<tbody><tr><td rowspan="2" style="padding:4px">d</td></tr></tbody></table>'
        '<ul><li data-at-ms="61000">said at 1:01</li></ul>'
        '<details open=""><summary>more</summary><time datetime="2026-10-09">today</time>'
        "</details></section>"
    )
    assert sanitize(designed) == designed


def test_css_that_makes_a_request_is_dropped_and_the_rest_kept() -> None:
    dirty = (
        '<p style="color:red; background:url(https://example.com/t.gif); margin:0;'
        " width:expression(alert(1)); b\\61ckground:u\\72l(x); x:@import 'y'\">p</p>"
    )
    assert sanitize(dirty) == '<p style="color:red; margin:0">p</p>'


def test_a_summary_written_before_the_allowlist_is_cleaned_when_read(tmp_path: Path) -> None:
    """Files on disk were made with the old regexes; the API cleans them on the way out."""
    from tests.fixtures.api import build_harness

    api = build_harness(tmp_path)
    svc = api.services
    folder = svc.config.data_root / "m-old"
    svc.dao.insert_meeting(meeting_id="m-old", folder=folder, source="manual")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / UI_NAME).write_text(
        '<div dir="ltr"><p>notes</p><img/src=x/onerror=alert(1)></div>', encoding="utf-8"
    )
    served = api.client().get("/api/meetings/m-old/summary.html")
    assert served.status_code == 200
    assert served.text == '<div dir="ltr"><p>notes</p></div>'
    assert read_summary(folder / UI_NAME) == served.text
    assert "default-src 'none'" in served.headers["content-security-policy"]


def test_the_app_shell_forbids_inline_script(tmp_path: Path) -> None:
    from app.main import SHELL_CSP, shell

    page = tmp_path / "index.html"
    page.write_text("<!doctype html>", encoding="utf-8")
    policy = shell(page).headers["content-security-policy"]
    assert policy == SHELL_CSP
    assert "script-src 'self'" in policy and "unsafe-inline" not in policy


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
