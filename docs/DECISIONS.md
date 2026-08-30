# Decisions taken during implementation

Every judgment call the plan left open, what the alternatives were, and why.

## D1 — Windows-only and heavy dependencies are imported lazily, inside functions
**Choice.** `pyaudiowpatch`, `pywin32`, `pycaw`, `windows-toasts`, `pystray`,
`faster_whisper`, `anthropic` and `keyring` backends are imported inside the function that
needs them, never at module scope.
**Alternatives.** Module-scope imports with try/except at the top; a compatibility shim
package.
**Why.** Phase 0's `test_imports_all_modules` walks `app/**` and imports every module. That
test is the tripwire for circular imports and typos, and it has to pass on Linux, in CI, and
inside a PyInstaller freeze. Lazy imports are the only way every module stays importable
everywhere without weakening that assertion.

## D2 — `/etc/wsl.conf` was not modified
**Choice.** Interop stays off; `docs/windows-run.md` carries the exact enabling steps and
the command sequence for the Windows-only gates.
**Alternatives.** `docs/next-session.md` §4.3 invites appending `enabled = true` under
`[interop]`.
**Why.** The session's operating instruction is that nothing outside the project folder may
be created, referenced, altered or deleted. `/etc/wsl.conf` is outside it, and enabling
interop requires a `wsl --shutdown` this shell cannot issue anyway, so the instruction costs
nothing here.

## D3 — Skips are reported as `ok` with a `skipped:` detail, not as a fourth check state
**Choice.** A self-test check that cannot run in this environment is emitted as
`{"ok": true, "detail": "skipped: <why>", "metrics": {"skipped": 1}}`.
**Alternatives.** A `"skipped"` key on the check object; a third value for `ok`.
**Why.** The report shape is fixed by `EXECUTION-PLAN.md` Phase 0 and must never change.
`metrics` is a free-form object by that same contract, so the skip is machine-readable
without touching the schema, and the suite's `ok` stays a straight conjunction.

## D4 — `ruff` never sees Markdown
**Choice.** `docs` and `*.md` are excluded in `[tool.ruff]`.
**Why.** `ruff format .` reformatted the Python code blocks inside `EXECUTION-PLAN.md` and
`TECHNICAL-DESIGN.md` on the first run. The design docs are the contract; a formatter must
not edit them. The two files were restored byte-for-byte and the exclusion makes it
impossible to repeat.

## D5 — `TRANSCRIBED` means "assembled"
**Choice.** The `transcribe` stage runs with the meeting in `TRANSCRIBING` and leaves it
there; the `assemble` stage is what moves it to `TRANSCRIBED`.
**Alternatives.** Add an `ASSEMBLING`/`ASSEMBLED` pair, as `DESIGN.md` §10 sketches.
**Why.** `TECHNICAL-DESIGN.md` §6.1 is the implementation-level contract and lists exactly
eight forward states with no assembly state, while §6 lists five stages. Mapping two stages
onto one state keeps §6.1's state list literal, and `TRANSCRIBED` then means what a user
would expect — `transcript.md` exists.

## D6 — `transcript_turns` backs search; `transcripts_fts` is created outside the migrations
**Choice.** Migration 0001 creates a plain `transcript_turns` table alongside the §3.1
tables. The FTS5 virtual table is created at connect time by `migrate.ensure_fts()` after
the probe.
**Alternatives.** Put the virtual table in the migration and let migration 0001 fail on a
SQLite without FTS5; keep no plain table and search nothing when FTS5 is missing.
**Why.** §19.1 requires the application to run without FTS5, and the plan requires a `LIKE`
fallback — `LIKE` needs a real table to scan. Creating the virtual table outside the
migration also means a database created on a build without FTS5 gains the index the first
time it is opened on a build that has it.

## D7 — Config carries which implementation is wired
**Choice.** `asr.backend`, `audio.capture`, `llm.provider`, `delivery.notifier`,
`detection.sources`, `enrichment.source`, `secrets.backend` and `db.fts` are config keys on
top of the `TECHNICAL-DESIGN.md` §14 document.
**Alternatives.** Select fakes with monkeypatch or environment-sniffing inside tests.
**Why.** `EXECUTION-PLAN.md` §16.3 requires fakes to be selected by config so the wiring
itself is exercised. Each added key is exactly one such seam, and every one of them is
validated against an enum, so a typo fails at startup rather than at runtime.

