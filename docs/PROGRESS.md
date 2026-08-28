# Progress

One line per phase: status, the command that proved it, date, deviations.
Statuses: **green** (proved here), **green-linux / windows-pending** (all
environment-independent tests green; `windows`/`audio_hw`/`gpu` tests written, collected
and skipped — see `docs/windows-run.md`), **blocked**.

## Environment

| Fact | Value |
|---|---|
| Probe | `ls /proc/sys/fs/binfmt_misc/WSLInterop` → **INTEROP_OFF** (2026-08-28) |
| Consequence | This shell cannot execute Windows binaries. Everything marked `windows`, `audio_hw` or `gpu` is written and collected but skipped here. |
| Toolchain | uv 0.10.11, CPython 3.13.12 (uv-managed), node 24.15.0 |
| Closing the gap | `docs/windows-run.md` — the exact command sequence from a Windows terminal |

## Phases

| Phase | Status | Proved by | Date | Notes |
|---|---|---|---|---|
| 0 — skeleton + harness | green | `ruff check . && mypy app && pytest -q && python -m app.selftest all` | 2026-08-28 | T1 speech fixture written; skipped off-Windows |
| 1 — config, paths, database | green | same gate; `selftest all` reports `fts5: true` | 2026-08-28 | **Measurement: FTS5 is present** in this SQLite (closes TECHNICAL-DESIGN §19.1); the `LIKE` fallback is implemented and tested by forcing `db.fts=off` |
| 2 — state machine, queue, worker | green | same gate + `pytest tests/integration/test_phase2_fuzz.py` (500-job fuzz) | 2026-08-28 | Worker logging is noisy by design on failure paths |
| 3 — audio, synthetic only | green | same gate + `pytest tests/integration/test_phase3_soak.py` (60-min soak, 13.5 s wall) | 2026-08-28 | Soak: durations sum to the input exactly (0 ms drift); `test_vad_on_speech_fixture` is windows-marked |
| 4 — audio on real hardware (the spike) | **windows-pending** | code + tests written; `pytest -m audio_hw` collects 6 tests, all skipped here (no WASAPI in WSL2) | 2026-08-28 | Closes on Windows with `uv run python -m app.selftest audio --report docs\spike-report.json` and `uv run pytest -m audio_hw`. The stop condition (`test_dual_stream_concurrent`) is unevaluated in this environment — the fallback ladder in TECHNICAL-DESIGN §4.0 was not needed to be descended because the spike has not run |
