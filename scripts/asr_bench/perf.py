"""Performance and resources: classifier time, peak memory, and one large model per meeting.

Runs what the TRANSCRIBE stage runs, in its order, on one recording: the classifier
(loaded, 5 windows, unloaded), then the one large model it chose over each track. A
thread samples this process's resident memory every 50 ms. Reports:

- classifier seconds, load included (gates: CPU <= 20 s, GPU <= 3 s);
- peak RSS while classifying and while transcribing (gate for the CPU: the D60 figure,
  4.6 GB, + 5 %);
- how many times the large model was loaded (R3: exactly one), in this process.

    python scripts/asr_bench/perf.py <meeting folder or wav> --device cpu
"""

from __future__ import annotations

import argparse
import gc
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import config_for, environment

from app.asr.classify import IsolatedClassifier
from app.asr.local import LocalAsr

CLASSIFIER_LIMIT_S = {"cpu": 20.0, "cuda": 3.0}
CPU_PEAK_LIMIT = 4.6e9 * 1.05


class Sampler:
    def __init__(self) -> None:
        import psutil

        self.process = psutil.Process()
        self.peak = 0
        self.phase_peaks: dict[str, int] = {}
        self.phase = "idle"
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            rss = self.process.memory_info().rss
            self.peak = max(self.peak, rss)
            self.phase_peaks[self.phase] = max(self.phase_peaks.get(self.phase, 0), rss)
            time.sleep(0.05)

    def __enter__(self) -> Sampler:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("recording", help="a meeting folder or a WAV file")
    parser.add_argument("--device", default="auto", choices=("auto", "cpu", "cuda"))
    parser.add_argument("--cuda-dir", nargs="*")
    parser.add_argument("--cpu-fast", action="store_true", help="asr.cpu_fast (greedy on CPU)")
    parser.add_argument("--clip-s", type=float, help="transcribe only this much of each track")
    parser.add_argument("--out", default="reports/asr-perf")
    args = parser.parse_args(argv)

    config: Any = config_for(args.device)
    if args.cuda_dir:
        config.set("asr.cuda_dir", args.cuda_dir)
    config.set("asr.cpu_fast", bool(args.cpu_fast))
    path = Path(args.recording)
    tracks = (
        {"them": path}
        if path.is_file()
        else {
            t: path / "audio" / f"{t}.wav"
            for t in ("me", "them")
            if (path / "audio" / f"{t}.wav").is_file()
        }
    )
    if args.clip_s:
        # The classifier still hears the whole meeting; only the transcription is cut
        # short, to measure its peak on a clip of known length (D60: 100 s).
        from common import SR, cache_dir, write_wav
        from faster_whisper import decode_audio

        clipped = {}
        for track, wav in tracks.items():
            audio = decode_audio(str(wav), sampling_rate=SR)[: int(args.clip_s * SR)]
            clipped[track] = write_wav(cache_dir(None) / "work" / "perf" / f"{track}.wav", audio)
    loads: list[str] = []

    def factory(**kwargs: Any) -> Any:
        from faster_whisper import WhisperModel

        loads.append(f"{sampler.phase}:{Path(str(kwargs['model_size_or_path'])).name}")
        return WhisperModel(**kwargs)

    with Sampler() as sampler:
        sampler.phase = "classify"
        started = time.monotonic()
        # The one the TRANSCRIBE stage uses: on the CPU, in a process of its own.
        classifier = IsolatedClassifier(config)
        decision = classifier.classify(tracks)
        classify_s = time.monotonic() - started
        gc.collect()
        sampler.phase = "between"
        time.sleep(0.3)
        sampler.phase = "transcribe"
        started = time.monotonic()
        backend = LocalAsr(config, role=decision.route, model_factory=factory)
        segments = 0
        for _track, wav in sorted((clipped if args.clip_s else tracks).items()):
            segments += len(backend.transcribe(
                wav, language=decision.transcribe_language, multilingual=decision.multilingual
            ))  # fmt: skip
        transcribe_s = time.monotonic() - started
        device = backend.device
        backend.unload()
        sampler.phase = "done"
        time.sleep(0.2)

    audio_s = 0.0
    import wave

    for wav in (clipped if args.clip_s else tracks).values():
        with wave.open(str(wav), "rb") as handle:
            audio_s = max(audio_s, handle.getnframes() / handle.getframerate())
    large_loads = [load for load in loads if load.startswith("transcribe:")]
    limit = CLASSIFIER_LIMIT_S.get(decision.device or "cpu", 20.0)
    report = {
        "when": time.strftime("%Y-%m-%d %H:%M"),
        "environment": environment(),
        "device": device,
        "classifier_device": decision.device,
        "audio_s": round(audio_s, 1),
        "language": decision.language,
        "route": decision.route,
        "classifier_s": round(classify_s, 2),
        "classifier_isolated": classifier.isolated,
        "classifier_limit_s": limit,
        "transcribe_s": round(transcribe_s, 1),
        "real_time_factor": round(transcribe_s / audio_s, 2) if audio_s else None,
        "segments": segments,
        "peak_rss_gb": {k: round(v / 1e9, 2) for k, v in sampler.phase_peaks.items()},
        "loads": loads,
        "one_large_model": len(large_loads) == 1,
        "passed": classify_s <= limit
        and len(large_loads) == 1
        and (device != "cpu" or sampler.phase_peaks.get("transcribe", 0) <= CPU_PEAK_LIMIT),
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    name = f"perf-{device}{'-fast' if args.cpu_fast else ''}.json"
    (out / name).write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report, indent=1))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
