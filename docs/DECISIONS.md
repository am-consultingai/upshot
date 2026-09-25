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
> **Closed by D38.** The sweep exists; this entry is kept for the reasoning that led to it.

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

## D33 — The microphone picker measures the same path the recorder uses

The Settings screen now selects the microphone (`audio.input_device`, null = the Windows
default) and shows a live meter beside it.

The meter is a `LevelMonitor` built through `make_capture`, **not** a direct WASAPI open.
That is the whole point: the level someone sees in Settings comes through the same
factory, device resolution, and format negotiation a meeting would use, so a device that
reads silent here reads silent in a meeting. A second, independent code path could
happily show a moving bar for a source the recorder cannot open.

Details worth keeping:

- **The meter reports the recorder's own level while a meeting is running.** Two WASAPI
  streams on one endpoint is a contention bug waiting to happen, and the honest reading
  during a meeting is the one being recorded.
- **A dB scale, not linear.** Ordinary speech peaks near 0.05 in linear amplitude, which
  is one segment out of 24 — a working microphone would look broken. −60 dBFS…0 spreads
  the usable range across the bar.
- **Peak-hold with decay.** An instantaneous bar reads as dead between syllables.
- **Silence is stated, not implied.** No signal for 2.5 s says so in words; that was the
  failure the feature exists to catch, after an afternoon lost to a synthetic source that
  was silent by configuration.
- **`GET /api/audio/devices` never raises.** Settings must open on a machine with no
  audio stack at all — CI, WSL, headless — so enumeration failure returns an empty list
  and an `error` string.
- **A configured device that has vanished falls back to the system default** with a
  warning. Recording something beats refusing to start.
- The e2e server now runs the synthetic source as `tone` rather than `silence`, so a
  meter that stays dead is a test failure rather than the expected result.

## D34 — One file per track, not a folder of chunks

Audio is now `audio/me.wav` and `audio/them.wav`, appended to as the meeting runs. The
per-minute chunk files are gone.

This was a design error, and the user found it by asking the obvious question: a
30-minute meeting was 30 files per track, so listening to it or sending it to someone
meant reassembling it first. Chunking was chosen for durability, and durability is real —
but it never required the *listener* to see chunks, and I compounded the mistake by first
building a stitching endpoint and a merge step to hide the chunks rather than not
creating them.

What replaces it:

- Each track file gets a 44-byte header at creation, and the RIFF/data sizes are
  rewritten after every committed segment. **The file is a valid, playable WAV at every
  moment**, verified mid-recording in the demo: 50.1 s readable while recording
  continued. A crash therefore leaves a playable file rather than one needing repair.
- The durability order is unchanged in spirit and still asserted:
  `append → flush → fsync → rewrite header → fsync → manifest line → fsync`.
- The manifest became an **index rather than a directory listing**: one line per
  committed segment carrying its sample `offset` into the track file. Recovery compares
  the file's own frame count against the last described offset, so audio that outlived
  its manifest line is still found.
- **Gaps are written as real silence.** Previously a lost 500 ms only advanced `t0_ms`,
  so the file's timeline and the meeting's timeline diverged. Now the file *is* the
  timeline, which is what makes the next point safe.
- Transcription is **one pass per track** instead of one per chunk. Timestamps need no
  shifting, and the model sees the whole track as context rather than one-minute windows.

A bug this surfaced: the WAV header helper wrote `bits_per_sample = 2` instead of `16`,
so decoders computed twice the true frame count. Caught because `recover()` then invented
a phantom trailing segment.

`app/audio/stitch.py` and `app/audio/merge.py` were deleted — the writer makes both
unnecessary. Kept: `track_of()` still accepts the old `me/0001.wav` layout, because
fixtures and imported audio use it.

## D35 — Playback mixes on read; the disk keeps two tracks

Two files per track is right for the pipeline and wrong for a listener. Transcription
needs to know who spoke; someone pressing play wants the meeting, not a track.

`GET /api/meetings/{id}/audio` now defaults to `track=mix`: both tracks summed into one
mono stream, synthesised as the request is served. `me` and `them` remain addressable for
checking what each side captured.

Why on read rather than a third file: both tracks are mono 16-bit PCM on the same
timeline (gaps became real silence in D34), so **output byte N maps to input byte N in
each track**. Range requests stay exact and stateless — seeking to minute 22 reads only
that window from each file, never the whole thing. A stored mix would cost 50% more disk
and reintroduce a merge step; browser-side mixing would need the entire file decoded in
memory, 460 MB for four hours, and two `<audio>` elements drift and break on seek.

Details:

- **Summed in int32 and clipped**, not averaged. Averaging makes every meeting 6 dB
  quieter to guard against a case that only arises when two people are loud at the same
  instant. A test covers saturation, because summing int16 directly wraps to a crackle.
- **Mono, not stereo.** Hard-panned voices are fatiguing over headphones and lose a side
  entirely on one earbud or a laptop speaker. The user chose this.
- The shorter track is **padded**, not truncated: the meeting is as long as its longest
  track.
- Ranges are sample-aligned internally and trimmed, since a request may legitimately
  begin or end mid-sample.

The `?track=mix` URL is itself a complete WAV, so "save as" yields one shareable file of
the whole meeting — sharing solved without storing anything extra.

**Found later:** the `click_transcript_seeks` browser test had gone stale against this
decision — it seeded a meeting with no audio on disk and asserted the player pointed at
`?track=them`, so it was failing on both counts. The seed now writes two real tracks and
the test asserts `?track=mix` and that the browser actually fetches it. A test that
asserts the behaviour a decision removed is worse than no test.

Verified against a real recording from the user's machine: the mix is sample-identical to
`me + them`, and 300 random ranges including odd offsets match the whole file exactly.

**Follow-up:** the per-track picker was removed from the meeting page at the user's
request. Nobody wants to choose a track before listening to their own meeting; the page
plays the mix and nothing else. `?track=me` / `?track=them` remain addressable for
diagnosis, and `audio_tracks` is still reported so the page can say when a meeting has
no audio at all.

## D36 — Crosstalk between the tracks is detected, not left to the ear

A microphone endpoint that also carries system audio records the far side a second time.
Nothing downstream can distinguish that from two people saying the same thing, and the
listener hears every remote voice twice. On a real machine this happened repeatedly with
a Voicemeeter bus selected as the microphone: measured correlation between the tracks was
**0.974**, i.e. the same signal.

The transcribe stage now measures it — the first minute of both tracks, one FFT
cross-correlation, reusing `analysis.cross_correlation` — logs the score, records it in
`ctx.metrics`, and above 0.85 adds a review reason so it surfaces in the UI instead of
being discovered by ear three sessions later.

Threshold reasoning: a digital copy lands above 0.95; two people genuinely talking land
near zero; speaker-into-microphone bleed across a room stays well below 0.85 because the
acoustic path colours the signal heavily. It deliberately does **not** fire on partial
leaks — a measured case at 0.651, where the user's own voice diluted the copy, goes
unflagged. Catching that would mean flagging ordinary meetings held without headphones,
and a warning on every meeting is a warning nobody reads.

**Found by this work:** `tests/fixtures/meetings.py` seeded both tracks identically, so
every fixture meeting was a perfect-crosstalk recording. The fixture now seeds per track.
A fixture should not look like a broken machine.

**Also fixed:** `WindowsToaster` does not support actions — it warns and drops them. Since
the buttons *are* the learning mechanism (DETECTION.md §6), every detector notification
had been shipping without them. Toasts with buttons now use
`InteractableWindowsToaster`, falling back to a plain toast if that is unavailable, since
a notification without its buttons still beats none.

## D37 — The echo is subtracted on the way out, never out of the recording

D36 measures the leak; this removes it. On a Voicemeeter bus routed to both the speakers
and the recording device the far side arrives twice — once properly on `them`, and once
copied into `me` — and the listener hears every remote voice as a slap echo while the ASR
transcribes it twice.

**The leak is not acoustic.** It never leaves the machine, so what lands on `me` is a
digital copy of `them` scaled by one gain and delayed by one constant. Measured on the
author's own recordings:

| recording | correlation | delay | gain | energy removed |
|---|---|---|---|---|
| `2026-09-03_1833_597be5` | 0.974 | 1683 samples (+105.2 ms) | 1.715 | **94.8%** |
| `2026-09-03_1916_ded850` | 0.980 | 1104 samples (+69.0 ms) | 1.490 | **96.0%** |
| `2026-09-02_1842_a7763a` | 0.651 | 1800 samples (+112.5 ms) | 1.565 | 43.9% |

Windowed at five seconds, the delay does not move at all (1683 in every window) and the
gain moves by 0.02; inside the windows where the far side is actually talking the single
tap removes **99%**. Fitting an 8-, 32- or 128-tap FIR instead was measured and buys at
most two further points on one file and nothing on another. So: one gain, one delay,
fitted by least squares — the scalar that actually minimises the residual, not a peak
ratio.

**Where it is applied.** Two places, from one model:

- the ASR input — `audio/clean/me.wav`, written streaming, transcribed, then deleted.
  `track_files` does not glob that subdirectory, and `track_of` still reads the track
  from the stem, so nothing else in the pipeline notices.
- the playback mixer — subtracted on read at a shifted byte offset, so range requests
  stay exactly as cheap as they were in D35.

**Where it is never applied:** the recording. `audio/me.wav` stays byte-for-byte what the
device produced, and `?track=me` serves it unchanged for diagnosis. A cancellation is a
judgment about a signal; a recording is evidence.

**Which window it is fitted on.** Not "the first minute": `597be5` opens with ten seconds
in which the far side has not said anything, and a real meeting can open with two minutes
of it. Not the whole span either — the *correlation gate* would be diluted by every quiet
minute and the leak would go unnoticed on any meeting where the far side is a minority of
the audio. So the estimator reads up to `audio.echo_scan_s` (600 s) of each track and fits
`audio.echo_window_s` (60 s) where `them` is loudest. That also fixed an existing
full-file read: the D36 check called `read_wav` and sliced, i.e. pulled 460 MB into memory
on a four-hour meeting to look at its first minute.

