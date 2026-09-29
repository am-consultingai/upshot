"""Shared pieces of the ASR benchmarks: FLEURS on disk, levelling, scoring, the app's models.

Nothing here names a path on anyone's machine. Models come from the app's own
resolution (``app.asr.models.resolve``, so ``UP_HOME`` picks the app home); downloaded
data goes to a cache directory that git ignores (``.cache/asr_bench`` in the repository
unless ``--cache`` says otherwise).
"""

from __future__ import annotations

import json
import os
import sys
import unicodedata
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

SR = 16_000
#: Silence between FLEURS utterances in an assembled recording.
GAP_S = 0.8
#: Each utterance is levelled to this RMS. About half of FLEURS is recorded ~40 dB low,
#: and both models skip sentences that quiet, so an unlevelled WER measures the
#: recording level rather than the model.
LEVEL_RMS = 0.1
DEFAULT_CACHE = REPO / ".cache" / "asr_bench"

#: Whisper code -> FLEURS config, where FLEURS names it other than ``<code>_<region>``.
FLEURS_CONFIGS: dict[str, str] = {
    "af": "af_za", "am": "am_et", "ar": "ar_eg", "as": "as_in", "az": "az_az",
    "be": "be_by", "bg": "bg_bg", "bn": "bn_in", "bs": "bs_ba", "ca": "ca_es",
    "cs": "cs_cz", "cy": "cy_gb", "da": "da_dk", "de": "de_de", "el": "el_gr",
    "en": "en_us", "es": "es_419", "et": "et_ee", "fa": "fa_ir", "fi": "fi_fi",
    "fr": "fr_fr", "gl": "gl_es", "gu": "gu_in", "ha": "ha_ng", "he": "he_il",
    "hi": "hi_in", "hr": "hr_hr", "hu": "hu_hu", "hy": "hy_am", "id": "id_id",
    "is": "is_is", "it": "it_it", "ja": "ja_jp", "jw": "jv_id", "ka": "ka_ge",
    "kk": "kk_kz", "km": "km_kh", "kn": "kn_in", "ko": "ko_kr", "lb": "lb_lu",
    "ln": "ln_cd", "lo": "lo_la", "lt": "lt_lt", "lv": "lv_lv", "mi": "mi_nz",
    "mk": "mk_mk", "ml": "ml_in", "mn": "mn_mn", "mr": "mr_in", "ms": "ms_my",
    "mt": "mt_mt", "my": "my_mm", "ne": "ne_np", "nl": "nl_nl", "no": "nb_no",
    "oc": "oc_fr", "pa": "pa_in", "pl": "pl_pl", "ps": "ps_af", "pt": "pt_br",
    "ro": "ro_ro", "ru": "ru_ru", "sd": "sd_in", "sk": "sk_sk", "sl": "sl_si",
    "sn": "sn_zw", "so": "so_so", "sr": "sr_rs", "sv": "sv_se", "sw": "sw_ke",
    "ta": "ta_in", "te": "te_in", "tg": "tg_tj", "th": "th_th", "tl": "fil_ph",
    "tr": "tr_tr", "uk": "uk_ua", "ur": "ur_pk", "uz": "uz_uz", "vi": "vi_vn",
    "yo": "yo_ng", "yue": "yue_hant_hk", "zh": "cmn_hans_cn",
}  # fmt: skip

TIER1 = ("he", "en", "es", "fr", "ru", "ar")

#: Pass thresholds for Tier 1 (WER, routed, levelled, large-v3 int8), from the
#: 2026-09-29 baselines: he 23 %, en 10 %, es 4 %, fr 6 %, ru 10 %. Arabic: recorded only.
TIER1_MAX_WER = {"he": 0.25, "en": 0.12, "es": 0.06, "fr": 0.08, "ru": 0.12}
TIER1_BASELINE_WER = {"he": 0.23, "en": 0.10, "es": 0.04, "fr": 0.06, "ru": 0.10}

#: The Unicode script (the first word of a letter's Unicode name) each language is
#: written in; any language not listed is written in Latin letters.
SCRIPTS: dict[str, tuple[str, ...]] = {
    "he": ("HEBREW",), "yi": ("HEBREW",),
    "ar": ("ARABIC",), "fa": ("ARABIC",), "ur": ("ARABIC",), "ps": ("ARABIC",),
    "sd": ("ARABIC",),
    "ru": ("CYRILLIC",), "uk": ("CYRILLIC",), "be": ("CYRILLIC",), "bg": ("CYRILLIC",),
    "mk": ("CYRILLIC",), "sr": ("CYRILLIC",), "kk": ("CYRILLIC",), "mn": ("CYRILLIC",),
    "tg": ("CYRILLIC",), "tt": ("CYRILLIC",), "ba": ("CYRILLIC",),
    "el": ("GREEK",), "hy": ("ARMENIAN",), "ka": ("GEORGIAN",),
    "hi": ("DEVANAGARI",), "mr": ("DEVANAGARI",), "ne": ("DEVANAGARI",),
    "sa": ("DEVANAGARI",),
    "bn": ("BENGALI",), "as": ("BENGALI",), "gu": ("GUJARATI",), "pa": ("GURMUKHI",),
    "ta": ("TAMIL",), "te": ("TELUGU",), "kn": ("KANNADA",), "ml": ("MALAYALAM",),
    "si": ("SINHALA",), "th": ("THAI",), "lo": ("LAO",), "km": ("KHMER",),
    "my": ("MYANMAR",), "bo": ("TIBETAN",), "am": ("ETHIOPIC",),
    "zh": ("CJK",), "yue": ("CJK",), "ja": ("CJK", "HIRAGANA", "KATAKANA"),
    "ko": ("HANGUL",),
}  # fmt: skip

