"""Which language a meeting was in, decided from its audio before a large model loads.

The multilingual epic's key decision (DECISIONS.md D80). A meeting has one language (R1),
and one large model transcribes all of it (R3): ivrit-ai large-v3 when it is mostly
Hebrew, stock Whisper large-v3 otherwise. The choice is made here, by Whisper small:

1. Silero VAD finds the speech on each track (the files transcription will read, so the
   echo-cancelled near track when the far side leaks into the microphone).
2. Five 30-second windows of that speech, spread evenly over each track and split
   between the tracks by their share of the speech.
3. ``detect_language`` on each window; the probabilities are averaged, so one window of
   small talk in another language moves the answer a little instead of flipping it.
4. The rule below picks the model, and whether to force the language on it.

Small is as accurate as large-v3 at this on every recording measured (10 of 10), about
five times cheaper (GPU 0.12 s a window against 0.55 s; CPU 1.6-2 s against 8.5-9 s),
loads in ~5 s in ~0.5 GB, and answers before either large model loads. ivrit cannot do
it at all: it answers Hebrew, p = 1.00, for everything, English included.

The decision is recorded (``segments.json``, ``meta.json``, one ``pipeline.log`` line)
and never shown (R7).
"""

from __future__ import annotations

import gc
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from app.asr.models import CLASSIFIER, HEBREW, HEBREW_LANGUAGE, OTHER, require
from app.config import Config
from app.errors import Cancelled
from app.log import get

log = get(__name__)

SAMPLE_RATE = 16_000

#: Mean p(he) at or above this → the Hebrew model. The literal majority rule of R2.
#: Measured (2026-09-29, 11 recordings): Hebrew-main recordings 0.67-0.98, non-Hebrew
#: ones 0.00-0.07. Nothing landed between 0.1 and 0.6.
HEBREW_THRESHOLD = 0.50

#: The top non-Hebrew language at or above this is forced on stock large-v3; below it,
#: Whisper decides per 30 s itself (``language=None, multilingual=True``). Forcing one
#: language on an unclear meeting translates the rest into it: a five-language mix forced
#: to Russian came out with its English in Russian. Measured: single-language recordings
#: 0.89-1.00, the five-language mix 0.29-0.64.
FORCE_THRESHOLD = 0.60

#: How many windows, and how long. 1, 3 and 10 gave the same decisions on every recording
#: measured; 5 is insurance against a meeting that opens with small talk in another
#: language. 30 s is what Whisper hears in one pass.
WINDOWS = 5
WINDOW_S = 30.0

#: At this much speech in total, every track that has any speech gets a window.
MIN_SPLIT_SPEECH_S = 60.0

TopN = tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class Window:
    """One sampled window: which track, where it starts on that track, what it heard."""

    track: str
    start_s: float
    top3: TopN = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "track": self.track,
            "start_s": round(self.start_s, 2),
            "top3": [[lang, round(p, 4)] for lang, p in self.top3],
        }


@dataclass(frozen=True)
class LanguageDecision:
    """What the meeting was in, which model transcribes it, and how that was decided."""

    #: The meeting's language (ISO 639-1, or Whisper's own code such as ``haw``).
    language: str
    #: ``hebrew`` (ivrit large-v3) or ``other`` (stock large-v3).
    route: str
    #: Whether ``language`` is forced on the model; False lets Whisper decide per 30 s.
    forced: bool = True
    p_he: float = 0.0
    #: The probability of ``language`` (for Hebrew, ``p_he``).
    p_top: float = 0.0
    top3: TopN = ()
    windows: tuple[Window, ...] = ()
    seconds: float = 0.0
    rule: str = ""
    #: Where it ran, for the log: cuda|cpu, or "" when no model was loaded.
    device: str = ""

    @property
    def transcribe_language(self) -> str | None:
        """What the large model is told: the language, or None to let it decide."""
        if self.route == HEBREW:
            return HEBREW_LANGUAGE
        return self.language if self.forced else None

    @property
    def multilingual(self) -> bool:
        return self.route == OTHER and not self.forced

    def as_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "route": self.route,
            "forced": self.forced,
            "p_he": round(self.p_he, 4),
            "p_top": round(self.p_top, 4),
            "top3": [[lang, round(p, 4)] for lang, p in self.top3],
            "windows": len(self.windows),
            "per_window": [window.as_dict() for window in self.windows],
            "seconds": round(self.seconds, 2),
            "rule": self.rule,
            "device": self.device,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> LanguageDecision:
        return cls(
            language=str(payload.get("language", HEBREW_LANGUAGE)),
            route=str(payload.get("route", HEBREW)),
            forced=bool(payload.get("forced", True)),
            p_he=float(payload.get("p_he", 0.0)),
            p_top=float(payload.get("p_top", 0.0)),
            top3=tuple((str(lang), float(p)) for lang, p in payload.get("top3", ())),
            windows=tuple(
                Window(
                    str(item.get("track", "")),
                    float(item.get("start_s", 0.0)),
                    tuple((str(lang), float(p)) for lang, p in item.get("top3", ())),
                )
                for item in payload.get("per_window", ())
            ),
            seconds=float(payload.get("seconds", 0.0)),
            rule=str(payload.get("rule", "")),
            device=str(payload.get("device", "")),
        )

    def log_line(self) -> str:
        return (
            f"meeting language {self.language}: p_he={self.p_he:.2f} p_top={self.p_top:.2f} "
            f"route={self.route} forced={self.forced} windows={len(self.windows)} "
            f"{self.seconds:.1f}s ({self.rule})"
        )