**When it fires.** `audio.echo_cancel = auto` (the default) subtracts when correlation
reaches `audio.echo_min_correlation` (0.85, D36's threshold). `on` subtracts whenever a
model fits at all, which is the escape hatch for the 0.651 partial leak D36 deliberately
declines to flag — subtracting there is measured to remove 43.9%, and the near voice is
what survives. `off` measures and warns without touching anything.

The review reason stays, reworded to say the echo was removed. The user's routing is still
wrong and they should still know; what changed is that the meeting is no longer ruined
while they fix it.

**Guard rails.** A fit whose gain exceeds ±8 or whose reduction is under 5% is discarded
rather than applied — that is noise being fitted to noise. Every failure path returns "no
model" and transcribes the raw track; a diagnostic must never cost a meeting.

**Found by this work:** `tests/fixtures/meetings.py` had no way to produce a leaking
meeting, so nothing downstream of D36 could be tested end to end. `write_chunks` now takes
`leak=`, and models the near voice at a quarter of the copy's amplitude, which is the
ratio measured on the real recording.

**Run on Windows, on the real recording, with the real model.** `2026-09-03_1833_597be5`
transcribed twice on the author's GPU (ivrit-ai large-v3, CUDA, int8), once with
`echo_cancel = off` and once with `auto`:

```
off   [me  ]  5.78- 9.22  ישנן צרות גדולות יותר
      [me  ]  9.22-21.01  ישנן צרות גדולות יותר      ← the far side, leaked onto ME
      [them] 11.02-21.36  יש לך טעות גדולות, יש לך טעות גדולות
      [me  ] 21.01-27.69  כן, איזה כיף, בטח, נשמע מעולה

auto  [me  ]  5.78- 9.22  ישנן צרות גדולות יותר.
      [them] 11.02-21.36  יש לך טעות גדולות, יש לך טעות גדולות
      [me  ] 14.85-27.69  כן, איזה כיף, בטח, נשמע מעולה.
```

Four segments become three. The duplicate — a ME segment spanning exactly the window in
which only the far side was talking — is gone, and both of the near speaker's own
utterances survive. Reproduced on two consecutive runs.

**The cost, stated:** the closing ME segment now starts at 14.85 s rather than 21.01 s.
Whisper extends a segment's start backwards across silence, and with the leak removed
there is now six seconds of silence in front of that turn. So click-to-seek on it lands
early. That is the trade: a wrong turn in the transcript, against a turn with a loose
start timestamp. The second is plainly the better one, but it is a real cost and not a
rounding error.

**Removed:** `analysis.crosstalk()` lost its last caller — `estimate()` computes the
correlation on its way to the gain and the delay, and returning only the first two of
three numbers is not worth a second code path. `CROSSTALK_THRESHOLD` stays where it is,
with its reasoning. The three unit tests that covered it (a duplicated track, two people
talking, a silent track) are covered case for case in `tests/unit/test_audio_echo.py`.

## D38 — Retention deletes the audio, keeps the meeting, and shows its work first

`retention.audio_days` has shipped in the default config since the first release and
nothing ever read it (**D27**). A key that promises deletion and does not delete is a trust
problem — and the mirror of it, a sweep that quietly deletes a user's recordings, is a
worse one. So the module splits in two: `plan()` decides and explains, `sweep()` acts, and
`GET /api/retention` serves the plan without touching anything. The policy can be checked
on a real machine before it is believed. `POST /api/retention/sweep` runs it on demand
rather than waiting for the worker's next pass.

**Two clocks, deliberately separate.** `audio_days` (30) removes the raw WAVs and keeps
everything derived from them; a 45-minute meeting is ~85 MB of audio against a few
kilobytes of transcript, so this is the one that earns its keep. `transcript_days` (`null`)
removes the meeting outright and is off by default, per DESIGN.md §17: transcripts and
summaries are kept indefinitely. `null` and `0` both mean *never*, and so does anything
that is not a positive count.

**What is never swept**, each reported by name rather than skipped in silence:

| refused | why |
|---|---|
| still recording | a meeting stuck in RECORDING for six weeks is a crash, not an expired one |
| a job is still queued | a retry enqueued last week must still find its audio this week |
| not transcribed yet | until the transcript exists, the audio **is** the meeting |
| outside the data folder | the folder path comes from the database and must never aim the delete elsewhere |

That third row is the one that matters. Invariant 1 says recording is sacred; a retention
policy is the one place the application deletes a recording on purpose, so it does it only
once something has been derived from it. A meeting that failed to transcribe keeps its
audio for ever, and the plan says so, which is a bug report rather than a silent leak.

**Driven from the worker loop, not APScheduler.** The dependency is present and STACK.md
names it for this job, but the sweep deletes the same folders and rows the pipeline reads,
and running it on the thread that already serialises that work removes a whole class of
race for the cost of one timestamp. It runs only when the queue has nothing runnable, so
housekeeping never competes with a job, and at most once every `retention.sweep_hours`
(6). The first call always runs, so a sweep happens shortly after every startup.

**Deletion is stated, not inferred.** `meta.json` gets `audio_deleted_at` and
`audio_bytes_freed`, and the meeting page reads it: *"The recording was deleted by the
retention policy. The transcript and summary are kept."* Without that, a swept meeting and
a recording that failed look identical — an empty player and the words "this meeting has
no audio".

**Reuse:** `MeetingService` gained `purge()` and `drop_audio()`, and `DELETE
/api/meetings/{id}` now goes through the same `purge()` the sweep does, with the same
containment check in one place instead of two.

**Found by running it on Windows: a delete that lied.** The first version called
`shutil.rmtree(..., ignore_errors=True)` and then wrote `audio_deleted_at`. Windows will
not unlink a file another handle has open, so with a reader holding `them.wav` the sweep
removed `me.wav`, swallowed the error, reported success, and marked the meeting swept —
which means it would never have been looked at again. Half the audio gone, the record
saying all of it was, and no retry. Linux would not have shown this: it unlinks open
files happily.

`remove_tree()` now raises with the names of the survivors, the mark is written only
after the folder is actually gone, `bytes_freed` counts what went rather than what was
there, and `purge()` removes the folder **before** the row, because a row deleted against
a surviving folder orphans it with nothing left pointing at it. `DELETE
/api/meetings/{id}` answers **409** with the reason instead of claiming success. The
regression test reproduces it on Linux without mocking anything, by making the audio
directory unwritable — `unlink` then fails the same way.

Verified on Windows after the fix: the held-handle sweep reports
`could not remove ...: 1 file(s) still held (them.wav)`, leaves `audio_deleted_at` unset,
and the next sweep — once the handle is closed — removes the remainder, writes the mark,
empties `audio_tracks` and turns the audio endpoint into a 404.

---

## D39 — A notification runs in its own process, or it can kill the recording

WinRT objects belong to the COM apartment of the thread that created them, and FastAPI
serves synchronous endpoints on short-lived, interchangeable threadpool threads. Every
notification failure this project has had comes from that mismatch, escalating each time:
`RPC_E_WRONG_THREAD` on 2026-09-01, "object is not connected to server" on 2026-09-02,
and on **2026-09-18** an access violation inside `_winrt_windows_data_xml_dom` that killed
the application the moment a recording was stopped.

The last one is the reason for this entry. Stop had already done its work — the audio was
flushed, the meeting was `RECORDED`, transcription was queued — and then the process
vanished showing "Meeting ended". A native access violation cannot be caught, so no
`except` ran, nothing reached `app.log`, and the only record was in the Windows Event Log.
The browser, meanwhile, said "Something went wrong", which reads as a bug in the page.

Toasts now run as a separate short-lived process (`app/notify_toast.py`), launched and
never waited on. Nothing in the server process imports WinRT any more. The toast gets a
clean main thread that nothing else shares, and if the notification stack faults it takes
only that helper with it. Stop no longer waits on a notification either.

Two smaller consequences, both about being told: the Windows launcher now watches the
process instead of tailing a log file forever, and says so — with the exit code and where
Windows recorded the fault — when it dies; and the page shows "the app stopped answering"
rather than a generic error when `/api/status` stops being answered.

---

## D40 — The detector reasons about the best holder of the microphone, not the first

The detector took `holders[0]` — whichever process the ConsentStore listed first — and
that is only ever correct on a machine where one app at a time uses the microphone.

On the author's machine, Voicemeeter routes all audio and **holds the microphone from boot
to shutdown**. It was listed first, every time. So on 2026-09-18 a real Google Meet call
produced exactly one detector event: `voicemeeter.exe`, peaked at 3, "90 s with no
verdict" — while Windows was reporting `chrome.exe` as a holder too, and
`Meet – rbn-nmqs-jbm - Google Chrome` among the window titles. The browser that joined the
call was never looked at. Worse, the verdict on Voicemeeter set `suppressed_process`,
which is only cleared when the microphone is released — and it never is — so the detector
was silent for the rest of the session.

The first fix was a list: ignore `voicemeeter*.exe` by default. That is the wrong shape
for something meant to ship. Every machine has its own furniture — Krisp, NVIDIA
Broadcast, SteelSeries Sonar, Elgato Wave Link, OBS, a voice assistant — and a list only
ever covers the machines we happened to see. It was also unnecessary, because Windows
already reports what tells them apart.

**A meeting starts. Furniture is already there.** `LastUsedTimeStart` gives, per process,
when it took the microphone; the reader had been parsing it into `MicHolder.since_ms` all
along and nothing read it. Voicemeeter measured 234 minutes on the machine in question.
Chrome joining the call measured one second.

So the detector now wakes on the *taking* of the microphone, never on the holding:

- **`_note_acquisitions` tracks who began holding since the previous look**, and only
  those are candidates. Scenery never generates an acquisition, whatever it is called.
- **The first look has no previous one**, so it falls back to the timestamp:
  `detection.fresh_hold_s` (120 s). Older than that was already there when the app
  started. An unreadable timestamp counts as recent — this is an undocumented registry
  artifact, and failing closed would mean a machine that does not report it detects
  nothing at all and says nothing about it.
- **A verdict is about one acquisition.** Judged, the process leaves the fresh set;
  letting go and taking the microphone again is news, and is looked at afresh. This
  replaces the suppression flag, which one permanent holder used to occupy for ever.
- **`candidate()` still chooses among those**, preferring a known conferencing app, so
  the order Windows happens to enumerate in stops mattering.
- **A wake, and a recording, follow their own process.** `_tick_awake` and
  `_tick_recording` are given the process that woke the detector, not any holder. With a
  permanent holder, "the microphone was released" was otherwise never true, so a detected
  meeting would never have ended on the release path at all.
- **`end()` puts the process back**, because the duration cap ends a meeting while the app
  keeps the microphone, and §7.4 wants the next one to start.

What this also closes: speech is a property of the room, not of a process, so an unknown
holder used to be able to cross the threshold on speech alone — unknown app 1 + both
tracks 2 + 2 = 5, exactly the default threshold. Had anyone been speaking on both sides,
Voicemeeter itself would have been recorded as a meeting. It can no longer wake at all.

`detection.ignore` goes back to what it was, and stays short.

---

## D41 — The app configures the machine; the developer never does

Two failures wear the same clothes, and both were live in this repository on 2026-09-18.

**A value that is true here and nowhere else.** `detection.ignore` grew `voicemeeter.exe` and
four of its siblings because Voicemeeter holds the microphone from boot to shutdown on the
author's machine. It fixed the symptom and would have shipped a detector that is blind to
exactly one vendor, on exactly the machines we happened to have seen — Krisp, NVIDIA
Broadcast, SteelSeries Sonar, Elgato Wave Link and a voice assistant would each have needed
their own entry, discovered the same way: by someone's meeting not being recorded. The rule
replaced it with a question Windows can answer anywhere: **when did this process take the
microphone?** Furniture was already holding it; a meeting takes it. No names (D40).

**Machine state nothing in the code puts there.** Toasts with buttons work on this machine
because the library falls back to Command Prompt's registered identity; plain toasts are
silently swallowed, because `Upshot.App` is not registered as an AppUserModelID and
*nothing in the app registers it*. Had that been fixed by hand — one `reg add` on this
machine — every test would have passed here and every fresh install would have shipped a
notification that does nothing. The registration belongs in the app's first run.

So, as a standing rule:

- **Shipped code may not name a machine's furniture.** Vendor names in `app/` are allowed in
  comments explaining why a rule exists, never as the rule.
- **Anything the app needs from the machine, the app arranges** — at install or first run,
  reporting what it did. A working machine is not evidence unless the code put it that way.
- **Development tooling discovers, it does not assume.** `scripts/windows/e2e.py` asks
  Windows for `%LOCALAPPDATA%`, `%TEMP%` and the browser, and `wslpath` for its own source
  path, because the account name, the distribution name and the browser's drive differ on
  every machine. It fails with a sentence naming what it could not find rather than reaching
  for a path that exists only here.
- **A test may not quietly change the machine it runs on.** The injected-audio suite uses
  virtual endpoints and leaves the system defaults alone; with none available it says so and
  stops, rather than borrowing the speakers and playing a chirp at whoever is sitting there.
  `UP_TEST_ALLOW_AUDIBLE=1` is the way to say that is fine.

The tell for both failures is the same: a change that makes the tests pass without making
the product work anywhere else.

## D42 — Three colour inputs, and a typeface chosen from the Hebrew side

The interface was to be rebuilt, and the first question was what it would be rebuilt *on*.
`frontend/src/index.css` was nine lines: one system font, no tokens, and 170 palette colours
named directly in components. There was no design system to extend, so the choice was what
kind of one to write.

**Derive the palette; do not maintain one.** Linear's published account of their own
redesign is the model: 98 colour variables collapsed into three inputs — a base, an ink and
an accent — with everything else derived. That is now `tokens.css`. Surfaces, borders and
text are the ink composited onto the canvas in oklab, so there is no grey ramp to keep in
step, and one token is correct on any background.

The reason to care is not elegance, it is churn. A second theme maintained by hand drifts
from the first, and the drift is only visible to whoever is looking at that screen in that
mode. Jamie — a direct competitor — shipped dark mode, withdrew it, and reinstated it "by
popular demand" across three years of redesigns, with reviewers still complaining about its
contrast. Here dark mode redefines the three inputs and one mix direction; everything else
follows. It cannot drift, because there is nothing to keep in step.

**Elevation without shadows, in dark.** Shadows on a dark canvas read as smudges, so the
shadow tokens resolve to `none` under dark and depth is carried by the background ladder plus
a hairline `inset 0 0 0 1px` ring — which, being inset, never affects layout. Linear and
Raycast both do this; it was read out of their stylesheets rather than taken on trust.

**Type had to be solved from the Hebrew side, and that changed the answer.** The obvious move
— a slab serif for display over a neutral sans for UI, which is most of why Granola reads as
a considered product — is not available here. This application ships 147 Hebrew strings, and
Inter has no Hebrew: it covers Latin, Cyrillic and Greek. Nor does any display face used by
the products worth learning from: Granola's *quadrant*, Jamie's PP Neue Montreal, Craft's
Untitled, Superhuman's Super Sans VF, Inter Display, NotionInter, Berkeley Mono. A Latin-first
choice would have forced a second family for Hebrew, with mismatched vertical metrics and
`:lang()` switching to hide the seam.

So the faces were chosen for Hebrew and checked for Latin, not the reverse. Heebo for UI:
Roboto's Latin extended to Hebrew, with the Hebrew as the primary design. Frank Ruhl Libre
for display: the open cut of Frank Rühl, Rafael Frank 1908–1910, the most ubiquitous Hebrew
typeface in print. One family per tier, both scripts, no switching.

Both are **bundled, not linked**. A stylesheet request to `fonts.googleapis.com` on every
launch would break an application designed to work offline and leak a request from one that
keeps recordings on the machine on purpose. 138 KB for both families across both scripts.

**Typography is scoped to script.** The tracking scale is a Latin device, and applying it from
`body` — which is what the first version did — quietly degraded every Hebrew screen while
English looked exactly as intended. Negative tracking is now `:dir(ltr)` only; Hebrew gets
more leading and a little word-spacing instead. The same rule governs components: chevrons
mirror, instruments do not. The level meter colours its segments by index, so in Hebrew it
filled from the right with red on the left until it was pinned to LTR.

Two consequences are enforced by tests rather than by review, because both failures are
invisible in an English light-mode screenshot: no component may name a palette colour, and
no component may use a physical direction utility.

**A feature is not shipped because its stylesheet is written.** Dark mode was finished, and
correct, and deliberately withheld from `prefers-color-scheme` until no component hardcoded a
colour — because with the components unmigrated, flipping the tokens left a light background
under near-white text and made the summary invisible. That was found by looking at a
screenshot, which is also the general lesson: the CSS read correctly the whole time.

## D43 — Google Calendar connects over loopback OAuth, in plain HTTP, with the client baked in

Calendar 1 (ClickUp `z8tj1h8jrg`, epic `z8tj1h8jrf`) connects one Google account. How:

**Loopback with PKCE, on a listener of its own.** Connect opens a one-shot HTTP listener on
`127.0.0.1:<ephemeral>` and sends the user's own browser to Google's consent page. Google
redirects back to the listener, which takes the first request carrying a `state`, checks it
in constant time, exchanges the code with the PKCE verifier, answers "you can close this
tab" and stops. It is not the app's own server on port 8000: that server sits behind
cookie auth and CSRF, which a redirect from Google cannot satisfy, and punching a hole in
that stack for one route is worse than a second socket that lives for at most five minutes.
Out-of-band ("paste this code") is not an alternative: Google blocked it for every client
on 2023-01-31.

**Scope `calendar.events.readonly`, nothing else.** The account's address, which Settings
shows, is read from the primary calendar's `summary` rather than from an identity scope, so
the consent screen asks for exactly one thing. Google lets the user untick that one thing
and still press Continue; that is caught and the partial grant is revoked, instead of
storing a token that can read nothing.

**No Google client libraries.** Four endpoints (auth, token, revoke, events) are plain
`httpx`. `google-auth` and `googleapiclient` would add megabytes to the installer to wrap
calls that are a form post each, and would hide the error codes the design depends on.

**The client ships in the application and never in git.** It is a *Desktop app* client;
its secret is not confidential by Google's own definition, and PKCE plus the loopback
redirect are what protect the flow. But the repository is public, and a client posted there
is found by leak scanners and can be disabled, which breaks every installation at once. So
the downloaded JSON sits at `app/gcal/google_oauth_client.json`, ignored by git, and the
PyInstaller spec copies it into the bundle when it exists. `UP_GOOGLE_CLIENT` points
elsewhere for a run. What happens when the client is disabled anyway, and a fallback for it,
is ClickUp `z8tj1h900g`.

**Tokens.** The refresh token and the account address go to the OS credential store through
`SecretStore`, never to `app_config.json`. Access tokens live in memory, with their expiry
taken from Google's `expires_in` on the monotonic clock, so a wrong wall clock cannot make a
live token look expired or a dead one look live.

**Failures are states.** `invalid_grant` on refresh — revoked, expired, or a Testing-mode
token past its seven days — deletes the token and shows *Reconnect*; nothing retries it. A
rejected client (`invalid_client`) stops all token requests until the user connects again.
No network is the opposite case: the token is kept and the caller tries later. Disconnect
calls Google's revoke endpoint **and** deletes locally, whatever the revoke says; when the
revoke could not be sent, Settings says so and links to the account's connections page.

## D44 — The calendar is a cache, matching has three answers, and the description never leaves Google

Calendar 2 to 7 of the epic, built on D43's connection.

**A local cache, polled.** `calendar_events` is a SQLite table the app can delete at any
time: everything in it comes back on the next sync. Two rhythms — the next few hours
every minute, thirty days either side every ten — because `events.watch` needs a public
HTTPS endpoint that a local app will never have. That is about 1,600 requests a day
against a quota of a million, so the timer needs no cleverness. Sync tokens are
deliberately not used: they may not be combined with a time window, and a windowed
re-list is one idempotent request with no 410 recovery path. Every consumer reads the
cache, never Google: the detector's tick, matching at the start of a meeting, and the
calendar view all stay local, so they work offline and cannot be slowed by the network.

**Matching answers "I don't know".** Overlap, never containment, so a recording started
late and run over is still its meeting. The verdict is *matched*, *proposed* or *none*,
and only *matched* renames a recording. Ambiguity is shown rather than resolved by
guessing: two back-to-back meetings inside one recording is a proposal with both
candidates, because attaching the wrong attendees is worse than attaching none. What the
user picks is final — automatic matching never overrides a choice, and never touches a
title the user typed. Times are compared as UTC instants only, so DST cannot move a
match, and the expiry of an access token is measured on the monotonic clock.

**A snapshot, not a reference.** A match stores the event's title and attendee names as
they were. Renaming the event next week does not rename the recording, and deleting it
does not lose the name.

**Title precedence, written down**: the user, then the calendar, then the model, then the
window title, with `title_source` recording the winner. A title typed at creation counts
as the user's.

**Privacy is enforced at the boundary, not by convention.** Attendee email addresses and
event descriptions are dropped in `app/gcal/events.py` before anything is stored, so no
later mistake can leak what was never kept; a display name Google omits is made from the
address's local part inside that same function. Descriptions are never stored and never
sent to a model, which overrides the epic's earlier idea of passing the agenda to the
prompt: they routinely carry dial-in PINs and forwarded mail. The title goes to the
summary model by default and attendee names only after the user turns them on, because
that model may be a hosted one; a private or confidential event sends nothing at all.
Tests assert that no address appears in the database, a prompt or a log line.

**The detector gets a signal, not a trigger.** A calendar meeting on now is worth 3
against a threshold of 5 — enough to make a known conferencing app's call immediate,
never enough alone. Nothing but a microphone wakes the detector, so an event by itself
cannot start a recording however it scores. It may say so, once, and offer to record.

## D45 — The invitation is read live and sent whole; it is still never stored

Amends D44, at the product owner's decision, after the first real meeting produced a
summary that knew the title and nothing else.

**What changed.** The whole invitation now goes to the summary model with the transcript:
title, times, organizer, who was invited (and who declined), the agenda the organizer
wrote, every link inside it, and the names and Drive URLs of the files attached to it. One
switch in Settings governs it, on by default, replacing the two narrower switches for the
title and attendee names. D44 said descriptions would never reach a model; this reverses
that, because an agenda is what tells a summary what the meeting was *for*, and a summary
written from the transcript alone reads like a stranger's notes.

**What did not change.** Email addresses still stop at the boundary in
`app/gcal/events.py` and `app/gcal/invite.py`: an attendee is a display name, or a name
made from the local part of their address, before any of this is assembled. Nothing in the
invitation is stored — not the description, not the attachments, not the links.

**Live, not cached, and that is the point.** The invitation is fetched from Google when it
is wanted: by the meeting page while it is open, and by the summarize stage while it builds
its prompt, with a 60-second reuse window so the two do not ask twice. The agenda therefore
lives where the user maintains it. Editing the event after the meeting corrects what the
page shows and what a re-summary reads; deleting the event takes it away. The cost is that
both paths need the network, so both degrade: the page says it could not read the
invitation, and the summary falls back to the snapshot taken when the recording was
matched. A meeting is summarized whatever the calendar is doing.

**Not done: reading the attached files.** The app can see that a Drive file is attached and
can name and link it, which needs no permission beyond the calendar scope. Reading what is
inside one needs a Drive scope, a second sensitive permission on the consent screen, and
more of Google's verification. Left until it is asked for.

## D46 — The subscription provider, re-checked against a policy that moved three times

D30 was written when Anthropic's position had one part. It now has several, and two of
them changed after that entry was filed. Checked **2026-09-20**; every date below is when
the thing happened, so the next reader can tell how stale this is.

**The mechanism is still the documented one.** The Agent SDK overview says, of driving the
agent loop from another language: *"run the CLI as a subprocess with the `-p` flag and
`--output-format json`."* That is what `app/llm/claude_cli.py` does, and it is why this
provider is not a workaround. Anthropic's help centre goes further and names the category
directly — *"third-party apps that authenticate with your Claude subscription through the
Agent SDK"* — so an application running on a user's own plan is something they bill for and
reason about, not a gap in their fence.

**The prohibition, and why we are on the right side of it.** *"Unless previously approved,
Anthropic does not allow third party developers to offer claude.ai login or rate limits for
their products."* We offer no login. The machine's owner installs Anthropic's tool and signs
in through Anthropic's own browser flow; this application never sees, stores or forwards
that credential, which `test_claude_cli_never_sees_a_credential` asserts structurally.

**What moved.**

* **4 April 2026** — Claude subscriptions stopped covering usage through third-party
  *harnesses* (OpenClaw and similar): agents that route their own OAuth and run autonomous
  workloads. The remedies offered were usage bundles or an API key. Spawning Anthropic's own
  CLI was not what that action was aimed at, but it is the clearest signal available of how
  this door closes if it closes.
* **13 May 2026** — a split was announced: Agent SDK, `claude -p`, GitHub Actions and
  third-party apps on a subscription would move onto a separate monthly credit metered at
  API list prices.
* **15 June 2026** — that split was **paused**. Such usage continues to draw on Pro, Max,
  Team and Enterprise limits *"exactly as before"*, with advance notice promised before any
  future change.

**A branding rule we were breaking.** The same page permits "Claude Agent" and "Claude" but
lists "Claude Code" and "Claude Code Agent" as not permitted for a third-party product, and
asks that the product not appear to be Claude Code. The provider was labelled *"Claude Code
(your own subscription)"* in Settings; it is now *"Claude Agent (your own subscription)"*.
Install and sign-in copy still names Claude Code, because that is the software the user has
to install — naming a prerequisite is not branding a product as it.

**Consequence for the OpenAI equivalent** (epic `z8tj1h9bnj`). Anthropic published both
halves: how to drive it from another program, and how third-party apps on a subscription are
treated. OpenAI has published only the first — `codex exec` is documented and supported,
but nothing states whether a third-party application may drive it on someone's ChatGPT plan,
and an OpenAI engineer asked directly declined to answer and pointed at the Terms of Use.
That is an unanswered question, not a refusal. Build it the same way, ship it the same way —
never the default, the user's own CLI and sign-in, no credential intermediation — and
re-check before release.

---

## D47 — Action items come back as data, and the document stays the model's

**2026-09-20.** D46's predecessor removed `NOTES_SCHEMA` because nine fixed fields plus a
Jinja template meant an edited prompt could change wording but never structure — which is
not what a prompt editor implies. That was right, and it stays. What it also did, recorded
honestly in `known-issues.md` #10, was destroy the basis for anything that reads *across*
meetings: an action-item inbox, per-person views, "what did I promise this week". None of
that is possible against opaque HTML.

A review on 2026-09-20, done in the character of a product manager in six or seven meetings
a day, put a price on that. The verdict was that the app is a filing cabinet, and that she
does not have a filing problem — she has a follow-through problem. Seven well-written
documents and a notebook that still wins.

**What changed.** The envelope grew one optional key:

```
{summary_html, title?, action_items?: [{who, what, due?, at_ms?}]}
```

**Why this is not the schema coming back.** The old schema *was* the document: nine fields
in, a template laid them out, and the prompt could not reach past it. This asks the model to
write whatever document it likes and then **repeat, as data, the commitments already in it**.
Nothing downstream rearranges `summary_html`; the list adds no section, changes no heading,
and omitting it costs only the cross-meeting view. The distinction that matters is that the
structure is now *derived from* the document rather than *imposed on* it.

**Optional, and forgiving, on purpose.** A summary written before this existed must keep
opening, so the key is not required. A provider that half-honours the request — a string
where an object belongs, a missing `what` — costs its own item and nothing else
(`schema.action_items`). The summary is the product; this list is a bonus on top of it, and
it must never be the reason a meeting has no notes.

**The tick is the user's, and it outlives the model's.** `done_at` lives in the database, not
in `notes.json`, and re-summarizing carries it forward by matching on the casefolded,
whitespace-squeezed text. Editing the prompt and pressing Summarize is routine — there is
deliberately no staleness check on it (D46-era reasoning) — so a checkbox that silently
reopened itself because the prompt moved would be untrustworthy, and an untrustworthy
checkbox is worse than no checkbox.

**What it does not fix.** #7 stands: nothing validates a summary. A model that invents an
owner still produces a valid envelope, and now the invention is a row in a table rather than
a sentence in a paragraph — more visible, not more true. Attribution on imported transcripts
remains unreliable by construction, because every imported turn is labelled `THEM`.

## D48 — One meeting is one row: the calendar merges, the page collects, and the list counts

Four changes that are one change. A meeting existed in this application as three separate
objects that happened to be about the same hour — a calendar event, a recording, and a set
of commitments — and each screen showed whichever of them it happened to own.

**The calendar view is the only view, and the rail says so.** There was a List/Calendar
toggle above the meeting list, choosing between the column and the pane beside it: two
views of one collection, side by side, with a switch insisting that only one of them could
be true. The column *is* the list. The calendar now holds the detail side from the moment
the screen opens, the toggle is gone, and the rail icon is a calendar rather than three
stacked lines that promised a list.

**A recording with no stored match is matched again when the page is read.** A recording
made before the calendar was connected carries no snapshot, and nothing ever went back to
give it one — so it drew a second block of its own beside its own event: the same meeting
twice, at two times, under two names, one block holding the joining link and the other the
transcript. `_inferred_calendar` asks the matcher that runs at record time (`app/gcal/match.py`)
against the cache, and uses **only** a `matched` verdict: a proposal is a guess, and a guess
drawn as a fact is worse than a duplicate. Nothing is written — the snapshot still belongs
to the moment of recording — and a meeting the user has already ruled on is never
second-guessed, because that meeting's `calendar_json` is not empty.

**The meeting page is where everything about the meeting lives.** The match card and the
invitation card were two cards stacked on each other, and the joining link was on neither:
it was reachable only from the calendar block the merge above has just removed. They are
one card now — event, times, people, link, agenda, attachments — because the alternative to
collecting it is losing it.

**Addresses reach that card, and nothing else.** This narrows D44 and D45 at the product
owner's request. An attendee is still a display name in the cache, in the snapshot on a
recording, in every prompt and in every log line; `Invite.people` carries the address as
well, is assembled from the live Google response, and dies with it. "Which Dana" is a
question a display name cannot answer, and replying to one of them is the next thing anyone
does. The tests that matter are unchanged and still pass: no address in the database, none
in a prompt, none in a log.

**The list says what each meeting still owes.** A library of well-written summaries answers
"what was said" and says nothing about what is outstanding, which is the only question
anyone scans a list of past meetings to answer. `/api/meetings` carries `actions_open` and
`actions_total` from one grouped query, and the card shows the open count — green, with the
total, once they are all discharged. Zero open is deliberately not the same news as none
recorded: a meeting that never made a commitment says nothing at all.

## D49 — A launcher may fix what the machine is, never what the user decided

"Detecting meetings" would not stay where it was put. It was set to automatic, it saved,
`app_config.json` recorded it, the screen read it back — and the next launch had it off
again. Repeatedly, over several sittings.

**The cause.** `scripts/demo.sh` exported `UP_DETECTION__MODE='"off"'`. The environment is
the top configuration layer (D-era layering: defaults, then the file, then the
environment), so it beat the saved choice on every start, for ever, and in silence. The
Settings screen reads the *resolved* value, so it showed "Off" and looked like a screen
that forgets what it is told.

This is the second time this shape of bug has been found. The first was
`UP_LLM__PROVIDER` on the Windows launcher, and the fix then was at **save** time: an
environment value is no longer written back to disk by an unrelated save
(`Config.save`, `_from_env` / `_explicit`). That fixed the file. It could not fix the
**load**, where the environment legitimately wins again the next morning.

**The rule.** A launcher may pin what the *machine* is — which port, no microphone, no
3 GB model, where the app home is. It may not pin what the *user* decided. A test now
reads both launchers and fails if either exports one of the keys that belong to the user
(`tests/unit/test_phase1_config.py::test_no_launcher_pins_a_setting_the_user_owns`).

**And the silence is gone.** Precedence is unchanged, because an operator override is
supposed to win. What changed is that the application can now name what is holding a key:
`Config.env_pinned()` maps each pinned dotted key to the variable holding it,
`/api/settings` returns it as `pinned`, the affected control on the Settings screen says
so in its own row rather than quietly reverting, and `start_background` writes one line
naming every pinned key. Any future launcher that pins something has to admit it on
screen; none of this can be silent again.

**What it is not.** Not a licence to reach for the environment. `detection.mode` is not
pinned anywhere now: the shipped default is `shadow`, which watches and logs and never
records on its own, and off Windows there is nothing for it to watch — so there was never
anything to pin it for.

## D50 — A deadline is a date, resolved by us when the model does not

**2026-09-23.** The inbox could say *who* owed *what* but not *when*: `due` was the words
someone said ("by Thursday", "עד סוף השבוע"), and words cannot be sorted, flagged overdue or
put on a calendar. The redesign needs all three.

**The model is asked first.** The envelope's action item grows `due_at` (YYYY-MM-DD) and
`detail` (one short line: why it matters, what it unblocks, who is waiting). A capable model
resolves "Thursday" better than a table can, because it heard the sentence around it. For
that it needs to know which week it was said in, so **the meeting's date and weekday now
lead every summarize request**, invitation or not (`summarize.date_context`). Before this,
a manually started recording carried no date at all into the prompt.

**`app/due.py` is the fallback, and it is ours.** Used when the model gave the words and no
date, and — lazily, at read time — for every row stored before `due_at` existed, against the
local day of the meeting it came from. No backfill: a better resolver improves old rows for
free, and the migration stays SQL. A malformed `due_at` from the model costs that date, not
the summary; the fallback then gets its turn.

**Why not a library.** chrono-node is JavaScript and reads no Hebrew. `dateparser` is large,
resolves a bare weekday to the *previous* one by default, and does not know "ביום חמישי",
"עד חמישי" or "מחרתיים". The phrases a deadline comes in are few; a table of them is easier
to test (a hundred cases in `tests/unit/test_due.py`) than a general parser is to configure.

**Conventions are Israeli, because the user is.** Weeks start on Sunday and "end of week"
is Friday. Numeric dates are day-first. A weekday is its next occurrence strictly after the
meeting day — "Thursday" said on a Thursday is a week later. "Next Thursday" is read the same
way as "Thursday"; "Thursday next week" is the Thursday of the following week.

**Never guess.** Anything unrecognised is None, and so is anything naming two different days
("Tuesday or Wednesday"). "Next month" is None too: its last day would be a guess presented
as a deadline. A wrong date is worse than none, because the inbox flags by it and the reader
believes the flag.

**User-added items.** `action_items.source` is `model` or `user`. Re-summarizing replaces
only the model's rows and renumbers the user's after them; a model item that repeats a
user's word for word is dropped rather than shown twice. `snoozed_until` is carried forward
by the normalised text exactly like `done_at` (D47). What is *not* carried: an edit to a model
row's wording, owner or date — the next re-summarize rewrites it, as it rewrites everything
else the model said. Deleting a model row is "not this", not "never": it returns if the
model finds it again.

## D51 — Tags are the user's words, one spelling per library

Meetings can be tagged (`meeting_tags`, `GET /api/tags`, `PUT /api/meetings/{id}/tags`). A
tag is stripped and whitespace-squeezed, deduplicated case-insensitively, and **takes the
spelling already used elsewhere in the library**: "roadmap" typed on Tuesday's meeting
becomes the "Roadmap" typed on Monday's, so the filter does not fill with case variants. On
its own meeting a tag can be re-cased, which is how the first spelling gets fixed. Twelve
tags of forty characters at most — a label, not a note. The list endpoint carries each
meeting's tags from one grouped query.

## D52 — Speaker names are a mapping laid over the transcript, not a rewrite of it

The transcript says `ME`, `THEM`, `THEM_1` — the recorder's tracks and the diariser's slots.
`meetings.speaker_names` is a JSON object from slot to the name the user gave it, merged by
`PATCH /api/meetings/{id}` (`null` or `""` removes a slot) and applied wherever the
transcript is shown or sent (the page; `ask`). Nothing in `transcript.json` is rewritten,
because a name typed against the wrong slot must be correctable without re-transcribing, and
because diarisation that reassigns slots on a re-run should not silently move names onto
the wrong voices. The summarizer does not use it yet: it already turns slots into names from
the conversation and the invitation, and the summary is not re-run when a name changes.

## D53 — Related meetings, from four signals that can each be said in words

`GET /api/meetings/{id}/related` returns up to five meetings, each with the reasons it was
chosen: a **shared action item** (identical once normalised, or word-set Jaccard ≥ 0.5 — a
commitment carried from one meeting to the next is the strongest thread there is), the
**same series** (same calendar title), the **same people** (two or more shared invitees, or
the one person in a one-on-one), and a **mention** — the single rarest shared word of five
or more letters that is not an English or Hebrew stopword and appears in at most a quarter
of the library. Scored in that order of strength, ties broken by nearness in time.

Computed on request from what is stored; there is no index to fall out of step. A reason
is an object with a `code`, so the page words it in either language without matching prose.
Meetings still recording or discarded are never offered. People come only from the calendar
snapshot: nothing is inferred from voices.

## D54 — Ask this meeting: the transcript and the question, and the moments it rests on

`POST /api/meetings/{id}/ask` sends the transcript (as `[mm:ss] SPEAKER: text`, with the
user's speaker names) and the question to the provider the summary came from, and asks for
an answer plus citations. `scope: "related"` adds up to three related meetings (D53), each
under its own labelled header. No embeddings and no retrieval: one meeting fits a hosted
model's request, and where the context does not fit it is cut to a character budget — the
meeting asked about gets half — because an answer from a visibly truncated transcript is
better than a confident one from a badly retrieved chunk.

Citations are checked against what the model was shown: one naming a meeting outside the
context is dropped, one between turns snaps to the turn it falls in and carries that turn's
text. **Sensitivity is contagious:** if any meeting in the context is sensitive, the request
goes to the local model, even when it is only related to the one being asked about. A
provider error is a 502 with the provider's reason, not a 500.

## D55 — The summarizer also returns chapters, and the document opens with its outcome

The envelope grows optional `chapters: [{title, start_ms, end_ms?}]` — three to seven topic
sections covering the conversation in order — kept in `notes.json` and served on the meeting
payload, so the page can show where the talk went and seek to it. Parsed defensively and
sorted, with an open end filled from the next start. Windowed runs take the merge's chapters
if it produced any, else every window's in time order.

The shipped prompt (version 6) adds two rules to the document that stays otherwise the
model's (D47): it **opens with a single lead sentence** in a plain `<p>` stating the most
important outcome — no "Overview" or "Summary" heading above it — and a decision with a
concrete next step gets it **directly beneath**, as `<p class="next">`. Both are things the
page's design depends on and a reader wants regardless; neither fixes a section list.

## D56 — The redesign's front end: what moved, and the few calls the mock did not make

The mock (`.ui-research/mocks/redesign.html`) is now the app, screen for screen; the gap
list in the ClickUp ticket "Close the remaining UI gaps against the redesign mock" is
checked item by item by `frontend/e2e/redesign.spec.ts`, seeded with the mock's own library
(`e2e/parity-data.ts`). The calls the mock left open:

- **The calendar strip went behind the people chip.** A settled match has no strip: the
  invitation, the addresses and "Not this event" open in a dialog from the chip and from the
  `⋯` menu. A *proposed* match still shows inline, because it is a question only the user can
  answer.
- **The palette and Search are one index.** The palette searches the same `/api/search` and
  groups hits (meetings, action items, transcript lines) above its commands, with a fixed
  key-hint footer; `/search` is the long form with recents and scopes.
- **"Overview" is dropped on display, not on disk.** Prompt v6 (D55) fixes new summaries;
  for the existing library `lib/summary.ts` hides a first heading that is a generic label or
  repeats the title, and only when a paragraph follows for the lead. The file, and the
  Markdown export, keep the document as written.
- **A next step in the summary files itself.** Clicking a `<p class="next">` opens the
  action-item add row with its words, one Enter from the list — the mock drew it as a link
  and gave it nowhere to go.
- **Menus are portalled.** One surface serves `⋯` and right-click, so every row's menu and
  its context menu are the same list and neither is clipped by a scroller. Delete asks in a
  real dialog that names the meeting; `window.confirm` is gone. Toasts are kept for undo
  and for things that finish elsewhere.
- **A person's colour follows their name** across the rail, the transcript, the waveform
  bands and action-item avatars; only a collision within one meeting moves it.
- **The advertised keys work**: `/`, `G L|A|S|,`, `Ctrl R`, `T` and `D W M L` on the
  calendar, `J K X H` and `Ctrl F` in the inbox. A hint for a key that does nothing teaches
  that the hints are decoration.
- **The List view** is the month as an agenda — the question a library is opened with.

## D57 — The browser sign-in is removed; any session on this machine gets the app

The one-time link (`?k=`) and the session cookie it set gated every request (§9 of
`SECURITY-AND-AUTH.md`). In use it failed on experience: each browser profile needed its own
link, the launcher opened links in whichever profile last had focus, and because
`AuthState.session_secret` is generated at start every restart signed every browser out
again — "Authorize this browser" was the most frequent screen in the app.

`AuthMiddleware` now refuses nothing. It still answers the launcher's `POST /api/auth/link`
(the Windows launcher waits for a `?k=` link in the log, so links keep that shape; the token
is ignored) and it hands any request without the `up_csrf` cookie the cookies the UI needs.
The Host check and the CSRF double-submit are unchanged, so websites are still kept out; the
CSRF cookie is `SameSite=Strict`, so a cross-site page cannot read or send it. The selftest
check `api_requires_cookie` became `api_requires_csrf`.

What this gives up: another Windows account or any local program can use the app, and
through it the owner's calendar, AI provider and recorder. Accepted for now and recorded as
known issue #16 and the ClickUp bug https://app.clickup.com/t/z8tj1ha63r, which asks for
research into a fix that is secure *and* friendly — persisting the session secret, opening
links in a chosen browser profile, or identifying the Windows user behind a loopback
connection.

Alongside it, Settings now marks what still needs setting up: a "!" on the sidebar's
Settings item and on the Calendar and Summaries sections when this build can connect a
calendar and none is connected, or the chosen summarizer has no key or sign-in.


## D58 — Codex on the user's own ChatGPT plan: built like D30, with half the permission

**Choice.** `llm.provider` gains `codex-subscription` (`app/llm/codex_cli.py`), and a new
`llm.fallback_provider` (default `""`, none). It is never the default and never will be.

**What it does.** It spawns the `codex` CLI the machine's owner installed and signed into
themselves: `codex exec - --sandbox read-only --skip-git-repo-check --ephemeral
--ignore-user-config --color never --cd <our folder> --output-schema <file>
--output-last-message <file>`, with the whole prompt on stdin. It runs in a folder the app
owns (`<app home>/codex-cli`), never a project and never the recordings. `OPENAI_API_KEY`,
`CODEX_API_KEY`, `CODEX_ACCESS_TOKEN` and `OPENAI_BASE_URL` are stripped from the child's
environment, because with a key set the CLI can bill the API instead of the plan, silently.
**The app never reads, writes or passes the CLI's credential store** (`~/.codex/auth.json` or
the OS keychain). `test_codex_cli_never_sees_a_credential` checks this in the module's
source: `keyring`, `api_key`, `Authorization`, `auth.json`, `session_token` and
`.credentials` must not appear. Sign-in state comes from `codex login status` and nothing else.
Only the method is kept ("ChatGPT account", or "an API key, billed to the API rather than
your plan"); the key fragment the CLI prints is never read.

**What OpenAI has published, checked 2026-09-23.** The dates matter, so the next reader
can tell how old this is.

* **The mechanism is documented.** `codex exec` is OpenAI's documented non-interactive
  mode (learn.chatgpt.com/docs/non-interactive-mode; developers.openai.com/codex now
  redirects there), and it *"reuses saved CLI authentication by default."* Flags were checked against the real
  `codex-cli 0.156.1` `--help`, installed into a scratch prefix and never signed in.
* **Permission for third parties is not published.** Nothing OpenAI has published says
  whether a third-party application may drive `codex exec` on someone's ChatGPT plan.
  An OpenAI engineer, asked directly, declined to answer and pointed at the Terms of Use.
  The App Developer Terms forbid anything that suggests an app is "created, supported,
  certified or endorsed by OpenAI". They do not cover running a user's own CLI.
* **`openai/codex#10974`** ("'Sign in with ChatGPT' for third-party apps so users can
  bring their own plan…", opened 2026-02-07) was the request for exactly this. An archived
  copy (Wayback, 2026-05-05) shows an OpenAI maintainer asking for the use case and then
  **closing it as "not planned" on 2026-03-20**: "This feature request hasn't received
  enough upvotes, so closing." On 2026-09-23 the issue itself returns 404. That closes
  the *feature* request; it is not a ruling on whether driving the CLI is allowed.
  A similar question, `openai/codex#36886` (2026-08-04, "Is there a documented auth
  contract for third-party clients using a ChatGPT subscription…"), is open with no reply.
* **The comparison.** Anthropic published both halves (D30, D46) and still changed its
  position three times in 2026: 4 April, 13 May and 15 June. OpenAI has published one half.

* **What the docs lean toward.** OpenAI's auth docs recommend API keys for "programmatic
  Codex CLI workflows", and the ChatGPT-auth CI section says to use a plan sign-in "only if
  you specifically need to run as your Codex account". The Terms of Use (effective
  2026-01-01) forbid sharing account credentials — which this never does — and
  "automatically or programmatically" extracting Output, whose reach here is unclear.

So this is **an unanswered question, not a refusal** — but a leaning one, and the
recommendation to use an API key for anything programmatic should weigh on release. We build it exactly as the Claude
provider is built and **re-read OpenAI's position immediately before release**, not only
now. If the answer is no, removal is one line (below).

**Naming (Codex 5).** openai.com/brand (Wayback 2026-09-21; the live page refuses
automated fetches) says: *"you may truthfully identify the OpenAI technology you use.
However, everything about your app, product or company (including name, logo,
description…) should be your own and should be free of OpenAI's brands"*. It also says:
*"We do not permit the use of OpenAI models or "GPT" in product or app names"*. It gives
no Codex-specific rule and does not require "powered by". So the row truthfully names the
program being run and the plan it spends: **"Codex CLI (your own ChatGPT plan)"**. There is
no logo and no partnership wording, and it is a label on one option, not a product name.
This is the same line D46 drew for Claude Code: naming a prerequisite is not branding
ourselves as it.

**What it spends.** It spends the ChatGPT plan's Codex allowance, which is shared with
everything else that account does in Codex: a 5-hour rolling window plus a weekly cap.
Settings should say that in one sentence. A product aimed at non-technical users bills its
own API key instead.

**Limits (Codex 3).** "You've hit your usage limit … try again at <time>" is recognised
specifically and raises `QuotaExhausted`, a `Deferred` → `RecoverableError`. Its message is
in plain words: "Your ChatGPT plan's Codex allowance is used up; it resets at …". The
worker parks the job until the reset (`JobQueue.defer`). The reset time can be written three ways ("5:47 AM", "Sep 24th, 2026
5:47 AM", "Aug 20, 2026, 7:38 AM"), and all three are parsed. This uses no attempt, is capped at
a week and waits an hour when no time is given, so the meeting never fails because a window
closed. A signed-out CLI does the same (`SignInRequired`, 15 minutes at a time), because it
is a Settings problem and not a failed meeting. Parked jobs keep their reason in
`last_error` and are listed by `GET /api/attention`. The Claude provider's "usage limit
reached" now raises `QuotaExhausted` as well.

`llm.fallback_provider` is used **only** for `QuotaExhausted`, and never for a sensitive
meeting. It is never used silently: the user has to set it, and the notes record who wrote
them. `meta.json` gets `llm.fallback_for` and `llm.fallback_reason`, and the meeting API
gets `summarized_by`. With no fallback set, the transcript does not leave the machine.

**Removal is one line.** `LLM_PROVIDERS` in `app/config.py` builds both the `llm.provider`
and the `llm.fallback_provider` enums. The Settings rows and the CLI routes are filtered by
it. `_retire_providers` resets a saved config that names a provider no longer listed: the
provider goes back to the default and a fallback naming it is cleared, each with a
`warnings()` message instead of a refusal to start.
`test_removing_codex_is_one_line_and_its_users_fall_back` proves this, so the day OpenAI
says no is a release and not a scramble.

**Install.** Where PowerShell can run it, the app uses OpenAI's installer (`install.ps1`
with `CODEX_NON_INTERACTIVE=1`, as `codex update` itself runs it). Under Constrained
Language Mode it falls back to npm, then to winget `OpenAI.Codex`. That package exists in
microsoft/winget-pkgs (0.156.1) but is not in OpenAI's docs, and where a portable install
lands has not been verified on a real machine.

**What is not proved here.** Everything that only Windows can show (Codex 4) is in
`manual-checks.md`: SmartScreen, elevation and firewall prompts, Constrained Language Mode,
stray child processes and timings. No `windows`-marked test exists yet.

## D59 — Hover means :hover; tooltips explain features and can be turned off; smaller fixes found on the way

**Hover.** Tailwind v4 wraps every `hover:` utility in `@media (hover: hover)`. A device
whose *primary* pointer is touch answers "no" — touchscreen Windows laptops do — so on the
machine the redesign was reviewed on no row, chip or link highlighted, while every test
browser (a mouse-first desktop Chrome) saw it work. `@custom-variant hover (&:hover)` in
`index.css` restores plain `:hover`, which is what the mock uses. `e2e/polish.spec.ts`
checks it in a `hasTouch` browser, where `(hover: hover)` is false; with the line removed,
that spec fails.

**Tooltips.** `Tooltip` is portalled and positioned from the control it is attached to (no
wrapper element, so it fits rows, links and flex children), and takes a one-line `hint`
saying what a feature is *for*. About thirty key features carry one. Settings → Appearance
has "Turn off tooltips" (`ui.tooltips_off`, default false — the explanations are how a new
user learns the app); with it on, controls keep their accessible names.

**"AI agents".** The Settings section that was "Summaries" is "AI agents", with a hover
explanation: it is where the AI that summarizes, extracts action items and answers questions
is set up. The id in the URL stays `#summaries`, so existing links keep working.

**Found while testing Codex:**
- `POST /api/llm/test` wrote the provider under test into the shared config and restored
  the value it saw at the start. Pressing Test right after choosing a provider put the old
  choice back over the new one, and the next save persisted it. It now names the provider
  to `make_client` and touches nothing shared.
- The e2e server builds the worker but never starts it (specs seed jobs in chosen states).
  `POST /api/test/run-jobs` (test mode only) drains the queue on request, so a spec can run
  a real summarize.
- The seed wrote `transcript.json` but not `transcript.md`, so a seeded meeting could be
  shown but never re-summarized. It now writes both, through the assemble stage's own
  `coalesce` and `render_markdown`.
- A stage that is waiting on purpose — a spent Codex allowance, a sign-in — says why on
  the meeting page, from `/api/attention`, instead of looking stalled.

**Addendum to D58, 2026-09-23 — why OpenAI's installer failed on stock Windows.** Inside the
app's install window, `install.ps1` stopped at `RuntimeInformation::OSArchitecture` with
"The property 'OSArchitecture' cannot be found on this object". The window is an
*interactive* Windows PowerShell 5.1 (it stays open for the sign-in), so it loads PSReadLine;
the PSReadLine 2.0.0 that ships inside Windows 10 and 11 carries its own internal
`System.Runtime.InteropServices.RuntimeInformation` with only `OSDescription`, and once loaded
that is what the bare type name resolves to. Found by logging the type's assembly from inside
the failing window. Every probe run non-interactively had answered `X64`, which is why it
took that. The installer now runs in a non-interactive PowerShell of its own inside the same
window (`codex_cli.isolated`), where PSReadLine is never loaded; the window's own diagnostics
ask for the type by its `mscorlib`-qualified name and report the PSReadLine version. The
npm → winget fallback stays for any other failure. Verified on Windows 11 with the real
installer up to the failing line; the full install on a clean machine is the Windows
testing epic's job, because on a developer machine it would rewrite the user's PATH.


## D60 — One speech model, ivrit-ai large-v3, always told Hebrew; the language is read from the transcript

**Choice.** `ivrit-ai/whisper-large-v3-ct2` is the only speech model, on the GPU and the
CPU alike. It is always called with `language="he"`. Nothing detects the language before
transcription; the meeting's language (which picks the summary's language under
`summary.language = auto`, and the page direction) is the script most of the transcript's
letters are in (`app/asr/language.py`: Hebrew at ≥ 25 % of the letters). Removed: the
turbo fine-tune for the CPU, stock Whisper for non-Hebrew meetings, the first-run
"meeting language" question, `asr.language_mode`, `asr.default_language`,
`asr.detect_min_confidence`, `asr.model_repo`, and every backend's `detect_language`. A
test fails if any other Whisper repo id appears in `app/`.

**Why.** Measured on 2026-09-25 on the author's machine (GTX 1080, int8) with an English
clip (40.6 s), a Hebrew clip (102.9 s) and the two spliced Hebrew → English → Hebrew
(66 s). Transcripts in the author's `F:\Junk\upshot\lang-tests\`.

| Model, how it was called | English | Hebrew | Mixed |
|---|---|---|---|
| ivrit-ai large-v3, `language="he"` | English, correct (no punctuation) | best | **each part in its own language** |
| ivrit-ai large-v3, `language="en"` | drifts into invented Hebrew from 14 s | — | — |
| ivrit-ai large-v3-turbo, `language="he"` | English, correct | very good | **the English translated into Hebrew**, 11 s dropped |
| stock large-v3-turbo, auto / `multilingual=True` | correct | worse than ivrit-ai | **the Hebrew translated into English** |

The turbo fine-tune's failure is machine B's (ClickUp z8tj1haczh): an English meeting came
out as Hebrew nobody said. The fix shipped for it on 2026-09-24 (a first-run language
choice, routing English to stock Whisper) handled one-language meetings only, and asked
the user something the app can work out. Language identification itself was also tested:
ivrit-ai's own detector answers Hebrew at p = 1.00 for every 10 s window, English
included, which is why the transcript's script is used instead. Stock whisper-small
detects both languages correctly (p 0.93–1.00) and stays the fallback plan if a meeting
ever needs segment-level routing.

**Cost.** The CPU gets the 3.09 GB model instead of 1.62 GB, and runs slower: large-v3 int8
on an i7-8700 (6 cores) took 409 s for 102.9 s of speech (**≈ 4× the audio**), peak RSS
4.6 GB. CPU-only machines therefore transcribe after the meeting, when idle, and the
setup screen says so. A GPU machine is unchanged.

**What is kept.** The hardware fallbacks: no CUDA, under 4 GB of VRAM, or a failed GPU
load all run the same model on the CPU (int8); the GPU's supported compute type is still
probed (the GTX 1080 refuses `int8_float16`); `asr.model_path` still points at a model
already on disk.

**Not measured yet.** Real meetings rather than clips, and English terms inside Hebrew
sentences — the commonest mix here. Diarization (D29) is unaffected: it works on voices,
not language.

## D61 — The AI assistant: PydanticAI for the API models, the two CLIs over our own MCP server, read-only tools over the existing index

_Status: decided on 2026-09-25 by the product owner (ClickUp z8tj1hawba, epic z8tj1hawb9).
Nothing is built yet. The decisions table at the end records each ruling._

**Ruled by the product owner, 2026-09-25: local models are out of this plan.** v1 runs on
the five cloud routes: the Anthropic, OpenAI and Gemini APIs, and the Claude Code and Codex
CLIs. When Ollama is the chosen provider, the panel says the assistant does not work with
local models yet. The research on them is kept under "Left for later" below.

**Ruled by the product owner, 2026-09-25: the application may send all the data it holds to
the chosen model.** Transcripts, summaries, notes, action items, calendar details and
related meetings are all in scope for the assistant's tools. The privacy page, the site's
FAQ and the capture screen's text were corrected to say so. Before this, they said
calendar data never reaches an AI provider, which D45 had already made untrue.

**Choice.** A new package, `app/assistant/`, runs one assistant for the whole application.
- **Three routes share one harness.** **PydanticAI** (`pydantic-ai-slim`, MIT) runs the
  agent loop for Anthropic, OpenAI and Gemini. It supports all three natively, as well as
  Ollama for later, with streaming and typed tools, and its message history serialises to
  JSON.
- **The two CLIs run their own loops.** The Claude Code and Codex CLIs call the same tools
  through an MCP server that Upshot serves on 127.0.0.1 with a token made at each launch.
  Upshot relays their events.
  - Claude: `claude -p --output-format stream-json --mcp-config … --strict-mcp-config
    --allowedTools "mcp__upshot__*"`, resumed with `-r`.
  - Codex: `codex exec --json` with `-c mcp_servers.upshot…`, resumed with `exec resume`.
  - Tools are written once as plain functions and registered both as PydanticAI tools and
    on the MCP server (the official `mcp` SDK).
- **Tools are read-only in v1.**
  - `search` (FTS5), `list_meetings`, `get_meeting` (summary, notes, action items),
    `get_transcript(id, from_ms, to_ms)`, `list_action_items`, `calendar_range`,
    `related_meetings` (D53).
  - For questions about the app: `app_help(topic)` and `app_status` (non-secret
    settings, provider sign-in state, recent failed jobs).
- **Answers cite their sources.** The model cites `(meeting_id, at_ms)` pairs taken from
  tool output. The server checks and snaps them to a transcript turn, exactly as
  `ask.py:_citations` does, and a citation click opens `/m/<id>?at=<ms>`.
- **Conversations are kept.** Sessions live in two new tables, `assistant_sessions` and
  `assistant_messages` (migration 0006). Each session gets a title after its first
  exchange, and older turns are compacted when a session nears the context window; the
  full history stays on screen.
- **The stream follows a published format.** Replies stream as server-sent events in the
  Vercel AI SDK "UI message stream" format. It is small and well specified, so the
  frontend can change libraries without touching the backend.
- **The panel is a side sheet.** It docks on the inline-end side and does not block the
  screen.
  - Ctrl/Cmd+J opens and closes it; Esc closes it. A launcher button shows when it is
    closed, and it is hidden on `/welcome`.
  - A removable chip sets the scope to the current screen ("This meeting").
  - Tool steps show as collapsible lines ("Searching meetings for 'budget'… 6 results").
  - It has Stop, suggested prompts per screen, and a session list.
  - `dir="auto"` goes on every message and on the input, with `<bdi>` around timestamps and
    citations.
  - It mounts next to `CommandPalette`/`Toaster` in `App.tsx`. The deeper UI study stays a
    separate, later item.

**Why each part.**
- **The harness.** PydanticAI is the only candidate that covers the three API routes (and
  Ollama, for later) natively, is pure Python and small (it freezes cleanly under
  PyInstaller), and needs no router.
  - **OpenAI Agents SDK** was the runner-up. It has good sessions, but Anthropic, Gemini
    and Ollama go through a beta LiteLLM extension.
  - **LangGraph and LlamaIndex** carry too much dependency weight for one chat panel.
  - **smolagents'** main mode runs Python the model writes.
  - **Agno** is now a hosting platform.
  - **Claude Agent SDK** covers Claude only and bundles its own CLI binary.
  - **LiteLLM** is large, and it was compromised on PyPI on 2026-03-24 (1.82.7/1.82.8 stole
    credentials); that is not something to freeze into an installer.
  - **Mem0 and Letta** are not needed: the meeting database already is the memory.
- **The CLIs over MCP.** Both CLIs run their own agent loop and will not take an outside
  tool-calling protocol, but both accept MCP servers. So MCP is the only way to give them
  Upshot's tools, and it costs one server we would want anyway.
  - `codex mcp-server` was removed in Codex 0.154.0. `codex app-server` (JSON-RPC, with
    experimental client-side tools) is the fallback if `exec --json` proves too limited.
- **The terms of use.** Anthropic's terms allow a user to run the unmodified `claude`
  binary on their own subscription. They forbid an app from routing, storing or proxying
  those credentials. Upshot spawns the user's own binary and never reads `~/.claude` or
  `~/.codex`, as D30/D58 already do. These routes stay opt-in, and the terms are checked
  again before each release.
- **Agentic search, not embeddings.**
  - Tool-based keyword search matches retrieval-augmented generation (RAG) in recent
    results, and it is the approach Claude Code itself moved to.
  - It needs no index to keep fresh, and it reuses FTS5 and D53.
  - Two gaps must close first:
    - **Summaries and notes are not in the database** (`dao.py:597`), so search cannot
      reach them.
    - **`unicode61` does not strip Hebrew prefixes** (ו/ה/ב/ל/ש), so "בתקציב" misses
      "תקציב". An FTS5 `trigram` table fixes that.
  - **Embeddings wait** until a measured miss rate asks for them. The candidate then is
    EmbeddingGemma (about 200 MB quantised, Hebrew included) with `sqlite-vec`.
- **Knowing the app.** Today the only text that describes the app to users is the 34
  `help.*` tooltip strings.
  - A small corpus, `docs/help/`, gets one file per screen and per settings section. It is
    bundled with the app and read by `app_help`.
  - "Why did this fail" comes from `app_status`, not from prose.
  - A test fails when a settings key or route has no help entry, which keeps the corpus
    current.
- **Memory.** No automatic memory across sessions in v1. It would need view, edit and
  delete screens, and it is one more place for injected text to persist. If users ask
  for it, an explicit "About me" field in Settings comes first.

**Privacy and safety.**
- **Nothing is withheld.** Per the ruling above, the tools return every meeting's content
  to whichever model the user chose.
- **No way to send data out.** Private data and untrusted text (transcripts, calendar
  invitations) are both in scope by design, so v1 removes the third leg of the "lethal
  trifecta": the ability to send data out.
  - The assistant has no web, email or other outbound tools.
  - Model markdown renders no remote images.
  - Links are limited to Upshot's own routes.
- **Tool output is marked as data.** It is wrapped in randomised delimiters, and the system
  prompt says marked text is data, never instructions.
- **Writes wait for v2.** Any tool that writes (add an action item, edit a summary) waits
  for v2 and goes through a confirmation card that shows the exact change.
- **The privacy page.** Already corrected by the ruling above: it says the chosen provider
  may receive any of the text Upshot keeps, calendar details included.

**Cost and performance (estimates, not measured).**
- **Cloud.** A cloud turn is about 3k tokens of instructions and tools, plus tool output
  capped at about 8k per call. That is roughly 15–30k input tokens for a turn with two or
  three tool calls.
- **Subscriptions.** The CLI routes spend the user's plan allowance, and a spent allowance
  falls back as summaries do (`QuotaExhausted` → `llm.fallback_provider`), unless the
  fallback is the local model. That fallback
  moves out of `summarize.py` so the assistant can share it.

**Found on the way.** Ask (D54) uses the configured `llm.provider`, not the provider that
wrote the summary, although its docstring and D54 say otherwise. Either the code or the
text is wrong; this is left to its own ticket.

**Proposed backlog items, in order.**

| # | Item | Effort |
|---|---|---|
| 1 | Search that reaches everything: index summaries and notes, add a trigram FTS table for Hebrew prefixes | M |
| 2 | Assistant core: sessions tables, streaming chat endpoint (AI SDK protocol), PydanticAI on Anthropic/OpenAI/Gemini, the read-only tools, checked citations | L |
| 3 | The panel, v1: side sheet, Ctrl/Cmd+J, streaming, tool steps, citations, session list, scope chip, RTL | L |
| 4 | Safety: delimited tool output, markdown without remote content, links only to Upshot's own screens | S |
| 5 | The MCP server (localhost, per-launch token) and the Claude Code CLI route | M |
| 6 | The Codex CLI route (`exec --json` + MCP config) | M |
| 7 | Knowing the app: `docs/help/` corpus, `app_help` and `app_status`, the test that keeps it current | M |
| 8 | Long conversations: titles, compaction, shared quota fallback | S |
| 9 | The deeper UI study (the epic's "researched separately, later") | S |

Later, not v1: writes with confirmation; embeddings as a hybrid, if measured; folding
"Ask this meeting" into the panel; local models (below).

**Decisions, as ruled by the product owner on 2026-09-25.**

| # | Decision | Ruling |
|---|---|---|
| 1 | The harness | PydanticAI, with the CLIs over an MCP server |
| 2 | Claude Code and Codex subscriptions as assistant routes | In v1, opt-in; terms re-checked before release |
| 3 | Changing data from the assistant | **Read-only in v1.** A later version may change action items, meeting titles, speaker names and tags, each behind a confirmation card. Deleting meetings, settings, keys, sign-ins and recording stay off-limits in every version |
| 4 | Memory across sessions | None in v1 |
| 5 | Embeddings | **None in v1** |
| 6 | Chat frontend | AI SDK `useChat` with our own components |
| 7 | Panel details | Set now as v1 defaults (side sheet, Ctrl/Cmd+J); the UI study refines them later |
| 8 | "Ask this meeting" | Stays beside the panel; folded in after panel v1 |
| 9 | Ask's provider (configured vs the one that wrote the summary) | Its own ticket |
| 10 | The nine backlog items | Added to the epic as listed |

**Left for later: local models.** Findings kept for when Ollama is taken up:
- On an 8 GB machine, `qwen3:4b` is the tool-calling model that fits. `gemma3` has no
  tool support at all.
- The assistant reads the model's capabilities from `/api/show`.
  - **With tools:** it offers a short list of narrow tools, with thinking turned off,
    because of an Ollama bug where tool calls inside thinking blocks hang.
  - **Without tools:** Upshot runs the search itself and asks one question with the
    results in context. That is `ask.py` today, widened.
- `qwen3:4b` needs about 3 GB of RAM. Next to the CPU speech model (peak 4.6 GB, D60),
  an 8 GB machine cannot run both at once.

## D62 — The assistant panel: a docked column, built from our own components on AI SDK `useChat`, with honest citations and Hebrew handled per block

_Status: adopted on 2026-09-25 with every recommendation below, when the product owner
started the implementation without ruling on them one by one; any of them can still be
revisited (ClickUp z8tj1hax1h, epic z8tj1hawb9). It replaces D61's first-pass panel
defaults._

**Order changed by the product owner, 2026-09-25: the subscription comes first.** The first
build runs the assistant on the user's own Claude plan through the Claude Code CLI, with
the Codex CLI second; the API keys (D61's PydanticAI harness) follow in a later epic. The
CLI route needs no harness of ours: the CLI runs the loop and calls Upshot's tools over
MCP, and Upshot translates its `stream-json` events into the AI SDK UI message stream
itself. The implementation plan is `docs/assistant-plan.md`.

Three research passes fed this entry:
- **Published guidance:** Microsoft HAX, Apple's generative-AI HIG, Google PAIR, Nielsen
  Norman Group (NN/G), IBM Carbon, GitHub Primer's Copilot accessibility patterns, and
  shapeof.ai.
- **Shipping products:** Linear, Notion, Granola, Fireflies, Otter, Slack, Microsoft 365
  Copilot and VS Code Copilot Chat.
- **Libraries:** the open-source chat libraries, installed and measured in a scratch
  folder, plus a map of Upshot's own frontend.

**Choice: the panel.**
- **Where it sits.** The panel is a docked column on the inline-end side, a sibling of the
  main column in `App.tsx`. It pushes the content aside instead of covering it, and the
  `xl:` rails drop off as they already do when there is less room. This follows the
  makeover's own rule, "a second workspace → a persistent rail, not a sheet".
  - Width: 400px by default, resizable between 360 and 640px with a drag handle, and
    remembered per machine.
  - It stays open across route changes. It is hidden on `/welcome`.
  - While it is open, the Toaster moves inward so the panel does not cover toasts.
- **Opening and closing.**
  - Ctrl/Cmd+J is bound by `KeyboardEvent.code`, so it still works when the keyboard is
    in Hebrew, and it calls `preventDefault`, since browsers use Ctrl+J for Downloads. It
    matches Linear and Fireflies.
  - A launcher button sits in the sidebar, and the command palette gets an entry for it.
  - Opening focuses the composer. Esc from the panel, or Ctrl+J again, closes it and
    returns focus to where it came from.
  - F6 and Shift+F6 cycle between the main area and the panel, the Windows convention.
    There is no focus trap: the panel is not modal.
- **Scope.** A chip above the composer shows what the question covers: "This meeting" on a
  meeting page, "All meetings" elsewhere. It follows the current screen, and removing it
  widens the scope to everything. This is Granola's model, and it follows HAX G4 (show
  what fits the current context).
- **Empty state.**
  - One sentence says what the assistant can answer and what it cannot, since v1 only
    reads.
  - Three suggested prompts depend on the screen, shown as buttons. On a meeting page:
    "What did we decide?", "What do I owe from this meeting?", "Who said what about…".
    Elsewhere: "What's open for me this week?", "When did we last talk about…".
- **While it works.**
  - Each tool call is a collapsible line with its own state (running, done, failed),
    worded exactly: "Searching 42 meetings for 'budget'… 6 found". Nothing is shown as
    "Thinking…".
  - The answer streams in.
  - Stop keeps the partial answer and offers Retry.
  - The view does **not** scroll past the start of a long answer, so the reader stays at
    its beginning (NN/G).
- **An answer.**
  - The short answer comes first. There is no small talk and nothing like "I think" (NN/G,
    "Less chat, more answer").
  - **Citations** are numbered chips placed next to the claim they support.
    - Hovering or focusing one shows a card with the quoted transcript line, the speaker,
      the meeting and the time. Checking the quote is then cheap, which counters
      over-trust (NN/G).
    - Clicking one opens `/m/<id>?at=<ms>` and seeks, exactly as Ask does today.
    - A citation whose meeting is gone renders as "source unavailable".
  - **When nothing is found, the answer says so.** It reads "No meetings mention X", shows
    the searches that ran with that tool step left open, and makes nothing up.
  - A small footer names the provider and model that answered and has Copy and Retry.
  - Two or three follow-up suggestions appear once the answer is finished. They come in
    the same response, so they cost no extra call.
- **Errors, each with a next step.**
  - Provider signed out: a "Sign in" button that opens Settings at that provider.
  - Allowance spent or rate limited: when to try again, and the fallback if one is set.
  - Network error, and stopped partway: Retry.
  - Local model chosen: the assistant does not work with local models yet (D61), with a
    link to AI settings.
- **Disclosure, not a choice.** Per D61, all data may go to the chosen model. The first
  time the panel opens it says so in one line, naming the provider ("Questions and the
  meeting text they need go to Claude. Upshot keeps everything else on this PC"), with a
  link to the privacy page. A one-line reminder stays under the composer, where NN/G says
  a disclaimer is actually read, not in a footer.
- **History.**
  - A session list, grouped Today / This week / Older, sits behind a button in the panel's
    header.
  - Sessions get an automatic title, and can be renamed and deleted. Search covers titles
    only in v1.
  - "New chat" is always one click away.

**Choice: accessibility.**
- **Announcements.**
  - The conversation is a `role="log"`, but the streaming text itself is not announced; a
    token-by-token stream floods screen readers.
  - One visually hidden `role="status"` region, always present in the page, announces the
    states: "Searching meetings…", then "Still working" every 5 seconds (GitHub Copilot's
    rhythm), "Answer ready, 3 sources", "Stopped", and errors.
  - The streaming message carries `aria-busy`.
- **Structure and focus.**
  - Each answer starts with a visually hidden "Assistant" heading, so H jumps between
    answers.
  - Focus never moves on its own when an answer completes.
  - Tool lines are disclosure buttons with `aria-expanded`.
  - Deleting a session moves focus to the previous item.
  - The panel is an `<aside aria-label>`.
- **Reduced motion.** The panel's slide and the "working" shimmer are removed, and text
  appears in blocks. This reuses the existing reduced-motion gates in `index.css`.

**Choice: Hebrew and mixed text.**
- **Direction per block.**
  - `dir="auto"` goes on every user message, on each markdown block of an answer, and on
    the composer, so direction follows each paragraph's content, not the UI language. A
    Hebrew UI can get an English answer, and one answer can mix both.
  - This is the first `dir="auto"` in the app; today direction follows the locale or the
    meeting's language.
- **Isolated inserts.**
  - `<bdi>` wraps meeting titles, speaker names and citation chips, so "[3]" and "12:34"
    do not jump across a Hebrew sentence.
  - Timestamps, code and tool JSON are `dir="ltr"`.
- **Icons.** Send, chevrons and the collapse icon mirror. Play/seek, clock, search and the
  checkmark do not.
- **The composer.**
  - Enter sends and Shift+Enter adds a new line, but never while an input method is still
    composing (`isComposing`).
  - Its auto-size layer uses the same bidi CSS as the textarea, since the caret drifting
    in Hebrew is a bug already reported against Claude Code and t3code.

**Choice: libraries.**

| Layer | Pick | Why |
|---|---|---|
| Stream protocol, backend | PydanticAI's own `VercelAIAdapter` (`pydantic_ai.ui.vercel_ai`), `sdk_version=7` | It already emits the AI SDK UI message stream from our harness, header included, and converts messages both ways for storing sessions. Citations travel as typed `data-citation` parts from `ToolReturn` metadata. No protocol code of our own |
| Client hook | `@ai-sdk/react` `useChat` (Apache-2.0, ~58 KB gz) | Streaming, Stop, Retry, tool and data parts. It has no session list; that is ours, on TanStack Query. Needs React ≥ 19.2.1: the lockfile has 19.2.8, and `package.json`'s `^19.0.0` is raised to match |
| Components | **Our own**, in the app's idiom (tokens, `Tooltip`, `Menu`, the existing focus and motion rules), taking layout ideas from Vercel's AI Elements and prompt-kit source | The app has no component library: no shadcn, no Radix. Adding one for a single panel goes against the makeover's advice |
| Stick-to-bottom scrolling | `use-stick-to-bottom` (MIT, ~2.7 KB, no deps) | What AI Elements, prompt-kit and CopilotKit all use. Scrolling is vertical, so it has no RTL issue |
| Markdown | **Streamdown** (Apache-2.0, ~156 KB gz), lazy-loaded with the panel | Built for streaming: it repairs half-finished markdown, memoises per block, sanitises, and has **`dir="auto"` per block**, which mixed Hebrew/English needs. Its optional code, math and mermaid plugins stay out. Links route inside the app, and its default link-safety modal is replaced |

**Rejected.**
- **assistant-ui** (MIT) is the strongest runner-up. It has the most complete thread list
  and RTL support, but costs about 240 KB gz, brings a second state store next to
  TanStack Query, and churns through frequent 0.x releases. Most of it (branching,
  editing, attachments, voice) is useless for a read-only panel. It runs on top of
  `useChat`, so it stays open for later.
- **AI Elements copied in as-is** needs shadcn and Radix set up, uses physical classes
  (`ml-auto`, `left-[50%]`), and its inline citation is a carousel.
- **CopilotKit** is about 3.7 MB gz, has a GraphQL runtime and no RTL work.
- **LlamaIndex chat-ui** is stale and heavy.
- **Deep Chat** uses a shadow DOM that fights Tailwind.
- **chatscope** is stale and has no streaming markdown. **NLUX** is stale, supports React 18
  only, and is MPL-licensed.
- **Chainlit and Gradio** are separate apps.
- **The AG-UI protocol** is sound and now stable, but its strengths (shared agent state,
  frontend tools, interrupts) serve an assistant that acts. It becomes the path if writes
  arrive.
- **react-markdown** is about a third of Streamdown's size, but needs our own block
  splitting and has no per-block direction.

**Deferred.**
- A full-page view, and tabs of open chats (Linear).
- @-mentions of meetings and people (Fireflies); the scope chip covers v1.
- Saved prompts (Granola's recipes), voice input, export and share, and search inside
  conversations.
- A "what was sent" inspector.
- Any confidence display: Google PAIR advises against false precision.
- Thumbs up and down.

**Effect on the backlog.**
- AI assistant 3 (the core) uses `VercelAIAdapter` instead of a hand-written stream.
- AI assistant 4 (the panel) is built to this entry and stays L.

**As built (2026-09-26, branch `ai-assistant`).** Everything above, with these
differences, each for a reason found while building:
- **No `use-stick-to-bottom`.** The question scrolls to the top on send and the view never
  follows the stream, which is the NN/G rule itself; there was nothing left for it to do.
- **Codex keeps nothing.** `codex exec --ephemeral` with a recap of the stored
  conversation each turn, instead of `exec resume`: nothing lands in the user's own Codex
  history (as with the summarizer), and the resume flags could not be confirmed without a
  Codex to try them on. Claude Code does resume, and when it has lost a session the
  question is asked again with a recap before the user sees anything.
- **Codex answers arrive whole.** `exec --json` does not stream an agent message's text,
  only the finished message; tool steps still appear as they happen.
- **The data fence is named at start-up** (`<upshot-data-xxxxxxxx>`), not per request:
  one MCP server serves every conversation, and a name the transcript cannot know is what
  matters.
- **Follow-ups** are written by the model at the end of its answer
  (`[[suggest: … | …]]`) and taken out of the text, so they cost no extra call.
- **History search** covers titles only, as D62 said.

**Open decisions for the product owner.**

| # | Decision | Recommended |
|---|---|---|
| 1 | Docked column that pushes content, or a sheet that overlays it | Docked |
| 2 | Components: our own, a library (assistant-ui), or AI Elements with shadcn and Radix | Our own |
| 3 | Markdown: Streamdown (lazy-loaded), or react-markdown | Streamdown |
| 4 | Thumbs up and down on answers: nobody receives them in a local app | Not in v1 |
| 5 | Follow-up suggestions after each answer | Yes, in the same response |