## D8 — Chunk boundaries are chosen by the **energy** stage, not by Silero
**Choice.** `ChunkWriter._find_cut` calls `EnergyGate`, not `TwoStageVad`.
**Alternatives.** Run the full two-stage VAD over the ±10 s search window.
**Why.** The boundary rule is "do not cut through sound", not "do not cut through speech":
music, a ringing phone, or hold tone are all things a chunk boundary should avoid, and
Silero classifies every one of them as silence. It is also the always-on stage
(`TECHNICAL-DESIGN.md` §4.7), which is what the writer thread can afford. `test_chunk_
prefers_silence_boundary` uses a tone as its stand-in for sound, and would be meaningless
under a speech-only detector.

## D9 — The resampler's delay line is flushed at end of stream
**Choice.** `Resampler.flush()` drains soxr's internal delay (~477 samples at 48→16 kHz)
and `Recorder.stop()` writes it as real audio.
**Alternatives.** Ignore it (roughly 30 ms lost per meeting per track).
**Why.** `test_resample_sample_count` asserts 10.0 s in → 160 000 ±16 samples out. Without
the flush the streaming resampler is 477 samples short, and the miss is silent — exactly
the class of bug the assertion exists to catch.

## D10 — A reopened device records a floored gap
**Choice.** On `StreamError` the recorder measures the reopen with the injected clock and
writes `gap_ms = max(measured, 100)`.
**Alternatives.** Write the measured value alone (zero under a `FakeClock`); estimate the
lost audio from the device.
**Why.** The audio actually lost while a device is gone is unknowable — WASAPI does not
report it. The floor states the honest minimum: a reopen is never free. What the manifest
must preserve is that the timeline has a hole at all, which is what `t0_ms` carries
forward, and that is asserted by `test_gap_marker_written`.

## D11 — `NEEDS_REVIEW` is sticky; jobs keep running under it
**Choice.** Once a meeting is `NEEDS_REVIEW`, the worker stops managing its state: later
stages still run and still write their artifacts, but nothing moves the meeting back onto
the happy path.
**Alternatives.** Flag review only at the end of the pipeline; add a `needs_review` column.
**Why.** The plan requires "meeting state `NEEDS_REVIEW`, artifacts still written"
(Phase 7) while `TECHNICAL-DESIGN.md` §3.1 has no column for a review flag. Making the
state sticky satisfies both without touching the schema, and `/attention` then has exactly
one thing to query.

## D12 — Which VAD is wired is a config key (`audio.vad`)
**Choice.** `two_stage` (default, energy + Silero) or `energy`.
**Alternatives.** Always two-stage; monkeypatch Silero out in tests.
**Why.** `resolve_language` asks "does this track carry enough sound to detect a language
from". Synthetic fixtures are sound but not speech, and Silero correctly rejects them — so
without a seam every language test would need real recorded speech. The key also covers a
real case: a machine where onnxruntime will not load still needs a working VAD.

## D13 — `segments.json` is the transcribe stage's artifact
**Choice.** `transcribe` writes `segments.json` (both tracks, meeting-relative
timestamps); `assemble` turns it into `transcript.json` and `transcript.md`.
**Alternatives.** Have `transcribe` write `transcript.json` directly.
**Why.** `TECHNICAL-DESIGN.md` §3.2 defines `transcript.json` as the assembled artifact
(echo-suppressed, coalesced), and stage idempotency needs each stage to own exactly one
output it can check for. Two files, two owners, two skip checks.

## D14 — Hebrew search matches whole tokens
**Observation, not a choice.** `transcripts_fts` uses `unicode61 remove_diacritics 0`, so
`סטטוס` does not match the token `הסטטוס` — Hebrew's attached prefixes are part of the
word. The `LIKE` fallback *does* match substrings, so the two search paths differ in
recall.
**Why it is left alone.** Changing it means a custom tokenizer or prefix indexing, which is
out of scope for V1 and would weaken `test_fts_hebrew_diacritics`. The UI search box passes
the user's text through unchanged, and the plan's own test asks only for "search a Hebrew
word → correct meeting id and snippet offset", which holds.