def top_n(probs: Mapping[str, float], n: int = 3) -> TopN:
    ranked = sorted(probs.items(), key=lambda item: (-item[1], item[0]))
    return tuple((lang, float(p)) for lang, p in ranked[:n])


def decide(probs: Mapping[str, float]) -> LanguageDecision:
    """The rule, on the mean probabilities over the windows."""
    p_he = float(probs.get(HEBREW_LANGUAGE, 0.0))
    top3 = top_n(probs)
    if p_he >= HEBREW_THRESHOLD:
        return LanguageDecision(
            HEBREW_LANGUAGE, HEBREW, True, p_he, p_he, top3,
            rule=f"p_he {p_he:.2f} >= {HEBREW_THRESHOLD:.2f}",
        )  # fmt: skip
    others = {lang: p for lang, p in probs.items() if lang != HEBREW_LANGUAGE}
    if not others:
        return LanguageDecision(HEBREW_LANGUAGE, HEBREW, True, p_he, p_he, top3, rule="no answer")
    language = max(others, key=lambda lang: (others[lang], lang))
    p_top = float(others[language])
    forced = p_top >= FORCE_THRESHOLD
    comparison = ">=" if forced else "<"
    return LanguageDecision(
        language, OTHER, forced, p_he, p_top, top3,
        rule=f"p_he {p_he:.2f} < {HEBREW_THRESHOLD:.2f}; p_{language} {p_top:.2f} "
        f"{comparison} {FORCE_THRESHOLD:.2f}",
    )  # fmt: skip


def override(language: str) -> LanguageDecision:
    """The language someone chose with "Transcribe again as…" (R9): no classifier, and
    the model for that language, told it."""
    hebrew = language == HEBREW_LANGUAGE
    return LanguageDecision(
        language, HEBREW if hebrew else OTHER, True,
        1.0 if hebrew else 0.0, 1.0, rule="override",
    )  # fmt: skip


def mean_probabilities(answers: list[list[tuple[str, float]]]) -> dict[str, float]:
    totals: dict[str, float] = {}
    if not answers:
        return totals
    for answer in answers:
        for lang, p in answer:
            totals[lang] = totals.get(lang, 0.0) + float(p) / len(answers)
    return totals


def allocate(speech_s: Mapping[str, float], total: int = WINDOWS) -> dict[str, int]:
    """How many windows each track gets: by its share of the speech, ``total`` in all.

    Largest remainder, ties to the track with more speech. With at least
    ``MIN_SPLIT_SPEECH_S`` of speech in all, a track with any speech gets at least one:
    a meeting where "me" says little is decided mostly, not only, by "them".
    """
    speaking = {track: s for track, s in speech_s.items() if s > 0}
    whole = sum(speaking.values())
    if not speaking or whole <= 0:
        return {}
    exact = {track: total * s / whole for track, s in speaking.items()}
    counts = {track: int(value) for track, value in exact.items()}
    by_remainder = sorted(
        speaking, key=lambda track: (-(exact[track] - counts[track]), -speaking[track], track)
    )
    for track in by_remainder[: total - sum(counts.values())]:
        counts[track] += 1
    if whole >= MIN_SPLIT_SPEECH_S:
        for track in speaking:
            if counts[track] == 0:
                donor = max(counts, key=lambda t: (counts[t], speaking[t]))
                if counts[donor] > 1:
                    counts[donor] -= 1
                    counts[track] = 1
    return {track: n for track, n in counts.items() if n > 0}


