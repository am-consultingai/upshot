"""Token counting, cached per meeting.

Windowing counts **real tokens**, never characters: Hebrew inflates token counts 2–4×
per word and a character heuristic silently blows the window.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Protocol


class TokenCounter(Protocol):
    def count(self, text: str) -> int: ...


class CachingCounter:
    """Wraps any counter with a per-meeting cache keyed by the hash of the text."""

    def __init__(self, counter: Callable[[str], int]) -> None:
        self._counter = counter
        self._cache: dict[str, int] = {}
        self.calls = 0
        self.hits = 0

    @staticmethod
    def key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def count(self, text: str) -> int:
        key = self.key(text)
        cached = self._cache.get(key)
        if cached is not None:
            self.hits += 1
            return cached
        self.calls += 1
        value = int(self._counter(text))
        self._cache[key] = value
        return value


class CharCounter:
    """A deterministic stand-in: ``chars_per_token`` characters make one token."""

    def __init__(self, chars_per_token: float = 1.0) -> None:
        self.chars_per_token = chars_per_token
        self.calls = 0

    def count(self, text: str) -> int:
        self.calls += 1
        return int(len(text) / self.chars_per_token)


def tokens_per_word(text: str, counter: TokenCounter) -> float:
    words = len(text.split())
    if words == 0:
        return 0.0
    return counter.count(text) / words
