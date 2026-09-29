"""Fetch about two minutes per language from FLEURS (dev split), on demand.

FLEURS (google/fleurs) is CC-BY 4.0. Its audio is never committed: this streams each
language's tar from Hugging Face and stops once it has ``--seconds`` of speech, saving
``<lang>.npz`` (16 kHz float32, one array per utterance) and ``<lang>.json`` (the
references) into the cache.

    python scripts/asr_bench/fleurs_fetch.py --tier 1
    python scripts/asr_bench/fleurs_fetch.py --tier 2          # every language FLEURS has
    python scripts/asr_bench/fleurs_fetch.py --langs de ja     # just these
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import tarfile
import urllib.request
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import FLEURS_CONFIGS, SR, TIER1, cache_dir, fleurs_dir, has_fleurs

BASE = "https://huggingface.co/datasets/google/fleurs/resolve/main/data"


def fetch(language: str, cache: Path, seconds: float) -> None:
    from faster_whisper import decode_audio

    config = FLEURS_CONFIGS[language]
    csv.field_size_limit(1 << 24)
    tsv = urllib.request.urlopen(f"{BASE}/{config}/dev.tsv", timeout=60).read().decode("utf-8")
    references = {}
    for row in csv.reader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        if len(row) > 2:
            references[row[1]] = row[2]  # file name -> raw transcription
    utterances: list[tuple[str, str, np.ndarray]] = []
    total = 0.0
    with (
        urllib.request.urlopen(f"{BASE}/{config}/audio/dev.tar.gz", timeout=120) as response,
        tarfile.open(fileobj=response, mode="r|gz") as tar,
    ):
        for member in tar:
            name = Path(member.name).name
            if not member.isfile() or name not in references:
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            pcm = decode_audio(io.BytesIO(handle.read()), sampling_rate=SR)
            utterances.append((name, references[name], pcm))
            total += len(pcm) / SR
            if total >= seconds:
                break
    folder = fleurs_dir(cache)
    np.savez(folder / f"{language}.npz", *[pcm for _n, _t, pcm in utterances])
    (folder / f"{language}.json").write_text(
        json.dumps(
            [{"file": n, "text": t} for n, t, _p in utterances], ensure_ascii=False, indent=1
        ),
        encoding="utf-8",
    )
    print(f"{language} ({config}): {len(utterances)} utterances, {total:.0f} s", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tier", type=int, choices=(1, 2))
    parser.add_argument("--langs", nargs="*", default=[])
    parser.add_argument("--seconds", type=float, default=125.0)
    parser.add_argument("--cache", help="where data goes (default: .cache/asr_bench)")
    parser.add_argument("--force", action="store_true", help="fetch again what is there")
    args = parser.parse_args(argv)
    cache = cache_dir(args.cache)
    languages = list(args.langs)
    if args.tier == 1:
        languages += list(TIER1)
    elif args.tier == 2:
        languages += sorted(FLEURS_CONFIGS)
    failed = []
    for language in dict.fromkeys(languages):
        if language not in FLEURS_CONFIGS:
            print(f"{language}: not in FLEURS", flush=True)
            continue
        if has_fleurs(cache, language) and not args.force:
            continue
        try:
            fetch(language, cache, args.seconds)
        except Exception as exc:  # one language failing must not stop the rest
            print(f"{language}: failed: {exc}", flush=True)
            failed.append(language)
    if failed:
        print("failed:", " ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
