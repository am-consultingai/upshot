"""Which language a meeting was in: read from its transcript, not asked of the model (D60).

The speech model is always told the language is Hebrew (``models.ASR_LANGUAGE``). That is
how ivrit-ai large-v3 writes English speech as English; told "English" it drifts into
Hebrew nobody said, and its own language detection answers Hebrew for every input. So
the question of what language a meeting was in is no longer put to the model before
transcription. It is answered afterwards, from the words that came out.

The answer decides the summary's language when ``summary.language`` is ``auto``, and the
direction the page renders in.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.asr.models import ASR_LANGUAGE

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
        return LanguageDecision(ASR_LANGUAGE, 0.0, "default")
    share = hebrew / total
    if share >= HEBREW_SHARE:
        return LanguageDecision("he", round(share, 3))
    return LanguageDecision("en", round(1 - share, 3))
