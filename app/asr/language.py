"""Which language a text is in, by the script it is written in.

A recorded meeting's language is decided from its audio before transcription
(``app/asr/classify.py``, D80). This is only for text that arrives without audio: an
imported transcript whose caller did not say what language it is in.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.asr.models import DEFAULT_LANGUAGE

HEBREW_LETTER = re.compile(r"[א-ת]")
LATIN_LETTER = re.compile(r"[A-Za-z]")

#: Hebrew wins at this share of the letters. Low on purpose: a Hebrew meeting is full of
#: English product names and terms ("ה-deployment", "sprint"), and Hebrew spends fewer
#: letters per word than English, so even an evenly mixed meeting lands near 0.4. A
#: meeting that is mostly Hebrew, or mixed, is summarized in Hebrew.
HEBREW_SHARE = 0.25


@dataclass(frozen=True)
class LanguageDecision:
    language: str
    #: The share of the transcript's letters in that language's script; 0 with no text.
    confidence: float
    source: str = "transcript"  # transcript|default


def spoken_language(texts: Iterable[str]) -> LanguageDecision:
    """Hebrew or English, by which script the transcript is written in."""
    hebrew = latin = 0
    for text in texts:
        hebrew += len(HEBREW_LETTER.findall(text))
        latin += len(LATIN_LETTER.findall(text))
    total = hebrew + latin
    if total == 0:
        return LanguageDecision(DEFAULT_LANGUAGE, 0.0, "default")
    share = hebrew / total
    if share >= HEBREW_SHARE:
        return LanguageDecision("he", round(share, 3))
    return LanguageDecision("en", round(1 - share, 3))
