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
| 5 — ASR backends | green (fakes + logic) / windows-pending (real model) | same gate; `pytest tests/unit/test_phase5_asr.py tests/integration/test_phase5_transcribe_stage.py` | 2026-08-28 | `test_transcribe_speech_fixture` and `test_detect_english_fixture` need SAPI + a local model — they skip here and print the measured detection confidence on Windows |
| 6 — assembly and transcript artifacts | green | same gate; golden `tests/goldens/transcript_he.md` reviewed | 2026-08-28 | Hebrew FTS matches whole tokens (`הסטטוס`, not `סטטוס`) — a property of `unicode61`, recorded in DECISIONS D14 |
| 6b — enrichment seams | green | same gate; `test_no_google_imports` and `test_no_oauth_flow_in_this_build` pass | 2026-08-28 | `NullSource` is the wired default; `FakeSource` proves the seam; `app/meetings.py` is the single owner of meeting creation |
| 7 — LLM and summarization | green (fakes) / **live_api pending** | same gate; `pytest tests/unit/test_phase7_llm.py tests/integration/test_phase7_summarize_stage.py` | 2026-08-28 | The four `live_api` tests are deselected by default and skip with no key. **Hebrew tokens-per-word is unmeasured** — closes with `python -m app.selftest live-llm --report live.json` (or `pytest -m live_api`) once a key is present |
| 8 — rendering and delivery (**M0**) | green | `python -m app.selftest pipeline --report m0.json` → `pipeline: OK` in ~1.2 s; goldens for both directions reviewed | 2026-08-28 | M0 asserts all six artifacts, schema-valid notes, state RENDERED, a clean pipeline.log and a wall-time budget |
| 9 — HTTP API | green | same gate; `pytest tests/integration/test_phase9_api.py` (22 tests) and `python -m app.selftest api` | 2026-08-29 | OpenAPI golden covers every §12 route; SSE is tested against a real uvicorn socket (DECISIONS D20) |
| 10 — frontend | green | `npm run typecheck && npm test` (17 unit tests) and `npx playwright test` (15 e2e, headless chromium) | 2026-08-29 | Found and fixed two real bugs: deep links 404'd (no SPA fallback) and concurrent FTS5 queries on one connection raised `InterfaceError` (DECISIONS D21) |
| 11 — tray, notifications, single instance | green | same gate; `pytest tests/unit/test_phase11_tray.py tests/integration/test_phase11_tray_wiring.py` (22 tests) | 2026-08-29 | All headless with `FakeNotifier`; the visual toast check is recorded in `docs/manual-checks.md` and gates nothing |
| 12 — detection (**M2 core**) | green (fakes + logic) / windows-pending (real signals) | `python -m app.selftest detect-e2e` → OK; `pytest tests/unit/test_phase12_evidence.py tests/integration/test_phase12_detector.py` (30 tests) | 2026-08-29 | `detect-shadow` skips here (no ConsentStore); T3 `test_registry_sees_self`, `test_registry_rearm`, `test_window_titles_enumerated` are written and skipped |