#: Written without spaces between words: WER means little there, CER is the measure.
NO_SPACES = frozenset({"zh", "yue", "ja", "th", "lo", "km", "my", "bo"})


@dataclass
class Utterance:
    language: str
    audio: np.ndarray
    text: str


def cache_dir(given: str | None) -> Path:
    path = Path(given).expanduser() if given else DEFAULT_CACHE
    path.mkdir(parents=True, exist_ok=True)
    return path


def fleurs_dir(cache: Path) -> Path:
    path = cache / "fleurs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_fleurs(cache: Path, language: str) -> list[Utterance]:
    folder = fleurs_dir(cache)
    pcm = np.load(folder / f"{language}.npz")
    refs = json.loads((folder / f"{language}.json").read_text(encoding="utf-8"))
    return [
        Utterance(language, pcm[f"arr_{i}"].astype(np.float32), item["text"])
        for i, item in enumerate(refs)
    ]


def has_fleurs(cache: Path, language: str) -> bool:
    folder = fleurs_dir(cache)
    return (folder / f"{language}.npz").exists() and (folder / f"{language}.json").exists()


def level(pcm: np.ndarray) -> np.ndarray:
    rms = float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2))) if len(pcm) else 0.0
    return np.clip(pcm * (LEVEL_RMS / max(rms, 1e-6)), -1, 1).astype(np.float32)


def assemble(utterances: list[Utterance]) -> tuple[np.ndarray, list[tuple[float, float, str, str]]]:
    """One recording: each utterance levelled, with a gap after it, and where each one is."""
    gap = np.zeros(int(GAP_S * SR), dtype=np.float32)
    parts: list[np.ndarray] = []
    spans: list[tuple[float, float, str, str]] = []
    at = 0.0
    for utterance in utterances:
        pcm = level(utterance.audio)
        parts += [pcm, gap]
        spans.append((at, at + len(pcm) / SR, utterance.language, utterance.text))
        at += len(pcm) / SR + GAP_S
    return np.concatenate(parts) if parts else np.zeros(0, np.float32), spans


def write_wav(path: Path, audio: np.ndarray) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SR)
        out.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    return path


# -- scoring --------------------------------------------------------------------------


def norm(text: str) -> list[str]:
    """Lower case, punctuation and symbols out, diacritics (niqqud, accents) out."""
    text = unicodedata.normalize("NFKC", text).lower()
    text = "".join(" " if unicodedata.category(c)[0] in "PS" else c for c in text)
    text = "".join(
        c for c in unicodedata.normalize("NFKD", text) if unicodedata.category(c) != "Mn"
    )
    return text.split()


def edits(a: list[str], b: list[str]) -> int:
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


def script_share(language: str, text: str) -> float:
    """The share of the text's letters written in the script ``language`` is written in."""
    expected = SCRIPTS.get(language, ("LATIN",))
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    hits = sum(1 for c in letters if unicodedata.name(c, "").split(" ")[0] in expected)
    return hits / len(letters)


@dataclass
class PartScore:
    errors: int = 0
    words: int = 0
    char_errors: int = 0
    chars: int = 0
    letters_ok: float = 0.0
    letters: int = 0

    @property
    def wer(self) -> float:
        return self.errors / self.words if self.words else 0.0

    @property
    def cer(self) -> float:
        return self.char_errors / self.chars if self.chars else 0.0

    @property
    def script(self) -> float:
        return self.letters_ok / self.letters if self.letters else 0.0


def score(
    words: list[tuple[str, float, float]], spans: list[tuple[float, float, str, str]]
) -> tuple[dict[str, PartScore], list[str]]:
    """WER/CER/script share per language part: each word goes to the utterance its middle
    falls in (or the nearest one), then each utterance is scored against its reference."""
    heard: list[list[str]] = [[] for _ in spans]

    def distance(i: int, middle: float) -> float:
        a, b = spans[i][0], spans[i][1]
        return 0.0 if a <= middle <= b else min(abs(middle - a), abs(middle - b))

    for text, start, end in words:
        middle = (start + end) / 2
        heard[min(range(len(spans)), key=lambda i, m=middle: distance(i, m))].append(text)
    parts: dict[str, PartScore] = {}
    lines: list[str] = []
    for (start, _end, language, reference), pieces in zip(spans, heard, strict=True):
        hypothesis = "".join(pieces).strip()
        ref_words, hyp_words = norm(reference), norm(hypothesis)
        part = parts.setdefault(language, PartScore())
        part.errors += edits(ref_words, hyp_words)
        part.words += len(ref_words)
        ref_chars, hyp_chars = list(" ".join(ref_words)), list(" ".join(hyp_words))
        part.char_errors += edits(ref_chars, hyp_chars)
        part.chars += len(ref_chars)
        letters = [c for c in hypothesis if c.isalpha()]
        part.letters += len(letters)
        part.letters_ok += script_share(language, hypothesis) * len(letters)
        lines.append(f"[{start:7.1f}] {language} REF: {reference}\n{'':10}HYP: {hypothesis}")
    return parts, lines


def config_for(device: str) -> object:
    """The app's config, with only the device changed; models resolve from ``UP_HOME``."""
    from app.config import default_config

    return default_config(asr__device=device)


def environment() -> dict[str, str]:
    """What the report records about where it ran (never a path)."""
    import platform

    try:
        import ctranslate2

        cuda = str(ctranslate2.get_cuda_device_count())
    except Exception:  # pragma: no cover - no ctranslate2
        cuda = "?"
    return {
        "python": platform.python_version(),
        "machine": platform.machine(),
        "cpus": str(os.cpu_count()),
        "cuda_devices": cuda,
    }
