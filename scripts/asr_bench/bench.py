"""Tier 1 and Tier 2: classify, route and transcribe FLEURS recordings with the app's code.

Each language's ~2 minutes of FLEURS becomes one recording (utterances levelled, 0.8 s
apart). The app's classifier decides the language and the route; the app's backend
transcribes with the model it chose, told what the app would tell it. Each part is
scored by its word timestamps: WER, CER and script share (the fraction of letters in the
expected script). Writes ``report.json`` and one REF/HYP file per language.

    # models from the app home (UP_HOME), GPU if available:
    python scripts/asr_bench/bench.py --tier 1 --out reports/asr-tier1
    python scripts/asr_bench/bench.py --tier 2 --classify-only --out reports/asr-tier2
    python scripts/asr_bench/bench.py --langs es ar --device cpu

``--clips DIR`` adds the D60 recordings for Tier 1 (``mixed-he-en-he.wav``: Hebrew
0-25 s, English 25.5-45.5 s, Hebrew 46-66 s); only their classification is checked.
Exit code 1 when a Tier 1 gate fails.
"""

from __future__ import annotations

import argparse
import gc
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (
    FLEURS_CONFIGS,
    NO_SPACES,
    SR,
    TIER1,
    TIER1_BASELINE_WER,
    TIER1_MAX_WER,
    assemble,
    cache_dir,
    config_for,
    environment,
    has_fleurs,
    load_fleurs,
    score,
    write_wav,
)

from app.asr.classify import WhisperClassifier
from app.asr.languages import LANGUAGES, direction_for
from app.asr.local import LocalAsr
from app.asr.models import MODELS

#: Tier 2: overall classification accuracy needed, and the script share per language.
TIER2_MIN_ACCURACY = 0.98
TIER2_MIN_SCRIPT = 0.90
TIER1_MIN_SCRIPT = 0.95
#: A regression of more than this against the baseline blocks the change (Tier 1).
MAX_REGRESSION = 0.02


class CountingFactory:
    """Wraps faster-whisper's model constructor to count loads per model (the R3 check)."""

    def __init__(self) -> None:
        self.loads: list[str] = []

    def __call__(self, **kwargs: Any) -> Any:
        from faster_whisper import WhisperModel

        self.loads.append(str(kwargs.get("model_size_or_path")))
        return WhisperModel(**kwargs)


def d60_recordings(folder: Path) -> dict[str, tuple[np.ndarray, str]]:
    from faster_whisper import decode_audio

    mixed = decode_audio(str(folder / "mixed-he-en-he.wav"), sampling_rate=SR)
    return {
        "d60-mixed": (mixed, "he"),
        "d60-hebrew": (mixed[: 25 * SR], "he"),
        "d60-english": (mixed[int(25.5 * SR) : int(45.5 * SR)], "en"),
    }


def run_language(
    language: str, cache: Path, work: Path, config: Any, out: Path, transcribe: bool
) -> dict[str, Any]:
    utterances = load_fleurs(cache, language)
    audio, spans = assemble(utterances)
    wav = write_wav(work / f"{language}.wav", audio)
    classifier = WhisperClassifier(config)
    decision = classifier.classify({"them": wav})
    entry: dict[str, Any] = {
        "language": language,
        "fleurs": FLEURS_CONFIGS[language],
        "audio_s": round(len(audio) / SR, 1),
        "classified": decision.language,
        "correct": decision.language == language,
        "route": decision.route,
        "forced": decision.forced,
        "p_he": round(decision.p_he, 3),
        "p_top": round(decision.p_top, 3),
        "top3": decision.top3,
        "classify_s": round(decision.seconds, 2),
        "device": decision.device,
        "direction": direction_for(decision.language),
        "expected_direction": direction_for(language),
    }
    if not transcribe:
        return entry
    factory = CountingFactory()
    backend = LocalAsr(config, role=decision.route, model_factory=factory)
    started = time.monotonic()
    segments = backend.transcribe(
        wav,
        language=decision.transcribe_language,
        multilingual=decision.multilingual,
        word_timestamps=True,
    )
    took = time.monotonic() - started
    backend.unload()
    gc.collect()
    words = [
        (w.w if w.w.startswith(" ") else " " + w.w, w.s, w.e) for s in segments for w in s.words
    ]
    if language in NO_SPACES:
        words = [(w.strip(), s, e) for w, s, e in words]
    parts, lines = score(words, spans)
    part = parts[language]
    entry.update(
        model=MODELS[decision.route].repo,
        transcribe_s=round(took, 1),
        model_loads=len(factory.loads),
        wer=round(part.wer, 3),
        cer=round(part.cer, 3),
        script=round(part.script, 3),
    )
    header = f"{language}: {MODELS[decision.route].repo}, language={decision.transcribe_language}"
    (out / f"{language}.txt").write_text(
        header + "\n\n" + "\n".join(lines) + "\n", encoding="utf-8"
    )
    return entry