@dataclass
class TrackSpeech:
    """A track's speech, joined up, and where each piece came from on the track."""

    track: str
    audio: np.ndarray
    #: (start, end) sample ranges of the original track, in the order they were joined.
    spans: list[tuple[int, int]] = field(default_factory=list)

    @property
    def seconds(self) -> float:
        return len(self.audio) / SAMPLE_RATE

    def original_s(self, index: int) -> float:
        """Where sample ``index`` of the joined speech was on the original track."""
        at = 0
        for start, end in self.spans:
            if index < at + (end - start):
                return (start + index - at) / SAMPLE_RATE
            at += end - start
        return (self.spans[-1][1] if self.spans else index) / SAMPLE_RATE


def speech_of(track: str, wav: Path) -> TrackSpeech:  # pragma: no cover - real VAD
    """Silero VAD, as faster-whisper runs it, over the whole file at 16 kHz."""
    from faster_whisper import decode_audio
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    audio = decode_audio(str(wav), sampling_rate=SAMPLE_RATE)
    stamps = get_speech_timestamps(audio, VadOptions())
    spans = [(int(s["start"]), int(s["end"])) for s in stamps]
    joined = (
        np.concatenate([audio[start:end] for start, end in spans])
        if spans
        else np.zeros(0, dtype=np.float32)
    )
    return TrackSpeech(track, joined.astype(np.float32), spans)


def sample_windows(speech: list[TrackSpeech], total: int = WINDOWS) -> list[tuple[Window, Any]]:
    """The windows to classify, each with its audio."""
    width = int(WINDOW_S * SAMPLE_RATE)
    whole = sum(len(item.audio) for item in speech)
    if whole == 0:
        return []
    if whole < width:
        # Under 30 s in all: one window of everything there is.
        joined = np.concatenate([item.audio for item in speech if len(item.audio)])
        tracks = "+".join(item.track for item in speech if len(item.audio))
        first = next(item for item in speech if len(item.audio))
        return [(Window(tracks, first.original_s(0)), joined)]
    counts = allocate({item.track: item.seconds for item in speech}, total)
    out: list[tuple[Window, Any]] = []
    for item in speech:
        n = counts.get(item.track, 0)
        if not n:
            continue
        starts = np.linspace(0, max(0, len(item.audio) - width), n).astype(int)
        for start in starts:
            out.append(
                (Window(item.track, item.original_s(int(start))), item.audio[start : start + width])
            )
    return out


class Classifier(Protocol):
    def classify(self, inputs: Mapping[str, Path]) -> LanguageDecision: ...


ModelFactory = Callable[..., Any]
SpeechReader = Callable[[str, Path], TrackSpeech]


def _default_factory(**kwargs: Any) -> Any:  # pragma: no cover - needs the real package
    from faster_whisper import WhisperModel

    return WhisperModel(**kwargs)


