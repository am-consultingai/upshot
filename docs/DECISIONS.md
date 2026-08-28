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