def gates(tier: int, rows: list[dict[str, Any]], clips: list[dict[str, Any]]) -> list[str]:
    """What failed, in words; empty when the tier passes."""
    failures: list[str] = []
    classified = rows + clips
    wrong = [r for r in classified if not r["correct"]]
    if tier == 1:
        failures += [f"{r['language']}: classified {r['classified']}" for r in wrong]
        for row in rows:
            language = row["language"]
            if "wer" not in row:
                continue
            limit = TIER1_MAX_WER.get(language)
            if limit is not None and row["wer"] > limit:
                failures.append(f"{language}: WER {row['wer']:.1%} over {limit:.0%}")
            base = TIER1_BASELINE_WER.get(language)
            if base is not None and row["wer"] > base + MAX_REGRESSION:
                failures.append(f"{language}: WER {row['wer']:.1%}, baseline {base:.0%} + 2")
            if row["script"] < TIER1_MIN_SCRIPT:
                failures.append(f"{language}: script share {row['script']:.2f}")
            if row.get("model_loads", 1) != 1:
                failures.append(f"{language}: {row['model_loads']} large-model loads (R3)")
    else:
        accuracy = 1 - len(wrong) / len(rows) if rows else 0.0
        if accuracy < TIER2_MIN_ACCURACY:
            failures.append(f"classification {accuracy:.1%} under {TIER2_MIN_ACCURACY:.0%}")
        for row in rows:
            if "script" in row and row["script"] < TIER2_MIN_SCRIPT:
                failures.append(f"{row['language']}: script share {row['script']:.2f}")
            if row["direction"] != row["expected_direction"]:
                failures.append(f"{row['language']}: direction {row['direction']}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tier", type=int, choices=(1, 2), default=1)
    parser.add_argument("--langs", nargs="*", help="instead of the tier's languages")
    parser.add_argument("--device", default="auto", choices=("auto", "cpu", "cuda"))
    parser.add_argument("--cuda-dir", nargs="*", help="folders holding cuBLAS/cuDNN")
    parser.add_argument("--classify-only", action="store_true")
    parser.add_argument("--clips", help="folder with the D60 clips (Tier 1)")
    parser.add_argument("--cache", help="data cache (default: .cache/asr_bench)")
    parser.add_argument("--out", default="reports/asr-bench", help="report folder")
    args = parser.parse_args(argv)

    cache = cache_dir(args.cache)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    work = cache / "work"
    config: Any = config_for(args.device)
    if args.cuda_dir:
        config.set("asr.cuda_dir", args.cuda_dir)
    languages = args.langs or (list(TIER1) if args.tier == 1 else sorted(FLEURS_CONFIGS))
    missing = [lang for lang in languages if not has_fleurs(cache, lang)]
    languages = [lang for lang in languages if lang not in missing]

    rows: list[dict[str, Any]] = []
    for language in languages:
        row = run_language(language, cache, work, config, out, not args.classify_only)
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)

    clips: list[dict[str, Any]] = []
    if args.clips:
        classifier = WhisperClassifier(config)
        for name, (audio, truth) in d60_recordings(Path(args.clips)).items():
            decision = classifier.classify({"them": write_wav(work / f"{name}.wav", audio)})
            clips.append({
                "language": name, "truth": truth, "classified": decision.language,
                "correct": decision.language == truth, "p_he": round(decision.p_he, 3),
                "route": decision.route, "classify_s": round(decision.seconds, 2),
            })  # fmt: skip
            print(json.dumps(clips[-1]), flush=True)

    failures = gates(args.tier, rows, clips)
    report = {
        "tier": args.tier,
        "when": time.strftime("%Y-%m-%d %H:%M"),
        "environment": environment(),
        "device": args.device,
        "models": {role: f"{m.repo}@{m.revision[:12]}" for role, m in MODELS.items()},
        "classification": {
            "correct": sum(r["correct"] for r in rows),
            "total": len(rows),
            "misses": [
                {"language": r["language"], "classified": r["classified"], "top3": r["top3"]}
                for r in rows
                if not r["correct"]
            ],
        },
        "whisper_languages_not_in_fleurs": sorted(set(LANGUAGES) - set(FLEURS_CONFIGS)),
        "not_fetched": missing,
        "languages": rows,
        "d60_clips": clips,
        "failures": failures,
        "passed": not failures,
    }
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    print("PASSED" if not failures else "FAILED:\n  " + "\n  ".join(failures))
    return 0 if not failures or args.tier == 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
