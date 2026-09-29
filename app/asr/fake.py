"""``FakeAsr`` — the backend that makes the whole pipeline testable in milliseconds."""

from __future__ import annotations

import hashlib
import wave
from collections.abc import Mapping
from pathlib import Path

from app.asr.backend import Segment, Word, track_of
from app.asr.classify import LanguageDecision, decide
from app.asr.models import HEBREW, HEBREW_LANGUAGE

SENTENCES: tuple[str, ...] = (
    "בוא נתחיל עם הסטטוס של הפרויקט",
    "יש לנו בעיה עם ה-deployment בסביבת הפרודקשן",
    "אני אקח את זה ואחזור אליכם עד יום חמישי",
    "we agreed to move the release to next week",
    "let's write that down as a decision",
    "מי לוקח את המשימה של ה-monitoring",
)

#: An English meeting, for ``language="en"``: canned text in the language asked for, so
#: the stages after transcription can be tested in it.
SENTENCES_EN: tuple[str, ...] = (
    "let's start with the status of the project",
    "we have a problem with the deployment in production",
    "I'll take that and get back to you by Thursday",
    "we agreed to move the release to next week",
    "let's write that down as a decision",
    "who is taking the monitoring task",
)

SENTENCES_ES: tuple[str, ...] = (
    "empecemos con el estado del proyecto",
    "tenemos un problema con el despliegue en producción",
    "me encargo de eso y os respondo antes del jueves",
    "acordamos mover el lanzamiento a la semana que viene",
    "anotemos eso como una decisión",
    "quién se encarga de la tarea de monitorización",
)

SENTENCES_AR: tuple[str, ...] = (
    "لنبدأ بحالة المشروع",
    "لدينا مشكلة في النشر على بيئة الإنتاج",
    "سأتولى ذلك وأعود إليكم قبل يوم الخميس",
    "اتفقنا على تأجيل الإصدار إلى الأسبوع القادم",
    "لنكتب ذلك كقرار",
    "من سيتولى مهمة المراقبة",
)

SENTENCES_RU: tuple[str, ...] = (
    "начнём со статуса проекта",
    "у нас проблема с развёртыванием в продакшене",
    "я возьму это на себя и отвечу до четверга",
    "мы договорились перенести релиз на следующую неделю",
    "давайте запишем это как решение",
    "кто возьмёт задачу по мониторингу",
)

SENTENCES_ZH: tuple[str, ...] = (
    "我们先从项目的状态开始",
    "生产环境的部署有问题",
    "这件事我来负责周四之前答复大家",
    "我们同意把发布推迟到下周",
    "把这个记下来作为一项决定",
    "谁来负责监控任务",
)

#: Canned text per language; any other language gets English.
SENTENCES_BY_LANGUAGE: dict[str, tuple[str, ...]] = {
    "he": SENTENCES,
    "en": SENTENCES_EN,
    "es": SENTENCES_ES,
    "ar": SENTENCES_AR,
    "ru": SENTENCES_RU,
    "zh": SENTENCES_ZH,
}


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
        self.sentences = SENTENCES_BY_LANGUAGE.get(language, SENTENCES_EN)
        self.repetitions = max(1, repetitions)
        self.segment_s = segment_s
        self.transcribe_calls: list[dict[str, object]] = []
        self.unloaded = 0
        #: Which model role this stands in for, and every role it was asked to be (R3).
        self.role = HEBREW
        self.roles: list[str] = []

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

    def select_role(self, role: str) -> None:
        """Stand in for this model role (the transcribe stage says which, once a meeting)."""
        self.role = role
        self.roles.append(role)

    def describe(self) -> dict[str, object]:
        return {"name": self.name, "role": self.role}

    # -- protocol

    def transcribe(
        self,
        wav: Path,
        *,
        language: str | None = "he",
        initial_prompt: str | None = None,
        word_timestamps: bool = True,
        multilingual: bool = False,
    ) -> list[Segment]:
        self.transcribe_calls.append(
            {"wav": wav, "language": language, "initial_prompt": initial_prompt,
             "multilingual": multilingual, "role": self.role}
        )  # fmt: skip
        sentences = SENTENCES_BY_LANGUAGE.get(language, SENTENCES_EN) if language else None
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
                pool = sentences or self.sentences
                text = pool[(seed + index + repeat) % len(pool)]
                if self.repetitions > 1:
                    text = pool[(seed + index) % len(pool)]
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


class FakeClassifier:
    """A scripted language decision, for tests: no model, no audio read.

    Give it a whole ``LanguageDecision``, or just a language: ``he`` routes to the Hebrew
    model, anything else to stock large-v3, forced, at ``p``.
    """

    name = "fake-classifier"

    def __init__(
        self, language: str = "he", *, p: float = 0.95, decision: LanguageDecision | None = None
    ) -> None:
        if decision is None:
            if language == HEBREW_LANGUAGE:
                probs = {HEBREW_LANGUAGE: p, "en": round(1 - p, 4)}
            else:
                probs = {language: p, HEBREW_LANGUAGE: round((1 - p) / 2, 4),
                         "en" if language != "en" else "es": round((1 - p) / 2, 4)}  # fmt: skip
            decision = decide(probs)
        self.decision = decision
        self.calls: list[dict[str, Path]] = []

    def classify(self, inputs: Mapping[str, Path]) -> LanguageDecision:
        self.calls.append(dict(inputs))
        return self.decision
