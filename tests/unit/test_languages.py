"""Every Whisper language, by name and direction (story D)."""

from __future__ import annotations

import pytest

from app.asr.languages import LANGUAGES, RTL_LANGUAGES, direction_for, english_name
from app.pipeline.stages import render
from app.pipeline.stages.summarize import language_instruction


def test_the_table_covers_every_faster_whisper_language() -> None:
    from faster_whisper.tokenizer import _LANGUAGE_CODES

    assert set(LANGUAGES) == set(_LANGUAGE_CODES)
    assert all(language.name and language.native for language in LANGUAGES.values())


@pytest.mark.parametrize(
    ("code", "name"),
    [("es", "Spanish"), ("ar", "Arabic"), ("zh", "Chinese"), ("ja", "Japanese"), ("he", "Hebrew")],
)
def test_the_notes_are_asked_for_by_the_languages_name(code: str, name: str) -> None:
    assert f"Write the notes in {name} ({code})." in language_instruction(code)


def test_an_unknown_code_is_passed_as_it_is() -> None:
    assert "Write the notes in xx (xx)." in language_instruction("xx")
    assert english_name("pt-BR") == "Portuguese"


@pytest.mark.parametrize("code", ["he", "ar", "fa", "ur", "yi", "ps", "sd"])
def test_right_to_left(code: str) -> None:
    assert direction_for(code) == "rtl"
    assert render.direction_for(code) == "rtl"


@pytest.mark.parametrize("code", ["en", "es", "ru", "zh", "ja", "hi", "am"])
def test_left_to_right(code: str) -> None:
    assert direction_for(code) == "ltr"


def test_no_language_is_left_to_right() -> None:
    assert direction_for(None) == "ltr" and direction_for("") == "ltr"


def test_the_rtl_list_is_all_whisper_languages() -> None:
    assert set(LANGUAGES) >= RTL_LANGUAGES
