# ASR benchmarks (the multilingual epic, DECISIONS.md D80)

Repeatable checks that the language classifier picks the right model and that each model
transcribes its languages well. They drive the app's own code (`app/asr/classify.py`,
`LocalAsr`), so what they measure is what ships.

Models come from the app home (`UP_HOME`, filled by `upshot --prepare`). Downloaded data
goes to `.cache/asr_bench/` and reports to `reports/`, both ignored by git. FLEURS
(google/fleurs) is CC-BY 4.0; its audio is never committed.

| Script | What |
|---|---|
| `fleurs_fetch.py` | Streams ~2 min per language of FLEURS (dev split) into the cache: `--tier 1`, `--tier 2`, or `--langs` |
| `bench.py` | Tier 1 / Tier 2: classify, route and transcribe each language; WER, CER and script share per part; `report.json` plus REF/HYP files. Exit 1 when a Tier 1 gate fails |
| `calibrate.py` | Tier 3: classify real recordings in place (meeting folders or WAVs); p_he distribution and each margin to 0.50; `--expect he` makes it a gate |
| `perf.py` | Classifier time, peak memory while classifying and transcribing, and the number of large-model loads (R3) on one recording |
| `lid_bench.py` | Whisper small against large-v3 as the classifier: accuracy and cost at 1, 3 and 10 windows |

Each utterance is levelled to RMS 0.1 before scoring. About half of FLEURS is recorded
~40 dB low, and both models skip sentences that quiet; unlevelled, the WER measures the
recording level rather than the model.

## Tiers and gates

- **Tier 1**, every change to the ASR path: FLEURS he, en, es, fr, ru, ar (plus the D60
  clips with `--clips`). Classification 100 %; WER he <= 25 %, en <= 12 %, es <= 6 %,
  fr <= 8 %, ru <= 12 %, ar recorded; script share >= 0.95; no language more than 2 points
  worse than its baseline; one large-model load per recording.
- **Tier 2**, before a release and on any model or revision change: every Whisper
  language FLEURS has. Classification >= 98 % overall, every miss reviewed (languages
  Whisper itself confuses, such as bs/hr/sr or ms/id, pass if the transcript is usable);
  script share >= 0.9; the direction right.
- **Tier 3**, before a release: every real meeting on the development machine classifies
  as expected. Any Hebrew meeting under p_he 0.60 is reviewed with the product owner.

```sh
python scripts/asr_bench/fleurs_fetch.py --tier 1
UP_HOME=<app home> python scripts/asr_bench/bench.py --tier 1 --clips <folder with the D60 clips>
UP_HOME=<app home> python scripts/asr_bench/calibrate.py --meetings <app data>/meetings --expect he
UP_HOME=<app home> python scripts/asr_bench/perf.py <meeting folder> --device cpu --clip-s 100
```

On a GPU from WSL the project venv has no CUDA libraries: install `nvidia-cublas-cu12` and
`nvidia-cudnn-cu12==9.*` into a scratch folder, put their `lib` folders on
`LD_LIBRARY_PATH`, and pass the cuBLAS one as `--cuda-dir`.

The real-model unit tests (`@pytest.mark.gpu`, which run on the CPU too) are excluded from
the default run: `pytest -m gpu`.
