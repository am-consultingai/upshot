# Current state

Where this repository stands on **2026-08-29**, after building `EXECUTION-PLAN.md` phases
0–14 and the work that followed. Written to be read cold, by someone who was not here.

- **What it is:** a local Windows application that records both sides of a meeting,
  transcribes it locally, summarizes it with an LLM, renders HTML and files it in a
  browsable timeline. No cloud service is required; no Google account exists in this build.
- **Design contract:** `DESIGN.md`, `TECHNICAL-DESIGN.md`, `DETECTION.md`, `STACK.md`,
  `SECURITY-AND-AUTH.md`. Every judgment call made while building is in `DECISIONS.md`
  (31 entries). Phase-by-phase status is in `PROGRESS.md`.

---

## 1. Status at a glance

| | |
|---|---|
| Phases 0–14 | **complete**, one commit each |
| Python suite | **338 passed, 24 skipped, 5 deselected** (`live_api`) |
| Frontend | **17 vitest** unit tests, **20 Playwright** e2e tests, headless chromium |
| `ruff` / `mypy --strict` | clean, 85 modules |
| M0 `selftest pipeline` | ✅ 0.6 s against a 120 s budget |
| M1 `selftest capture-e2e` | ✅ **synthetic capture mode** — see §4 |
| M2 `selftest detect-e2e` | ✅ |
| Size | 12.5k lines of app, 6.1k lines of tests, 89 config keys |

The per-phase gate, which every commit passes:

```bash
uv run ruff check . && uv run mypy app && uv run pytest -q && uv run python -m app.selftest all
```

---

## 2. What exists

**Capture.** Two WASAPI streams (microphone + render loopback) → callbacks that only copy
bytes → a writer thread that resamples to 16 kHz mono int16, cuts chunks on a VAD-silence
boundary near 60 s (hard cut at 70 s), and writes `manifest.jsonl` with a durability order
that is asserted by test: `writeframes → flush → fsync(wav) → append manifest → fsync`.
A 60-second pre-roll ring means a detected meeting starts *before* the trigger. A device
change costs a `gap_ms` marker, never the meeting.

**Pipeline.** SQLite job queue (`claim_next` is one `UPDATE … RETURNING`), a preemptible
worker, five stages — transcribe, assemble, summarize, render, deliver — each idempotent
and restartable. Exit criterion: a randomized 500-job fuzz loses nothing.

**ASR.** faster-whisper behind a protocol, with the CUDA probe, the Windows
`add_dll_directory` **plus** PATH prepend fix, a warmup inference, CPU fallback, and
`condition_on_previous_text=False`. Language is resolved once from the first chunk with
speech and then pinned. Optional speaker diarization (§5).

**Assembly.** Two segment lists merge into one speaker-tagged timeline: echo suppression
(rapidfuzz ≥ 85 within an overlap window), 2-second coalescing, glossary correction,
`transcript.json` + `transcript.md`, every turn indexed for search.

**Summarization.** Map-reduce windowed by *real* tokens, six providers behind one protocol
(§6), a JSON Schema contract, prompt caching on the Anthropic path, and sanity gates that
raise `NEEDS_REVIEW` rather than accepting a bad summary silently.

**Rendering and delivery.** One Jinja template → a styled page and a premailer-inlined
email variant, RTL-correct by construction (logical CSS properties only, asserted), no
external resources, `data-at-ms` so the summary seeks into the audio. Delivery defaults to
**draft**: render, store, notify.

**Detection.** The four-tier state machine from `DETECTION.md`, pure evidence scoring
tested against every worked example in §5, the ConsentStore watch with the re-arm that the
API requires, and **shadow mode as the install default** — it evaluates and logs and
commits nothing.

**Interfaces.** FastAPI on 127.0.0.1 with Host-header, cookie-auth and CSRF middleware in
that order; SSE; ranged audio. React + TanStack Query + Tailwind UI with en/he catalogues
and runtime direction switching. A pystray tray whose every decision comes from one pure
function.

**Packaging.** PyInstaller one-dir spec with the hidden imports PyInstaller cannot see,
an Inno per-user installer, and a `build.ps1` that runs `--selftest imports` and
`--selftest pipeline` **against the freeze** and fails the build if either does.

---

## 3. How to run it

