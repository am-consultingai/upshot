"""``FakeAsr`` — the backend that makes the whole pipeline testable in milliseconds."""

from __future__ import annotations

import hashlib
import wave
from pathlib import Path

from app.asr.backend import Segment, Word, track_of

SENTENCES: tuple[str, ...] = (
    "בוא נתחיל עם הסטטוס של הפרויקט",
    "יש לנו בעיה עם ה-deployment בסביבת הפרודקשן",
    "אני אקח את זה ואחזור אליכם עד יום חמישי",
    "we agreed to move the release to next week",
    "let's write that down as a decision",
    "מי לוקח את המשימה של ה-monitoring",
)

#: An English meeting, for ``language="en"``: the meeting's language is read from what
#: was transcribed (``app/asr/language.py``), so the fake has to say it in English.
SENTENCES_EN: tuple[str, ...] = (
    "let's start with the status of the project",
    "we have a problem with the deployment in production",
    "I'll take that and get back to you by Thursday",
    "we agreed to move the release to next week",
    "let's write that down as a decision",
    "who is taking the monitoring task",
)


class FakeAsr:
    """Deterministic segments derived from the input file name and its duration."""

    name = "fake"

    def __init__(
        self,
        *,
        language: str = "he",
        repetitions: int = 1,
        segment_s: float = 6.0,
    ) -> None:
        self.language = language
        self.sentences = SENTENCES_EN if language == "en" else SENTENCES
        self.repetitions = max(1, repetitions)
        self.segment_s = segment_s
        self.transcribe_calls: list[dict[str, object]] = []
        self.unloaded = 0

    # -- helpers

    @staticmethod
    def _duration_s(wav: Path) -> float:
        try:
            with wave.open(str(wav), "rb") as handle:
                return handle.getnframes() / float(handle.getframerate())
        except Exception:
            return 12.0

    def _seed(self, wav: Path) -> int:
        # The track is part of the key: the two tracks must not produce identical text,
        # or echo suppression would (correctly) delete one of them.
        # Seed on the track, not the folder: both tracks are now sibling files, and
        # seeding on the name alone made every track produce identical text.
        key = f"{track_of(wav)}/{wav.name}"
        return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16)

    # -- protocol

    def transcribe(
        self,
        wav: Path,
        *,
        language: str = "he",
        initial_prompt: str | None = None,
        word_timestamps: bool = True,
    ) -> list[Segment]:
        self.transcribe_calls.append(
            {"wav": wav, "language": language, "initial_prompt": initial_prompt}
        )
        track = track_of(wav)
        speaker = "ME" if track == "me" else "THEM"
        duration = self._duration_s(wav)
        seed = self._seed(wav)
        segments: list[Segment] = []
        index = 0
        start = 0.0
        while start + 1.0 <= duration:
            end = min(duration, start + self.segment_s)
            for repeat in range(self.repetitions):
                text = self.sentences[(seed + index + repeat) % len(self.sentences)]
                if self.repetitions > 1:
                    text = self.sentences[(seed + index) % len(self.sentences)]
                words = self._words(text, start, end)
                segments.append(
                    Segment(
                        id=len(segments),
                        track=track,
                        speaker=speaker,
                        start=start,
                        end=end,
                        text=text,
                        words=words,
                        avg_logprob=-0.2,
                        no_speech_prob=0.01,
                    )
                )
            index += 1
            start = end
        return segments

    @staticmethod
    def _words(text: str, start: float, end: float) -> tuple[Word, ...]:
        parts = text.split()
        if not parts:
            return ()
        step = (end - start) / len(parts)
        return tuple(
            Word(part, round(start + i * step, 3), round(start + (i + 1) * step, 3), 0.9)
            for i, part in enumerate(parts)
        )

    def unload(self) -> None:
        self.unloaded += 1
