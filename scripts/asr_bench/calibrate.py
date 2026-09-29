"""Tier 3: classify real recordings with the app's classifier, and how close each one was.

Give it meeting folders (each with ``audio/me.wav`` and/or ``audio/them.wav``), a folder
of meetings, or single WAV files. Each is read in place: recordings of other people are
never copied, and the report holds decisions and probabilities, no audio and no text.

    python scripts/asr_bench/calibrate.py --meetings <app data>/meetings --expect he
    python scripts/asr_bench/calibrate.py some.wav other.wav --transcribe

``--expect`` makes it a gate: every recording must classify as that language (exit 1
otherwise). The report gives the p_he distribution and each meeting's margin to the 0.50
threshold; any Hebrew meeting under 0.60 is listed for review with the product owner.
``--transcribe`` also routes and transcribes each one (the old route test), writing the
transcript beside the report, which stays on this machine (reports/ is not committed).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import config_for, environment

from app.asr.classify import HEBREW_THRESHOLD, WhisperClassifier
from app.asr.local import LocalAsr

REVIEW_BELOW = 0.60


def recordings(paths: list[str], meetings: str | None) -> dict[str, dict[str, Path]]:
    """name -> {track: wav}."""
    found: dict[str, dict[str, Path]] = {}
    folders = [Path(p) for p in paths]
    if meetings:
        folders += sorted(p for p in Path(meetings).iterdir() if p.is_dir())
    for path in folders:
        if path.is_file() and path.suffix.lower() == ".wav":
            found[path.name] = {"them": path}
            continue
        tracks = {t: path / "audio" / f"{t}.wav" for t in ("me", "them")}
        tracks = {t: wav for t, wav in tracks.items() if wav.is_file() and wav.stat().st_size > 44}
        if tracks:
            found[path.name] = tracks
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", help="meeting folders or WAV files")
    parser.add_argument("--meetings", help="a folder of meeting folders")
    parser.add_argument("--expect", help="every recording must classify as this language")
    parser.add_argument("--device", default="auto", choices=("auto", "cpu", "cuda"))
    parser.add_argument("--cuda-dir", nargs="*")
    parser.add_argument("--transcribe", action="store_true")
    parser.add_argument("--names", action="store_true", help="keep folder names in the report")
    parser.add_argument("--out", default="reports/asr-calibration")
    args = parser.parse_args(argv)

    config: Any = config_for(args.device)
    if args.cuda_dir:
        config.set("asr.cuda_dir", args.cuda_dir)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    found = recordings(args.paths, args.meetings)
    classifier = WhisperClassifier(config)
    rows: list[dict[str, Any]] = []
    for index, (name, tracks) in enumerate(found.items(), 1):
        decision = classifier.classify(tracks)
        label = name if args.names else f"recording-{index:02d}"
        row: dict[str, Any] = {
            "recording": label,
            "tracks": sorted(tracks),
            "language": decision.language,
            "route": decision.route,
            "forced": decision.forced,
            "p_he": round(decision.p_he, 3),
            "margin": round(decision.p_he - HEBREW_THRESHOLD, 3),
            "p_top": round(decision.p_top, 3),
            "top3": decision.top3,
            "windows": len(decision.windows),
            "seconds": round(decision.seconds, 2),
            "rule": decision.rule,
        }
        if args.expect:
            row["correct"] = decision.language == args.expect
        if args.transcribe and decision.rule != "no speech":
            backend = LocalAsr(config, role=decision.route)
            lines = []
            for track, wav in sorted(tracks.items()):
                for s in backend.transcribe(
                    wav, language=decision.transcribe_language, multilingual=decision.multilingual
                ):
                    lines.append(f"[{track} {s.start:7.1f}] {s.text}")
            backend.unload()
            (out / f"{label}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)

    spoken = [r for r in rows if r["rule"] != "no speech"]
    hebrew = [r["p_he"] for r in spoken if r["language"] == "he"]
    wrong = [r for r in rows if r.get("correct") is False]
    report = {
        "when": time.strftime("%Y-%m-%d %H:%M"),
        "environment": environment(),
        "recordings": len(rows),
        "without_speech": len(rows) - len(spoken),
        "expect": args.expect,
        "correct": sum(1 for r in rows if r.get("correct")),
        "misclassified": [r["recording"] for r in wrong],
        "p_he": {
            "min": min(hebrew, default=None),
            "median": round(statistics.median(hebrew), 3) if hebrew else None,
            "max": max(hebrew, default=None),
            "smallest_margin": round(min(hebrew) - HEBREW_THRESHOLD, 3) if hebrew else None,
        },
        "review_below_0_60": [
            r["recording"] for r in spoken if r["language"] == "he" and r["p_he"] < REVIEW_BELOW
        ],
        "classifier_seconds": {
            "mean": round(statistics.mean(r["seconds"] for r in spoken), 2) if spoken else None,
            "max": max((r["seconds"] for r in spoken), default=None),
        },
        "rows": rows,
        "passed": not wrong,
    }
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    print(
        json.dumps({k: v for k, v in report.items() if k != "rows"}, ensure_ascii=False, indent=1)
    )
    return 1 if wrong else 0


if __name__ == "__main__":
    raise SystemExit(main())