```bash
# headless: server + worker + detector, no tray
uv run python -m app.main

# with the tray icon (Windows)
uv run python -m app.tray

# fakes end to end, for a demo with no model and no key
MA_AUDIO__CAPTURE='"synthetic"' MA_ASR__BACKEND='"fake"' MA_LLM__PROVIDER='"fake"' \
MA_AUDIO__VAD='"energy"' uv run python -m app.main
```

The frontend must be built once (`cd frontend && npm ci && npm run build`); FastAPI serves
`frontend/dist`. `docs/windows-run.md` is the runbook for everything that needs Windows.

---

## 4. What is **not** proved

This was built in a **WSL2 shell with Windows interop off**, so nothing has ever executed
on Windows. 24 tests are written, collected and skipped; `PROGRESS.md` lists each with the
command that closes it. The load-bearing ones:

- **Phase 4's stop condition has not run.** `test_dual_stream_concurrent` decides whether
  PyAudioWPatch can hold a capture and a loopback stream together for hours. Everything
  downstream assumes it can. If it fails, descend the ladder in `TECHNICAL-DESIGN.md` §4.0
  — only `app/audio/wasapi.py` changes.
- **M1 ran in synthetic mode.** The fixture is fed through `SyntheticCapture` instead of
  being played out a real render endpoint and captured on loopback. The same command
  reports `mode=wasapi` on Windows.
- **No frozen build exists.** `dist/` has never been produced.
- **No real LLM call has been made** through any provider — every test uses an injected
  transport. The Settings **Test** button closes that in one click per provider.
- **Measurements still open:** clock drift and xruns (Phase 4), Hebrew tokens-per-word
  (Phase 7, needs a key), language-detection confidence on a first chunk (Phase 5).
  FTS5 presence is **closed** — it is available.

---

## 5. Added after the phases, on request

**Diarization** (`asr.diarization = off|onnx|fake`, **off by default**). Splits `THEM` into
`THEM_1/2/3`; the `me` track is never relabelled because two-track capture already settles
it. ONNX via sherpa-onnx rather than pyannote/torch — the design's "~2.5 GB" objection was
a Linux number (`DECISIONS.md` D28). Measured: clustering threshold **0.6** is the only
value correct on both reference recordings, 10–12× real time on CPU, ~19 MB of wheels plus
~37 MB of models, no Hugging Face account. Hebrew accuracy is unmeasured.

**Three behaviours the design declared but nothing wired** (D26): `POST /api/import` now
ingests audio into real chunks and runs the pipeline; the glossary correction pass runs in
`assemble`; and `job_policy = "scheduled"` honours a nightly window instead of silently
behaving like `asap`.

**Provider choice and secrets** (§6).

**Bugs found by tests, each fixed** — Jinja autoescaping silently stripped the Hebrew font
stack from every email; concurrent FTS5 queries on one connection raised `InterfaceError`;
deep links like `/m/<id>` returned 404; two writer threads tripled a recording's duration;
the e2e suite made a real HTTPS call to Google; provider selection snapped back until the
server responded.

---

## 6. Summarization providers

`llm.provider` ∈ `anthropic` (**default**) · `openai` · `gemini` · `claude-subscription` ·
`ollama` · `fake`. Keys live in the OS credential store behind a **write-only**
`PUT /api/settings/secrets`; the `GET` returns booleans. A meeting flagged `sensitive`
still forces Ollama regardless of the setting.

`claude-subscription` spawns the Claude Code CLI the user signed into themselves and
**holds no credential** — see `inference-subscription.md` for the rules, the evidence and
the mechanism.

---

## 7. Known gaps, in the order I would close them

1. **The Windows build has never run.** Everything in §4 depends on it.
2. **No setup UI.** `bootstrap.py` and `preflight()` exist and are tested; first run today
   is `--bootstrap` plus hand-editing `app_config.json`. `EXECUTION-PLAN.md` Phase 13 also
   calls for a 5-second two-track test recording with live level meters — the one check
   that proves capture works before a real meeting depends on it. No test in the plan's
   Phase 13 table would have caught its absence.
3. **Retention is configured but not swept** (D27). `retention.audio_days` defaults to 30
   and nothing deletes anything. A config key that promises deletion and does not delete
   is a trust problem.
4. **Settings exposes 3 of 89 config keys.** About 18 deserve a UI; the rest are seams,
   tuned defaults, or test-only switches.