class WhisperClassifier:
    """Whisper small over sampled speech. Loaded for one meeting, unloaded before return."""

    name = "whisper-small"

    def __init__(
        self,
        config: Config,
        *,
        model_factory: ModelFactory | None = None,
        read_speech: SpeechReader | None = None,
    ) -> None:
        self.config = config
        self.model_factory = model_factory or _default_factory
        self.read_speech = read_speech or speech_of
        self.loads = 0
        self.unloads = 0
        #: Called between windows; raises to stop (the meeting is being deleted).
        self.stop_check: Callable[[], None] | None = None

    def classify(self, inputs: Mapping[str, Path]) -> LanguageDecision:
        started = time.monotonic()
        speech = [self.read_speech(track, Path(wav)) for track, wav in sorted(inputs.items())]
        windows = sample_windows(speech)
        if not windows:
            return LanguageDecision(
                HEBREW_LANGUAGE, HEBREW, True, rule="no speech",
                seconds=time.monotonic() - started,
            )  # fmt: skip
        model, device = self._load()
        try:
            answers: list[list[tuple[str, float]]] = []
            sampled: list[Window] = []
            for window, audio in windows:
                if self.stop_check is not None:
                    self.stop_check()
                _lang, _p, probs = model.detect_language(audio=audio)
                answer = [(str(lang), float(p)) for lang, p in probs]
                answers.append(answer)
                sampled.append(Window(window.track, window.start_s, top_n(dict(answer))))
        finally:
            del model
            gc.collect()
            self.unloads += 1
        decision = decide(mean_probabilities(answers))
        return LanguageDecision(
            decision.language, decision.route, decision.forced, decision.p_he, decision.p_top,
            decision.top3, tuple(sampled), time.monotonic() - started, decision.rule, device,
        )  # fmt: skip

    def _load(self) -> tuple[Any, str]:
        """On the device transcription plans for, int8, with the same CUDA → CPU fallback."""
        from app.asr.local import LocalAsr, probe_device

        choice = require(self.config, CLASSIFIER)
        device, _compute, _dirs = probe_device(self.config)
        threads = max(1, (os.cpu_count() or 4) - 2)
        try:
            model = self.model_factory(
                model_size_or_path=choice.reference, device=device, compute_type="int8",
                cpu_threads=threads if device == "cpu" else 0,
            )  # fmt: skip
        except Exception as exc:
            if device == "cpu" or not LocalAsr._is_cuda_error(exc):
                raise
            log.warning("classifier: GPU load failed (%s); on the CPU instead", exc)
            device = "cpu"
            model = self.model_factory(
                model_size_or_path=choice.reference, device="cpu", compute_type="int8",
                cpu_threads=threads,
            )  # fmt: skip
        self.loads += 1
        return model, device


def classify(inputs: Mapping[str, Path], config: Config, **kwargs: Any) -> LanguageDecision:
    return WhisperClassifier(config, **kwargs).classify(inputs)


def _classify_in_child(data: dict[str, Any], inputs: dict[str, str]) -> dict[str, Any]:
    """The classifier, in a process of its own (see ``IsolatedClassifier``)."""
    config = Config(data)
    decision = WhisperClassifier(config).classify({k: Path(v) for k, v in inputs.items()})
    return decision.as_dict()


ChildRunner = Callable[..., dict[str, Any]]


def spawn_child(
    data: dict[str, Any],
    inputs: dict[str, str],
    stop_check: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Run ``_classify_in_child`` in a fresh spawned process and wait for it. ``stop_check``
    is asked every fifth of a second; if it raises, the process is ended at once."""
    import multiprocessing

    pool = multiprocessing.get_context("spawn").Pool(1)
    try:
        pending = pool.apply_async(_classify_in_child, (data, inputs))
        while not pending.ready():
            if stop_check is not None:
                stop_check()
            pending.wait(0.2)
        return dict(pending.get())
    finally:
        pool.terminate()
        pool.join()


class IsolatedClassifier:
    """The classifier on the CPU runs in a child process that exits when it answers.

    Measured (2026-09-29, CPU, a 100 s clip): run in the same process, the classifier's
    freed memory stayed in the heap, fragmented, and ivrit large-v3 then peaked at 5.7 GB
    instead of 4.5 GB, over the D60 budget that the 12 GB minimum (D66) rests on. A process
    of its own returns every byte. On the GPU the weights are in video memory and the
    extra second a new process costs is not worth it, so it runs in place there.
    """

    name = "whisper-small"

    def __init__(self, config: Config, *, run_child: ChildRunner | None = None) -> None:
        self.config = config
        self.run_child = run_child or spawn_child
        self.isolated = False
        self.stop_check: Callable[[], None] | None = None

    def classify(self, inputs: Mapping[str, Path]) -> LanguageDecision:
        from app.asr.local import planned_device

        require(self.config, CLASSIFIER)  # a missing model is said here, not in the child
        if planned_device(self.config) != "cpu":
            return self._in_place(inputs)
        try:
            answer = self.run_child(
                self.config.as_dict(),
                {track: str(wav) for track, wav in inputs.items()},
                self.stop_check,
            )
        except Cancelled:
            raise
        except Exception as exc:
            log.warning("classifier: its own process failed (%s); running it in place", exc)
            return self._in_place(inputs)
        self.isolated = True
        return LanguageDecision.from_dict(answer)

    def _in_place(self, inputs: Mapping[str, Path]) -> LanguageDecision:
        classifier = WhisperClassifier(self.config)
        classifier.stop_check = self.stop_check
        return classifier.classify(inputs)