## D15 — Structured output via `messages.create(output_config.format)`, not `messages.parse`
**Choice.** `AnthropicClient` calls `client.beta.messages.create(...)` with
`output_config={"effort": ..., "format": {"type": "json_schema", "schema": NOTES_SCHEMA}}`,
reads the JSON out of the first text block, and validates it with `jsonschema`.
**Alternatives.** `TECHNICAL-DESIGN.md` §9.2 writes `client.messages.parse(...)` with the
same `output_config`.
**Why.** In the current SDK, `messages.parse()` takes `output_format=<Pydantic model>` and
returns `parsed_output`; the raw-JSON-Schema path is `messages.create` with
`output_config.format`. Our schema is a raw JSON Schema by §9.3, and the `betas` /
`fallbacks` parameters the same section requires are only accepted on
`client.beta.messages.*`. The intent of §9.2 — the schema is the contract, validation is
not free-text parsing — is preserved exactly; only the SDK entry point differs.

## D16 — A broken credential store degrades to the environment
**Choice.** `KeyringSecrets.get` catches any exception from `keyring` and returns `None`
after a warning, so `config.secret(...)` falls through to `ANTHROPIC_API_KEY`.
**Why.** On this Linux box `keyring` raises `NoKeyringError` with no backend installed, and
that took down `selftest live-llm` — a diagnostic that must never crash. Writes still
raise: a secret that was not saved has to be reported.

## D17 — A draft delivery completes its job without advancing the meeting
**Choice.** `StageContext.hold_state`; the `deliver` stage sets it in `draft` mode and the
worker then leaves the meeting at `RENDERED`.
**Alternatives.** Let the stage move the meeting to `DELIVERED`; skip enqueuing `deliver`
at all in draft mode.
**Why.** The plan requires `test_draft_mode_does_not_send` to end at `RENDERED` "and marked
deliverable", while the job itself must still run (it writes `delivery.json` and fires the
toast). Not enqueuing the stage would mean the draft is never prepared.

## D18 — `test_no_oauth_flow_in_this_build` scans code, not prose
**Change to a test I wrote earlier in this build.** The first version matched the raw file
text, and `app/mail.py`'s docstring — "no Gmail API, no OAuth, no consent screen" —
tripped it. The assertion now strips comments and docstrings with `tokenize` before
matching, and additionally bans `consent screen`. The property under test is unchanged and
the scan is strictly harder to satisfy than before; only the prose exemption is new.

## D19 — `{{ css }}` is rendered with `|safe`
**Bug found by the golden files.** Jinja autoescaping HTML-escaped the quotes inside the
`<style>` block, so `font-family: "Segoe UI", "Arial Hebrew"` arrived as `&#34;Segoe UI&#34;`
and cssutils silently dropped the declaration — the Hebrew font stack disappeared from
every email. The stylesheet is ours, not user input, so it is marked safe; everything else
in the template stays escaped.

## D20 — The middlewares are pure ASGI, and SSE is tested against a real socket
**Choice.** `HostHeaderMiddleware`, `AuthMiddleware` and `CsrfMiddleware` are plain ASGI
callables, not `BaseHTTPMiddleware` subclasses; `test_sse_emits_state_change` starts
`LocalServer` on an ephemeral port and reads the stream with a real `httpx` client.
**Alternatives.** Keep `BaseHTTPMiddleware` and test SSE through `TestClient`.
**Why.** `BaseHTTPMiddleware` buffers the response body, which is wrong for an endpoint
whose body never ends, and Starlette's `TestClient` cannot read an unbounded response at
all — it runs the app to completion before returning (a minimal FastAPI app reproduces the
hang with no project code involved). Both changes make the tested path closer to
production: real middleware ordering, a real socket, a real client.

## D21 — Every SQL statement runs under a connection lock, with its rows materialized
**Bug found by the frontend.** The /attention page issues four `GET /api/meetings?state=`
requests at once. FastAPI runs sync endpoints in a threadpool, so several threads hit the
one SQLite connection — and an **FTS5 cursor is not safe for concurrent use on a single
connection**, which surfaced as `sqlite3.InterfaceError: bad parameter or other API
misuse`. Reproduced in four threads outside the API before fixing.
**Choice.** `db.dao.Connection` overrides `execute`/`executemany` to hold an `RLock` and
return a `Result` whose rows were already fetched under that lock.
**Alternatives.** A thread-local connection per thread (more moving parts, and WAL
readers still need care around the FTS writer); dropping FTS.
**Why.** A desktop app's query volume does not need parallel SQLite, and serializing is
the one change that cannot be got subtly wrong later.

