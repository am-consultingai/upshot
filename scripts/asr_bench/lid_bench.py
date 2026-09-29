"""The classifier benchmark behind D80: Whisper small against stock large-v3 at telling
which language a recording is in, and what each costs.

For each FLEURS language (and the D60 clips with ``--clips``), 1, 3 and 10 windows of
30 s of speech, evenly spaced; the decision rule is the app's (``classify.decide``).
Reports accuracy, confidence, seconds per window and load time per model.

    python scripts/asr_bench/lid_bench.py --device cuda --out reports/lid
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import SR, TIER1, assemble, cache_dir, config_for, has_fleurs, load_fleurs

from app.asr.classify import decide, mean_probabilities
from app.asr.models import CLASSIFIER, OTHER, resolve


def windows(audio: np.ndarray, n: int) -> list[np.ndarray]:
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    stamps = get_speech_timestamps(audio, VadOptions())
    speech = np.concatenate([audio[s["start"] : s["end"]] for s in stamps]) if stamps else audio
    width = 30 * SR
    k = max(1, min(n, len(speech) // width))
    return [
        speech[s : s + width] for s in np.linspace(0, max(0, len(speech) - width), k).astype(int)
    ]


def main(argv: list[str] | None = None) -> int:
    from faster_whisper import WhisperModel, decode_audio

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--device", default="cuda", choices=("cpu", "cuda"))
    parser.add_argument("--langs", nargs="*", default=list(TIER1))
    parser.add_argument("--clips", help="folder with the D60 clips")
    parser.add_argument("--cache")
    parser.add_argument("--out", default="reports/lid")
    args = parser.parse_args(argv)
    cache = cache_dir(args.cache)
    config: Any = config_for(args.device)
    recordings = {
        lang: (assemble(load_fleurs(cache, lang))[0], lang)
        for lang in args.langs
        if has_fleurs(cache, lang)
    }
    if args.clips:
        mixed = decode_audio(str(Path(args.clips) / "mixed-he-en-he.wav"), sampling_rate=SR)
        recordings["d60-english"] = (mixed[int(25.5 * SR) : int(45.5 * SR)], "en")
        recordings["d60-mixed"] = (mixed, "he")
    sampled = {name: windows(audio, 10) for name, (audio, _truth) in recordings.items()}
    rows = []
    for label, role in (("small", CLASSIFIER), ("large-v3", OTHER)):
        choice = resolve(config, role)
        started = time.monotonic()
        model = WhisperModel(choice.reference, device=args.device, compute_type="int8")
        load_s = time.monotonic() - started
        for n in (1, 3, 10):
            for name, (_audio, truth) in recordings.items():
                pool = sampled[name]
                picks = [pool[int(i)] for i in np.linspace(0, len(pool) - 1, min(n, len(pool)))]
                started = time.monotonic()
                answers = [
                    [(str(lang), float(p)) for lang, p in model.detect_language(audio=w)[2]]
                    for w in picks
                ]
                took = time.monotonic() - started
                decision = decide(mean_probabilities(answers))
                rows.append({
                    "model": label, "device": args.device, "windows": n, "recording": name,
                    "truth": truth, "decided": decision.language,
                    "correct": decision.language == truth, "p_he": round(decision.p_he, 3),
                    "p_top": round(decision.p_top, 3), "seconds": round(took, 2),
                    "per_window": round(took / len(picks), 3), "load_s": round(load_s, 1),
                })  # fmt: skip
                print(json.dumps(rows[-1]), flush=True)
        del model
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"lid_{args.device}.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
