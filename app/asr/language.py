"""Language resolution: once per meeting, then pinned (TECHNICAL-DESIGN.md §7).

Per-chunk detection is deliberately *not* used — Whisper's mid-audio language switching
is unreliable and one wrong switch corrupts a whole chunk.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.asr.backend import AsrBackend
from app.audio.vad import TwoStageVad, make_vad, read_wav
from app.config import Config
from app.log import get

log = get(__name__)


@dataclass(frozen=True)
class LanguageDecision:
    language: str
    confidence: float
    source: str  # fixed|detected|default
    needs_review: bool = False

    @property
    def review_reason(self) -> str | None:
        if not self.needs_review:
            return None
        return f"language detection was only {self.confidence:.2f} confident"


def has_speech(wav: Path, min_s: float, vad: TwoStageVad | None = None) -> bool:
    try:
        pcm, rate = read_wav(wav)
    except Exception:
        return False
    return (vad or TwoStageVad()).has_speech(pcm, rate, min_s=min_s)


def resolve_language(
    backend: AsrBackend,
    first_chunks: dict[str, Path],
    config: Config,
    *,
    vad: TwoStageVad | None = None,
    min_speech_s: float = 3.0,
) -> LanguageDecision:
    default = config.default_language
    if config.language_mode == "fixed":
        return LanguageDecision(default, 1.0, "fixed")
    vad = vad or make_vad(config)
    minimum = config.detect_min_confidence
    for track in ("them", "me"):  # 'them' usually carries more speech
        wav = first_chunks.get(track)
        if wav is None or not wav.exists():
            continue
        if not has_speech(wav, min_speech_s, vad):
            continue
        language, probability = backend.detect_language(wav)
        if probability >= minimum:
            log.info("language %s detected on %s (p=%.2f)", language, track, probability)
            return LanguageDecision(language, probability, "detected")
        log.warning(
            "language detection returned %s at %.2f (< %.2f) — using the default %s",
            language,
            probability,
            minimum,
            default,
        )
        return LanguageDecision(default, probability, "default", needs_review=True)
    return LanguageDecision(default, 0.0, "default", needs_review=True)