## D22 — The server serves the SPA for unknown non-API paths
**Bug found by the frontend.** `/settings`, `/m/<id>`, `/attention` are client-side routes;
the backend had no handler, so a deep link — including the one a toast opens — returned
404. Added a catch-all that serves `index.html`, plus an explicit `/api/{rest:path}` 404 in
front of it so an unknown API path never returns the HTML shell for any method.

## D23 — A verdict suppresses re-waking on the same process until the mic is released
**Choice.** After a shadow verdict or a discard, `Detector.suppressed_process` holds that
process name; `_tick_idle` ignores it until the microphone is released.
**Alternatives.** Re-wake immediately (what the first implementation did).
**Why.** In shadow mode the detector reached a verdict and then, with the mic still held,
re-armed and reached the same verdict again every ~11 seconds — dozens of duplicate
`detector_events` and both audio streams re-opening continuously for a meeting it had
already decided about. The suppression is per-process and clears the moment the mic is
released, so a genuine second meeting still wakes it.

## D24 — The M2 negative is a media app holding the mic, not a browser playing video
**Choice.** `detect-e2e` asserts two negatives: watching a video (loopback audio, nobody
holds the microphone) never wakes the detector at all, and a media/voice-note app that
*does* hold the mic peaks at 3 and is logged as a `near_miss`.
**Alternatives.** The gate's wording is "a simulated YouTube pattern produces a
`near_miss`".
**Why.** Watching YouTube does not take the microphone, so it cannot produce a near miss —
by `DETECTION.md` §8 a near miss is a *wake* that peaked above the watermark, and there is
no wake. Making the browser hold the mic to force one would have simulated a Meet call and
scored 5 — correctly. Both real negatives are asserted instead of one impossible one.

## D25 — The wake tick scores immediately
**Choice.** `_tick_idle` falls straight into `_tick_awake`, so the sustain window opens on
the same tick the microphone is acquired.
**Why.** `DETECTION.md` §3 Tier 1 says evidence collection starts "within ~200 ms" of the
wake. Deferring it to the next tick added a second to every commit and made the sustain
window one tick shorter than the configured value.

## D26 — Three design behaviours that were declared but not wired, finished before sign-off
A pass over the design against the code found three places where a documented behaviour
existed only as a config key or an unused function. Each is now implemented and asserted
in `tests/integration/test_completeness.py`:

1. **`POST /api/import` produced a meeting with no audio.** `DESIGN.md` §7 says an
   imported file "runs the pipeline from `TRANSCRIBING`", but the route only saved the
   upload. `app/audio/ingest.py` now converts it (ffmpeg when it is not a 16-bit WAV),
   writes real chunk files and a manifest on the `them` track, and finishes the meeting so
   the queue picks it up. The M0 selftest uses the same function, so the gate and the
   product share one path.
2. **The glossary was used once, not twice.** `DESIGN.md` §11 has it feeding Whisper's
   `initial_prompt` *and* a correction pass before summarization. `apply_corrections` was
   written in Phase 5 and never called; `assemble` now runs it over the segments and
   reports the count in `meta.json`.
3. **`job_policy = "scheduled"` behaved exactly like `asap`.** A config value that
   silently does the opposite of what it says is worse than one that is absent, so the
   worker now honours a nightly window (`schedule.hour`, `schedule.hours`).

## D27 — Known gap: retention is configured but not swept
`retention.audio_days` (default 30) and `retention.transcript_days` are in the shipped
config, and nothing deletes anything yet. The retention sweep belongs to **M4** in
`DESIGN.md` §17 and no phase in `EXECUTION-PLAN.md` implements or gates it, so it is left
unbuilt rather than half-built. It is listed here because a config key that promises
deletion and does not delete is a trust problem the next session should close first.

## D28 — Measurement: diarization does not cost 2.5 GB on Windows
**`STACK.md` and `DESIGN.md` §20.1 defer diarization because "pyannote drags in PyTorch
(~2.5 GB install)". Measured on 2026-08-29, that figure is Linux-specific and wrong for
this product.**

