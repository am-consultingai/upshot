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
LATIN_LETTER = re.compile(r"[A-Za-zÀ-ɏ]")
ARABIC_LETTER = re.compile(r"[\u0600-\u06FF\u0750-\u077F]")
CYRILLIC_LETTER = re.compile(r"[\u0400-\u04FF]")
HAN = re.compile(r"[\u4E00-\u9FFF]")
KANA = re.compile(r"[\u3040-\u30FF]")
HANGUL = re.compile(r"[\uAC00-\uD7AF]")

#: Hebrew wins at this share of the letters. Low on purpose: a Hebrew meeting is full of
#: English product names and terms ("ה-deployment", "sprint"), and Hebrew spends fewer
#: letters per word than English, so even an evenly mixed meeting lands near 0.4. A
#: meeting that is mostly Hebrew, or mixed, is summarized in Hebrew.
HEBREW_SHARE = 0.25


@dataclass(frozen=True)
class LanguageDecision:
    language: str
    #: The share of the text's letters in that language's script; 0 with no text.
    confidence: float
    source: str = "transcript"  # transcript|default


def spoken_language(texts: Iterable[str]) -> LanguageDecision:
    """Which language a text is in, by its script: Hebrew, Arabic, Cyrillic (Russian),
    Chinese, Japanese, Korean, or Latin (English: a script shared by dozens of languages
    says nothing more, and English is the likeliest of them)."""
    counts = {"he": 0, "ar": 0, "ru": 0, "zh": 0, "ja": 0, "ko": 0, "en": 0}
    for text in texts:
        counts["he"] += len(HEBREW_LETTER.findall(text))
        counts["ar"] += len(ARABIC_LETTER.findall(text))
        counts["ru"] += len(CYRILLIC_LETTER.findall(text))
        counts["zh"] += len(HAN.findall(text))
        counts["ja"] += len(KANA.findall(text))
        counts["ko"] += len(HANGUL.findall(text))
        counts["en"] += len(LATIN_LETTER.findall(text))
    total = sum(counts.values())
    if total == 0:
        return LanguageDecision(DEFAULT_LANGUAGE, 0.0, "default")
    if counts["ja"]:
        # Japanese is written in kana and kanji together: the kanji count for it too.
        counts["ja"] += counts["zh"]
        counts["zh"] = 0
    if counts["he"] / total >= HEBREW_SHARE:
        return LanguageDecision("he", round(counts["he"] / total, 3))
    language = max(counts, key=lambda code: counts[code])
    return LanguageDecision(language, round(counts[language] / total, 3))
