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