| Component | Measured |
|---|---|
| `ivrit-ai/pyannote-segmentation-3.0` weights | 6.0 MB |
| `pyannote/wespeaker-voxceleb-resnet34-LM` weights | 26.7 MB |
| **diarization weights, total** | **~33 MB** (the ASR model is 3,091 MB) |
| `torch` 2.13.0 `cp313-win_amd64` wheel | 122 MB compressed |
| `sherpa-onnx` + `sherpa-onnx-core` (`win_amd64`) | 2.3 MB + 16.5 MB |

The 2.5 GB is the Linux torch install, which pulls the `nvidia-*-cu12` wheels. The Windows
wheel is self-contained CPU-only, and this product is Windows-only.

**Two other facts worth having on record:**

1. Upstream `pyannote/speaker-diarization-3.1` is `gated=auto` — it needs a Hugging Face
   account, accepted terms and a token at load time, which is unshippable in a desktop
   installer. `ivrit-ai/pyannote-speaker-diarization-3.1` is **ungated**, and its
   `config.yaml` points at `ivrit-ai/pyannote-segmentation-3.0` (ungated) and
   `pyannote/wespeaker-voxceleb-resnet34-LM` (ungated), so the pipeline is token-free end
   to end. That is what the fork is for.
2. A torch-free path exists: `sherpa-onnx` runs the same two models as ONNX, and this
   build already ships `onnxruntime` for Silero VAD.

**Nothing has changed in the build.** Diarization is still Post-V1 per
`EXECUTION-PLAN.md` §14 and is not implemented. What has changed is that the *reason* for
deferring it no longer holds on the target platform, so the decision should be re-taken on
its merits — the open question is accuracy on Hebrew multi-speaker audio, which no one in
this project has measured. The seam is ready either way: `Segment.speaker` is a `str`, not
an enum (`TECHNICAL-DESIGN.md` §8).

**Not verified:** the sherpa-onnx diarization API shape, ONNX-vs-torch accuracy in
practice, the unpacked torch footprint on Windows, and quality on Hebrew audio. The
ivrit-ai diarization repo is a re-hosted fork of upstream pyannote, **not** a Hebrew
fine-tune — diarization is acoustic rather than lexical, so that is expected.

## D29 — Diarization is implemented, ONNX-first, off by default — and the threshold is measured
**Choice.** `app/asr/diarize.py` behind a `Diarizer` protocol with three implementations:
`OnnxDiarizer` (sherpa-onnx: pyannote segmentation + a wespeaker embedding, both ONNX),
`FakeDiarizer` (config-selected, deterministic), and `None` when `asr.diarization = "off"`,
which is the default. It runs inside the `transcribe` stage, over the **whole** `them`
track reassembled on the meeting's timeline — diarizing per chunk would produce speaker
ids that disagree across chunk boundaries.

**Why inside `transcribe` and not as a sixth stage.** `TECHNICAL-DESIGN.md` §3.1 and
`STAGE_ORDER` fix the stage list at five, and the retry route, the OpenAPI golden and both
milestone gates enumerate it. Diarization consumes the same audio the stage already reads
and its output belongs in `segments.json`, so it costs nothing to keep the stage list
literal.

**Measured threshold.** `FastClusteringConfig.threshold` decides how many speakers come
out. Swept against the two reference recordings published by k2-fsa:

| threshold | 2-speaker sample | 4-speaker sample |
|---|---|---|
| 0.4 | 2 ✅ | 6 ❌ |
| 0.5 | 2 ✅ | 5 ❌ |
| **0.6** | **2 ✅** | **4 ✅** |
| 0.7 | 2 ✅ | 3 ❌ |
| 0.8 | 3 ❌ | 2 ❌ |

**0.6 is the only value correct on both, and is now the default** — the 0.5 in the
sherpa-onnx examples is wrong for this workload. Speed: **10–12× real time on CPU**, so a
45-minute meeting diarizes in about four minutes on one core.

**Pinning the speaker count makes it worse.** With `num_clusters` forced to the true value
the clusterer *under*-segmented (2 → found 1; 4 → found 3), so `asr.diarization_speakers`
stays at `-1` (auto) and the UI should not offer "I know there were N people" as a fix.

**Still unmeasured:** accuracy on Hebrew, and on real meeting audio from this recorder.
The reference samples are English and Chinese. The `them` track is also echo-suppressed
and single-channel, which is easier than the general case, so treat 0.6 as a starting
point rather than a tuned value.

**Not changed:** `notes.json`'s `participants[].track` is still the enum `ME|THEM`. The
system prompt (now **version 2**) tells the model that `THEM_2` is a speaker slot that
belongs in `name`, not in `track`.

## D30 — Four summarization providers; the subscription one never holds a credential
**Choice.** `llm.provider` accepts `anthropic` (default, unchanged), `openai`, `gemini`,
`claude-subscription`, `ollama`, `fake`.

**Why `claude-subscription` is not the default and never will be.** Anthropic's Agent SDK
documentation states: *"Unless previously approved, Anthropic does not allow third party
developers to offer claude.ai login or rate limits for their products, including agents
built on the Claude Agent SDK."* The Claude Code compliance page adds that developers may
not *"collect, store, or intermediate Claude.ai credentials or session tokens."*

The provider is therefore built so that **the application never authenticates**: it spawns
the `claude` CLI that the machine's owner signed into themselves, pipes the transcript to
stdin, and reads JSON from stdout. `test_claude_cli_never_sees_a_credential` asserts this
structurally — the module is scanned for `keyring`, `api_key`, `Authorization`,
`session_token` and `.credentials`, and must contain none of them. The child process also
has `ANTHROPIC_API_KEY` stripped from its environment, or Claude Code would offer to use
the key instead of the subscription session.

Tools are removed from that subprocess's context (`--disallowed-tools Bash,Read,Write,…`):
summarizing a transcript has no business reading the user's disk, and
`--dangerously-skip-permissions` is never passed.

**What the non-Anthropic providers give up.** Only the Anthropic Messages API enforces our
schema server-side, so the other three share one validate-and-repair loop
(`app/llm/repair.py`, extracted from the Ollama client). Consequences: no
`cache_read_input_tokens` accounting, and no `count_tokens` endpoint except Gemini's —
`claude-subscription` estimates at 2.0 chars/token, deliberately low so windows come out
smaller rather than over-full, because `DESIGN.md` §9.1 warns a character heuristic
silently blows the window on Hebrew.

**Subscriptions do not grant API access** at any of the three vendors — verified against
their own documentation. Gemini is the only one with a real free tier, which is why it is
the recommended no-payment-method option in the UI.

## D31 — Two bugs the provider work surfaced
1. **The e2e suite reached the public internet.** The Test button, with a key left behind
   by an earlier spec, made a real `POST` to `generativelanguage.googleapis.com`. Tests
   must never depend on or touch a third-party service, so `scripts/e2e_server.py` now
   points every provider base URL at `127.0.0.1:9`: an accidental call fails locally
   instead of leaving the machine.
2. **Provider selection looked broken.** The radio was bound to server state, so it snapped
   back until the round-trip finished — Playwright reported *"clicking the checkbox did not
   change its state"*, which is exactly what a user on a slow machine would see. Selection
   is now optimistic and settles on the response.

## D32 — `asr.cuda_dir` implemented; it was promised by the design and missing
`DESIGN.md` §2 says *"The app takes a `model_path` and a `cuda_dir` in config, so first run
can point at these and skip a 3 GB download"*, and `TECHNICAL-DESIGN.md` §17 step 2 repeats
it. `model_path` was built in Phase 5; **`cuda_dir` never existed** — `cuda_library_dirs()`
searched only the app home, three hard-coded toolkit paths, and `nvidia-*` wheels on
`sys.path`.

Found when the user pointed at a real machine: cuBLAS 12 and cuDNN 9 sitting in
`…\temp\Scripts`, which none of those three searches covers. The failure mode is the bad
kind — no error, just `CUDA_VISIBLE_DEVICES=""` and a 3.09 GB large-v3 model running on
CPU int8, several times slower, with nothing in the UI to say why.

`asr.cuda_dir` now takes a path or a list, is searched **first**, and warns rather than
failing when the folder holds no cuBLAS. Four tests cover it, including that a wrong path
degrades to CPU instead of crashing mid-meeting.

**Also recorded:** `supported_compute_type()` prefers `float16`, which is wrong for a
GTX 1080 — Pascal has no fast FP16, and `DESIGN.md` §20.5 specifies int8 for that card.
Rather than adding GPU-generation detection, the Windows launcher sets
`asr.compute_type = "int8"` explicitly with a comment. Automatic detection is the better
fix if a second GPU generation ever matters.
