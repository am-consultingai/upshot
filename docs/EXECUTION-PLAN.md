# Upshot — Execution Plan

An implementation plan for an autonomous full-stack agent. Every phase ends in a green
automated check that **requires no human** — no clicking, no speaking into a microphone,
no eyeballing a screenshot.

**Inputs:** `DESIGN.md` (architecture), `TECHNICAL-DESIGN.md` (spec), `DETECTION.md`
(detection mechanism), `STACK.md` (dependencies), `SECURITY-AND-AUTH.md` (auth model).
This plan is self-contained: everything needed to build is either here or in those five
documents.

---

## 0. Readiness

The technical design is ready to build against. Five items in `TECHNICAL-DESIGN.md` §19
are *measurements*, not unknowns blocking design — and three are answered by phases in this
plan rather than by discussion:

| Open item | Resolved by |
|---|---|
| FTS5 present in the bundled SQLite | **Phase 1** — probe with fallback |
| Dual-stream WASAPI stability | **Phase 4** — the spike is the phase |
| Real Hebrew token ratio | **Phase 7** — measured, then asserted as a regression bound |
| `nemotron_h` support in the local runtime | Phase 7, optional path; skipped if absent |
| Two-clock drift over hours | Phase 4 soak test reports it; §4.3's decision revisited only if it exceeds 1 s/hour |
| Language-detection accuracy on a first chunk | **Phase 5** — measured; extend to two chunks if below bar |

**Every open item is a measurement with a scheduled phase. There is no question requiring
a human answer before or during this build.**

### Scope of this version

**In:** local recording, transcription, summarization, HTML rendering, SMTP delivery, the
local web UI, the tray app, and auto-detection. **V1 = M0 + M1 + M2.**

**Out:** Google Calendar and any Google OAuth. Not deferred-but-assumed — *out*. The build
must be complete and useful with no Google account in existence. What this plan does
include is the **seam** (Phase 6b) so calendar can be added later as one implementation of
an existing protocol, without reworking the pipeline.

Note that SMTP delivery is unaffected by this: it uses a plain SMTP server and an app
password, with no Google API, no OAuth, and no consent screen. That decoupling was made
deliberately in `SECURITY-AND-AUTH.md` §1 and is what lets calendar drop out cleanly.

---

## 1. Self-testing doctrine

The product is a Windows audio recorder, which naively means "a human must speak into a
microphone". Four techniques remove the human entirely. **Everything else in this plan
depends on them, so build them first (Phase 0).**

### T1 — Speech fixtures from Windows SAPI
Windows ships a TTS engine reachable via COM. Tests synthesize their own speech, so audio
fixtures are generated on demand rather than committed as binaries, and the expected
transcript is known exactly.

```python
# tests/fixtures/speech.py
def synth(text: str, out: Path, rate: int = 0) -> Path:
    import win32com.client
    v = win32com.client.Dispatch("SAPI.SpVoice")
    fs = win32com.client.Dispatch("SAPI.SpFileStream")
    fs.Open(str(out), 3)                 # SSFMCreateForWrite
    v.AudioOutputStream, v.Rate = fs, rate
    v.Speak(text); fs.Close()
    return out
```

Generated once per session into a temp dir, cached by hash of `(text, rate, voice)`.
On non-Windows CI, tests requiring T1 are skipped and the phase's Linux-safe subset runs.

### T2 — The loopback echo test (end-to-end audio, no hardware assumptions)
WASAPI loopback captures whatever the render endpoint plays — **including audio this
process plays itself**. So a test can play a known signal and capture it, closing the loop:

```
play(fixture.wav) ──► default render endpoint ──► WASAPI loopback ──► capture ──► assert
```

Assertions: cross-correlation peak ≥ 0.8 against the source, detected offset < 500 ms,
sample count within 1 % of expected, zero xruns. This validates device enumeration, stream
opening, resampling, chunking and the manifest **on real hardware with no human**.

### T3 — Self-held microphone (real detection signals)
Opening a capture stream registers *this* process in the microphone ConsentStore. A test
can therefore open a mic stream and assert the registry reader observes its own PID holding
the mic — exercising the real Windows signal path rather than a mock.

### T4 — Fakes at every boundary
`FakeAsr`, `FakeLlm`, `FakeCapture`, `FakeClock`, `FakeNotifier`, `FakeRegistry`,
`FakeSmtp` (a real `aiosmtpd` server on a loopback port). Every one is selected by config,
never by monkeypatching in the test body — so the wiring itself is exercised.

### Determinism rules
- No `datetime.now()` outside `app.clock`; tests inject `FakeClock`.
- No `random` without an injected seed. Meeting ids come from `clock` + a seeded counter in tests.
- Golden files are regenerated with `pytest --update-goldens` and diffed in review, never silently overwritten.
- Every test that touches the app home uses a `tmp_path` app home via the `UP_HOME` env var.

### The single entry point
```
python -m app.selftest all           # everything runnable in this environment
python -m app.selftest audio         # T2 loopback echo test
python -m app.selftest pipeline      # fixture WAV → summary.html with fakes
python -m app.selftest live-llm      # one real API call, schema-validated
python -m app.selftest detect-shadow # 60 s of real detector signals, no commit
```
`selftest` exits non-zero on any failure and prints a machine-readable JSON report to
`--report out.json`. **This is the agent's primary feedback loop.**

---

## 2. Conventions

- **Package manager:** `uv`. `uv sync` installs; `uv run` executes. Lockfile committed.
- **Layout:** exactly `TECHNICAL-DESIGN.md` §2.
- **Tests:** `tests/unit/`, `tests/integration/`, `tests/e2e/`, `tests/fixtures/`. Markers: `@pytest.mark.windows`, `@pytest.mark.audio_hw`, `@pytest.mark.gpu`, `@pytest.mark.live_api`.
- **Default test run** excludes `live_api`. CI on Linux runs everything not marked `windows`/`audio_hw`/`gpu`.
- **Typing:** full annotations; `mypy --strict` on `app/` (not on tests).
- **Lint:** `ruff check` + `ruff format --check`.
- **Every phase ends with:** `uv run ruff check . && uv run mypy app && uv run pytest -q && uv run python -m app.selftest all`

---

## Phase 0 — Skeleton and the test harness

**Goal:** the scaffolding every later phase asserts against exists and is green.
**Depends on:** nothing.

**Deliverables**
- `pyproject.toml` (Python ≥3.13, deps per `STACK.md`), `uv.lock`
- Full empty package tree from `TECHNICAL-DESIGN.md` §2, every module importable
- `app/clock.py` — `Clock` protocol, `SystemClock`, `FakeClock(advance/set)`
- `app/selftest.py` — subcommand dispatcher, JSON reporter, exit codes
- `tests/fixtures/speech.py` (T1), `tests/conftest.py` with `app_home`, `fake_clock`, `seeded` fixtures
- `.github/workflows/ci.yml` — Linux job; Windows job marked `continue-on-error: false` but skipping `audio_hw`

**Implementation notes**
- `selftest` must run from both source and a PyInstaller freeze — no `__file__`-relative asset lookups; use `app.paths.resource(...)` from day one.
- The JSON report shape is fixed now and never changes: `{"suite":str,"ok":bool,"checks":[{"name":str,"ok":bool,"detail":str,"metrics":{}}]}`.

**Tests**

| Test | Assertion |
|---|---|
| `test_imports_all_modules` | walk `app/**`, `importlib.import_module` each — catches circular imports and typos before they compound |
| `test_selftest_reports_json` | `selftest all --report` writes a file matching the schema; exit 0 |
| `test_fake_clock_monotonic` | `advance(5)` moves `now()` by exactly 5 s; no wall-clock leakage |
| `test_speech_fixture_generates` *(windows)* | `synth("hello world")` produces a WAV > 0.5 s, 1 channel, readable by `wave` |
| `test_speech_fixture_cached` *(windows)* | second call with same args returns the same path without re-synthesizing |

**Exit criteria:** `ruff`, `mypy`, `pytest`, `selftest all` all green on Linux and Windows.

---

## Phase 1 — Config, paths, database

**Goal:** durable state with enforced invariants.
**Depends on:** 0.

**Deliverables**
- `app/paths.py` — app home from `UP_HOME` or `%LOCALAPPDATA%\upshot`; `resource()` resolves bundled assets under both source and frozen layouts
- `app/config.py` — three-layer merge (defaults → `app_config.json` → env), schema-validated, typed accessors; `secrets.get/set` over `keyring` with `FakeKeyring` for tests
- `app/db/schema.sql`, `migrate.py`, `dao.py` — exactly the DDL in `TECHNICAL-DESIGN.md` §3.1

**Implementation notes**
- Migrations are numbered `.sql` files applied in order inside one transaction each; `schema_version` is a single-row table.
- **FTS5 probe at startup:** `CREATE VIRTUAL TABLE temp.fts_probe USING fts5(x)` in a try/except. On failure set `capabilities.fts=False` and route search to a `LIKE` fallback. Record the result in `selftest` metrics — this closes §19.1.
- `dao.set_state(meeting_id, new)` asserts membership in `LEGAL_TRANSITIONS` and raises `IllegalTransition` otherwise.
- `config.redacted_dump()` returns config with every secret key replaced by `"***"`; the settings API and diagnostics bundle use only this.

**Tests**

| Test | Assertion |
|---|---|
| `test_migrations_idempotent` | run twice on a fresh DB → same `schema_version`, no error, table set unchanged |
| `test_migration_atomic` | inject a failing statement into migration 2 → `schema_version` stays 1, DB usable |
| `test_fts5_probe_reports` | probe returns a bool; when forced off, `search()` still returns correct rows via `LIKE` |
| `test_fts_hebrew_diacritics` *(fts only)* | insert two Hebrew strings differing only in niqqud; a search for one does **not** match the other (proves `remove_diacritics 0`) |
| `test_dao_meeting_roundtrip` | insert → fetch → all fields equal, timestamps ISO-8601 with offset |
| `test_illegal_transition_raises` | `RECORDING → DELIVERED` raises `IllegalTransition`; legal path succeeds |
| `test_config_layer_precedence` | env beats file beats default, per key, with a 3-way conflict fixture |
| `test_secrets_never_in_config_file` | write a secret, dump `app_config.json`, assert the value string does not appear anywhere in the file bytes |
| `test_redacted_dump` | every key named in `SECRET_KEYS` renders as `"***"` |
| `test_apphome_respects_env` | `UP_HOME=tmp` → DB created under tmp, nothing written to `%LOCALAPPDATA%` |
| `test_data_root_default` | unset `data_root` → resolves under the app home; set → resolves to the configured path |
| `test_data_root_change_preserves_old` | create a meeting, change `data_root`, create another → both readable; the first still resolves via its stored absolute `folder` |
| `test_synced_folder_warning` | `data_root` under a path containing `OneDrive`/`Dropbox`/`Google Drive` (or matching `%OneDrive%`) → config validation returns a warning, not an error |
| `test_meeting_folder_is_absolute` | `meetings.folder` is always absolute — a relative path is rejected at insert |

**Exit criteria:** all green; `selftest all` includes an `fts5` capability metric.

---

## Phase 2 — State machine, job queue, worker

**Goal:** a pipeline runner that survives crashes and preemption, proven without any real stage.
**Depends on:** 1.

**Deliverables**
- `app/pipeline/states.py` — `MeetingState`, `JobStage`, `JobState`, `LEGAL_TRANSITIONS`
- `app/pipeline/queue.py` — `enqueue`, `claim_next`, `complete`, `fail`, `release`, `enqueue_next_stage`
- `app/pipeline/worker.py` — the loop in `TECHNICAL-DESIGN.md` §6.2, with a stage registry
- Test-only stages: `slow`, `flaky`, `boom`, `preemptible`

**Implementation notes**
- `claim_next` is one statement: `UPDATE jobs SET state='running', started_at=? WHERE id = (SELECT id FROM jobs WHERE state='pending' AND (not_before IS NULL OR not_before<=?) ORDER BY priority, id LIMIT 1) RETURNING *`.
- Backoff: `not_before = now + min(2**attempts, 3600)` seconds, jitter ±10 %.
- `should_yield()` returns true when the recorder is active and policy ≠ `asap`; stages call it between units and raise `Preempted`.
- On startup, any job left `running` is reset to `pending` (crash recovery) and logged.

**Tests**

| Test | Assertion |
|---|---|
| `test_claim_is_exclusive` | 8 threads call `claim_next` against 1 job → exactly one non-None result |
| `test_stage_advances_pipeline` | completing `transcribe` enqueues `assemble` and nothing else |
| `test_retry_backoff_grows` | `flaky` fails 3× → `not_before` deltas ≈ 2, 4, 8 s (±jitter); attempt count increments |
| `test_permanent_after_max_attempts` | 5 failures → job `failed`, meeting `FAILED`, `last_error` populated |
| `test_preemption_does_not_count_attempt` | `preemptible` yields → job `pending`, `attempts` unchanged |
| `test_crash_recovery_resets_running` | mark a job `running`, restart worker → job is `pending` again |
| `test_idempotent_stage_skips` | run a stage whose output exists and is newer than its input → returns without doing work (assert a side-effect counter unchanged) |
| `test_delivery_failure_does_not_regress_state` | `deliver` fails → meeting stays `RENDERED`, not `FAILED` |
| `test_policy_gates_execution` | policy `when_idle` + simulated busy → nothing claimed; then idle → claimed |

**Exit criteria:** worker survives a randomized 500-job fuzz (random failures, preemptions, restarts) with no job lost, duplicated, or stuck — assert final states sum to the job count.

---

## Phase 3 — Audio, synthetic only

**Goal:** every byte-level guarantee in `TECHNICAL-DESIGN.md` §4 proven with **no audio hardware**.
**Depends on:** 1.

**Deliverables**
- `app/audio/capture.py` — `AudioCapture` protocol: `start()`, `stop()`, `frames` queue, `stats`
- `app/audio/fake.py` — `SyntheticCapture(pattern)` emitting deterministic 48 kHz float32 stereo: silence, tone, speech-fixture playback, and a `glitch_after(n)` mode
- `app/audio/ring.py` — bounded pre-roll deque with `flush_to(writer)` and `drop()`
- `app/audio/writer.py` — resample → mono → int16 → chunk WAVs → `manifest.jsonl`
- `app/audio/vad.py` — two-stage VAD: cheap energy gate, Silero verification; `voiced_frames(pcm) -> list[bool]`

**Implementation notes**
- Writer chunking: target 60 s; scan for a VAD-silence boundary in ±10 s; hard cut at 70 s if none.
- Durability order is **load-bearing and asserted**: `writeframes` → `flush` → `fsync(wav)` → append manifest line → `fsync(manifest)`.
- On queue-full: increment `dropped`, drop from the ring first; if committed audio would be lost, write `{"gap_ms": n}` on the next manifest line instead of shortening the timeline.

**Tests**

| Test | Assertion |
|---|---|
| `test_resample_sample_count` | 10.0 s of 48 kHz in → 160 000 ±16 samples of 16 kHz out |
| `test_stereo_to_mono_energy` | mono output RMS within 1 % of the channel mean |
| `test_chunk_prefers_silence_boundary` | tone-silence-tone pattern → cut lands in the silence window, not at exactly 60.0 s |
| `test_chunk_hard_cut_on_continuous` | continuous tone → cut at 70 s exactly |
| `test_manifest_durable_after_kill` | write 3 chunks, truncate the manifest mid-line, reopen → recovery reads 2 valid chunks and re-derives the 3rd from its WAV header |
| `test_manifest_fsync_order` | patch `os.fsync` to record call order → asserts wav-fsync precedes manifest append precedes manifest-fsync |
| `test_preroll_flush_is_chunk_one` | 30 s of ring + commit → `0001.wav` is exactly the ring contents, `t0_ms == 0` |
| `test_preroll_drop_leaves_no_files` | discard → the meeting folder does not exist |
| `test_backpressure_drops_ring_not_committed` | starve the writer → `dropped` > 0 while committed chunk durations remain exact |
| `test_gap_marker_written` | force a committed-audio drop → manifest line carries `gap_ms` > 0 and timeline length is preserved |
| `test_vad_on_speech_fixture` *(windows)* | T1 speech → voiced ratio > 0.5; on pure silence → < 0.02 |
| `test_vad_two_stage_agreement` | energy gate never rejects a frame Silero accepts (gate must be the looser stage) |

**Exit criteria:** a 60-minute synthetic soak at 4× speed produces byte-exact expected sample totals and a manifest whose durations sum to the input within 50 ms.

---

## Phase 4 — Audio on real hardware (the spike)

**Goal:** answer `TECHNICAL-DESIGN.md` §19.2 with evidence, and prove capture on the real machine.
**Depends on:** 3. **This phase is the highest-risk item in the project; do it before anything downstream.**

**Deliverables**
- `app/audio/devices.py` — enumerate WASAPI endpoints, resolve default render/capture, find the loopback companion of a render device
- `app/audio/wasapi.py` — `WasapiCapture` implementing `AudioCapture` over PyAudioWPatch, callback-only, bounded queue
- `app/selftest.py: audio` — the T2 loopback echo test
- `docs/spike-report.json` — emitted metrics: xruns, drift, sample totals, latency

**Implementation notes**
- Open at the endpoint's native mix format; never request a format WASAPI must convert.
- Callback body is exactly `q.put_nowait`; anything else is a bug this phase must not introduce.
- Record `t0 = perf_counter_ns()` at first callback per stream; per-track timestamps derive from frame counts alone.
- Device-change recovery: catch stream errors, close the chunk, re-resolve the default, reopen, emit `gap_ms`, continue the same meeting.

**Tests**

| Test | Assertion |
|---|---|
| `test_enumerate_devices` *(audio_hw)* | ≥1 render and ≥1 capture endpoint; each has a sample rate and channel count |
| `test_loopback_echo` *(audio_hw)* | **T2**: play a 20 s T1 speech fixture, capture loopback → cross-correlation ≥ 0.8, offset < 500 ms, samples within 1 %, xruns == 0 |
| `test_loopback_chunks_and_manifest` *(audio_hw)* | the same run through the real writer → 20 s produces the expected chunk count with durations summing to 20 s ±100 ms |
| `test_mic_stream_smoke` *(audio_hw)* | open the default capture device, receive ≥ 1 s of frames, correct dtype and rate. **No content assertion** — silence is a pass |
| `test_dual_stream_concurrent` *(audio_hw)* | both streams open simultaneously for 60 s → both produce continuous frames, neither starves, xruns == 0 |
| `test_device_change_recovery` | inject a stream error via `SyntheticCapture.glitch_after(n)` wired through the real writer → meeting continues, `gap_ms` recorded, no state regression |
| `test_soak_two_hours` *(audio_hw, slow)* | 2 h dual-stream run → report **clock drift** between tracks; fails only if drift > 1 s/hour or any xrun occurs |

**Exit criteria:** `selftest audio` green on the target machine and `spike-report.json` written.
**Stop condition:** if `test_dual_stream_concurrent` fails, do **not** proceed — descend the
fallback ladder in `TECHNICAL-DESIGN.md` §4.0 (SoundCard → sounddevice → vendored C++
loopback helper) and re-run this phase. Only `app/audio/wasapi.py` changes; everything
above the `AudioCapture` protocol is unaffected, which is the point of the protocol.

---

## Phase 5 — ASR backends

**Goal:** audio in, timestamped segments out, on GPU and CPU, with a fake that makes every
later phase testable in milliseconds.
**Depends on:** 3 (4 not required — this phase consumes WAV files).

**Deliverables**
- `app/asr/backend.py` — `Segment`, `Word` dataclasses and the `AsrBackend` protocol from `TECHNICAL-DESIGN.md` §7
- `app/asr/fake.py` — deterministic segments derived from the input filename; supports an injected "repetition loop" mode for testing the guard
- `app/asr/local.py` — faster-whisper backend
- `app/asr/models.py` — model resolution and download
- `app/asr/remote.py` — HTTP client + health check + automatic local fallback

**Implementation spec for `local.py`** (build from this description; do not copy from
another project):

1. **Device probe.** Try to locate a CUDA runtime: check for `cublas*` under (a) the app home's `cuda/nvidia/*/bin` (Windows) or `lib` (Linux), (b) system CUDA paths, (c) `nvidia-*-cu12` wheel directories on `sys.path`. If none found, set `CUDA_VISIBLE_DEVICES=""` and return `("cpu","int8")`.
2. **Windows DLL registration.** When CUDA libs are found on Windows, both `os.add_dll_directory(d)` **and** prepend `d` to `os.environ["PATH"]` for each library directory. CTranslate2 loads cuBLAS lazily via a plain `LoadLibrary` at the first matrix multiply, which does *not* search `add_dll_directory` paths — without the PATH prepend the model constructs successfully and then fails on the first inference. On Linux, extend `LD_LIBRARY_PATH` instead.
3. **Compute type.** Query `ctranslate2.get_supported_compute_types("cuda")`; prefer `float16`, then `int8_float32`, then `int8`. On Pascal-class GPUs `int8` is the correct choice; make it configurable.
4. **Warmup.** Immediately after construction, transcribe 0.5 s of generated silence. This forces the lazy GPU library load *now*, so failures surface at startup rather than mid-meeting.
5. **CPU fallback.** Wrap load and warmup; if the exception text matches any of `cuda, cublas, cudnn, cudart, nvrtc, nvidia, gpu, no kernel image`, log it and rebuild the model on CPU/int8. Never let a GPU problem fail a transcription.
6. **Transcribe parameters:** `language="he"` (configurable), `vad_filter=True`, `word_timestamps=True`, **`condition_on_previous_text=False`**, `beam_size=5`, `initial_prompt` from the caller.
7. **`unload()`** must drop the model and force a GC so the LLM stage can have the memory (the sequential-stages rule in `DESIGN.md` §20.4).

**Model resolution order:** configured `model_path` → app-home `model/` → download from the
configured repo id. Profile picks the repo: GPU → `whisper-large-v3-ct2`, CPU →
`whisper-large-v3-turbo-ct2`.

**Tests**

| Test | Assertion |
|---|---|
| `test_fake_backend_deterministic` | same input → byte-identical segment list across runs |
| `test_segments_monotonic` | for any backend, `start` non-decreasing, `end > start`, words inside their segment bounds |
| `test_device_probe_no_cuda` | with a patched empty filesystem → returns `("cpu","int8")` and sets `CUDA_VISIBLE_DEVICES=""` |
| `test_cuda_error_falls_back` | patch model construction to raise `RuntimeError("cublas64_12.dll not found")` → a CPU model is constructed and transcription succeeds |
| `test_windows_dll_dirs_registered` *(windows)* | with a fake cuda dir present, assert each lib dir appears in `PATH` **and** was passed to `add_dll_directory` |
| `test_unload_frees_memory` | RSS after `unload()` + GC drops by > 50 % of the model's on-disk size |
| `test_transcribe_speech_fixture` *(windows, slow)* | **T1**: synthesize a known English sentence → transcribe with `language="en"` → ≥ 70 % of expected words present (tolerant of ASR error, strict enough to catch a broken pipeline) |
| `test_no_repetition_loop` *(slow)* | 3 minutes of silence + one utterance → no segment repeated more than twice consecutively (guards the `condition_on_previous_text` regression) |
| `test_initial_prompt_passed` | glossary terms appear in the `initial_prompt` argument reaching the backend, truncated to the token cap |
| `test_remote_falls_back_when_down` | `remote.py` pointed at a closed port → falls back to local, emits a warning, result is still produced |
| `test_language_detect_prefers_them_track` | `them` has speech, `me` is silent → detection runs on `them` only (assert via a call counter) |
| `test_language_detect_falls_back_to_me` | `them` silent, `me` has speech → detection runs on `me` |
| `test_language_pinned_after_first_chunk` | detection returns `en` on chunk 1 → chunks 2..N are transcribed with `language="en"` and **no further detection calls** |
| `test_low_confidence_uses_default` | detector returns `("en", 0.4)` → meeting language is the configured default `he`, `language_conf` recorded, `NEEDS_REVIEW` reason added |
| `test_fixed_mode_skips_detection` | `language_mode="fixed"` → zero detection calls, configured language used |
| `test_detect_english_fixture` *(windows, slow)* | **T1**: an English speech fixture → detected `en` with confidence ≥ 0.6. **Record the confidence in the selftest report** — this closes `TECHNICAL-DESIGN.md` §19.6 and sets the bar for whether one chunk is enough |

**Exit criteria:** `selftest pipeline` runs end-to-end on `FakeAsr` in < 2 s; the real
backend transcribes a T1 fixture on the target machine.

---

## Phase 6 — Assembly and transcript artifacts

**Goal:** two independent segment lists become one speaker-tagged timeline.
**Depends on:** 5.

**Deliverables**
- `app/pipeline/stages/assemble.py`
- `transcript.json` and `transcript.md` writers
- FTS indexing of every turn

**Implementation notes** (algorithm exactly as `TECHNICAL-DESIGN.md` §8)
- Tag by track, stable-sort by `start`.
- **Echo suppression:** for each `ME` segment, find `THEM` segments overlapping in time with `rapidfuzz.ratio ≥ 85`; drop the `THEM` copy and count it. Expose the count as `echo_suppressed` in `meta.json`.
- Coalesce same-speaker turns separated by < 2 s.
- Emit `**[mm:ss] SPEAKER:** text` lines; index each turn into `transcripts_fts`.

**Tests**

| Test | Assertion |
|---|---|
| `test_merge_orders_by_start` | interleaved inputs → output strictly ordered; ties broken deterministically by track |
| `test_echo_suppressed` | inject the same sentence on both tracks 200 ms apart → one turn survives, tagged `ME`, `echo_suppressed == 1` |
| `test_echo_not_oversuppressed` | genuine agreement ("כן, בדיוק" on both sides) with similarity < 85 → both survive |
| `test_coalesce_adjacent_turns` | three `ME` segments 1 s apart → one turn; 3 s apart → three turns |
| `test_overlap_preserved` | truly simultaneous speech → two adjacent turns, timestamps overlapping, neither dropped |
| `test_transcript_md_golden` | golden-file comparison including a Hebrew line with an embedded English product name |
| `test_fts_roundtrip` | index a transcript, search a Hebrew word → correct meeting id and snippet offset |
| `test_assemble_idempotent` | running twice produces identical bytes and no duplicate FTS rows |

**Exit criteria:** golden files stable; `selftest pipeline` now produces `transcript.md`.

---

## Phase 6b — Enrichment seams (no integrations)

**Goal:** make the meeting record and the pipeline ready for optional enrichment sources —
calendar today, anything else later — **without implementing any of them**. Small phase;
its whole purpose is that adding an integration later touches one file.

**Depends on:** 2, 6.

**Deliverables**
- `app/enrich/source.py` — the protocol:
  ```python
  class EnrichmentSource(Protocol):
      name: str
      def for_meeting(self, started_at: datetime, ended_at: datetime | None) -> Enrichment | None: ...
  @dataclass(frozen=True)
  class Enrichment:
      title: str | None = None
      participants: tuple[str, ...] = ()
      agenda: str | None = None
      recipients: tuple[str, ...] = ()
      raw: dict | None = None        # stored verbatim in meetings.calendar_json
  ```
- `app/enrich/null.py` — `NullSource`, returning `None`. **This is the wired-in default and the only source that ships.**
- `app/enrich/fake.py` — a test source returning a fixed `Enrichment`, used to prove the seam.
- Wiring: the recorder asks the configured source once at commit; whatever comes back fills `title`/`participants`/`agenda`/`recipients` **only where they are still empty**, and `raw` lands in `meetings.calendar_json`. `initial_prompt` already accepts a participant list and takes `()` today.

**Implementation notes**
- Enrichment is **advisory and never blocking**: it runs with a 2 s timeout inside a try/except, and any failure is logged and ignored. A meeting must never fail, stall, or wait because an enrichment source is slow or absent.
- The UI's timeline takes an optional `future` list which is empty today; no component branches on "is calendar connected".
- No Google package is imported, and no OAuth code exists in this build.

**Tests**

| Test | Assertion |
|---|---|
| `test_null_source_is_default` | fresh config → the wired source is `NullSource`; a committed meeting has `calendar_json IS NULL` and still reaches `RENDERED` |
| `test_fake_source_fills_empty_fields` | `FakeSource` returning a title and two participants → both land on the meeting, `raw` stored verbatim |
| `test_enrichment_never_overwrites` | a meeting that already has a user-set title → enrichment does **not** replace it |
| `test_enrichment_timeout_ignored` | a source sleeping 5 s → commit proceeds within 2.5 s, meeting unaffected, one warning logged |
| `test_enrichment_exception_ignored` | a source raising → meeting still commits and completes |
| `test_participants_reach_initial_prompt` | with `FakeSource` participants present, those names appear in the ASR `initial_prompt`; with `NullSource`, the prompt contains only glossary terms |
| `test_no_google_imports` | static scan of `app/` → zero imports of `google*`/`googleapiclient` in this build |

**Exit criteria:** the whole pipeline runs on `NullSource`; the seam is proven by
`FakeSource`; `test_no_google_imports` passes.

---

## Phase 7 — LLM and summarization

**Goal:** transcript → schema-valid `notes.json`, with map-reduce, caching, and a local fallback.
**Depends on:** 6.

**Deliverables**
- `app/llm/client.py` — `LlmClient` protocol; `AnthropicClient`, `OllamaClient`, `FakeLlm`
- `app/llm/schema.py` — the JSON Schema from `TECHNICAL-DESIGN.md` §9.3
- `app/llm/prompts/{system,map,reduce}.md` with `version:` front-matter
- `app/pipeline/stages/summarize.py` — windowing, map, reduce, sanity gates
- `app/llm/tokens.py` — token counting with a per-meeting cache

**Implementation notes**
- **Window by real tokens, never characters.** Binary-search window boundaries using `count_tokens`, cache results keyed by text hash. Target 6 000 tokens, 300 overlap.
- Anthropic call exactly as `TECHNICAL-DESIGN.md` §9.2: `messages.parse()` with the schema, `model` and `effort` from config, adaptive thinking, `cache_control` on the system and glossary blocks, server-side fallbacks enabled.
- **Always check `stop_reason` before reading content**; `refusal` is a `PermanentError` carrying the category into `meta.json` so the UI can explain it.
- Ollama path: same protocol, `format: json`, plus a validate-and-repair loop (up to 2 retries feeding the validation error back) because local models honour schemas less reliably.
- Sanity gates → `NEEDS_REVIEW`, never silent acceptance: zero `action_items` on a > 20 min meeting; `tldr` < 2 items; any `action_items[].who` not in the participant set.

**Tests**

| Test | Assertion |
|---|---|
| `test_schema_rejects_malformed` | 6 crafted bad payloads (missing required, wrong type, over-length title, extra property) each raise `ValidationError` |
| `test_window_split_by_tokens` | with a mock tokenizer where 1 char == 1 token, a 20 000-char transcript → 4 windows with 300-token overlaps and no lost text (concatenate and compare against source minus overlaps) |
| `test_window_hebrew_ratio` *(live_api)* | count tokens of a real Hebrew transcript; **record tokens-per-word** in the selftest report and assert it is < 5.0 — this both closes `TECHNICAL-DESIGN.md` §19.4 and becomes the regression bound |
| `test_cache_control_placement` | inspect the constructed request: `cache_control` appears on system blocks only, and the volatile transcript is after the last breakpoint |
| `test_cache_hit_on_second_window` *(live_api)* | window 2 reports `usage.cache_read_input_tokens > 0`; zero is a failure, since it means a volatile value leaked into the prefix |
| `test_refusal_is_permanent` | fake a `stop_reason="refusal"` → job fails permanently with the category recorded, no infinite retry |
| `test_map_reduce_merges` | two windows each yielding one decision → reduce output contains both, de-duplicated |
| `test_sanity_gate_flags_review` | a 40-minute meeting whose notes have no action items → meeting state `NEEDS_REVIEW`, artifacts still written |
| `test_ollama_repair_loop` | `FakeOllama` returns invalid JSON once then valid → succeeds in 2 attempts; invalid 3× → `PermanentError` |
| `test_prompt_version_recorded` | `meta.json.prompt_versions` matches the front-matter of the files actually used |
| `test_live_smoke` *(live_api)* | one real call on a 2-minute fixture → schema-valid, `title` non-empty, `stop_reason == "end_turn"` |
| `test_summary_language_resolution` | `summary.language` of `en`/`he`/`auto` → resolved value is `en`/`he`/`meetings.language` respectively, and is stored on the meeting |
| `test_output_language_in_prompt` | the constructed request contains an explicit target-language instruction matching the resolved value |
| `test_cross_language_summary` *(live_api)* | a Hebrew transcript with `summary.language="en"` → `notes.tldr` is English (assert < 10 % Hebrew codepoints), and Hebrew proper nouns survive verbatim in `participants[].name` |

**Exit criteria:** `selftest pipeline` produces `notes.json` with `FakeLlm`;
`selftest live-llm` green when a key is present.

---

## Phase 8 — Rendering and delivery

**Goal:** `notes.json` → two HTML variants → an email that provably sends.
**Depends on:** 7.

**Deliverables**
- `templates/summary.html.j2`, `templates/_email.css`, `app/pipeline/stages/render.py`
- `app/mail.py` and `app/pipeline/stages/deliver.py`

**Implementation notes**
- One template, two passes: UI variant keeps a `<style>` block; email variant runs through `premailer` to inline every rule.
- RTL: `<html dir="rtl" lang="he">`, logical CSS properties, `unicode-bidi: plaintext` on quotes, system font stack, no webfonts, no external URLs of any kind.
- Quote and decision elements carry `data-at-ms` so the UI can seek from the summary into the audio.
- `deliver.py` default mode is `draft`: render, store, notify — no send. `auto_send` opt-in.

**Tests**

| Test | Assertion |
|---|---|
| `test_render_golden_ui` | golden HTML for a fixture `notes.json` |
| `test_render_golden_email` | golden HTML for the email variant |
| `test_email_has_no_style_block` | parse the email variant: zero `<style>` elements, and every visible element carries a `style` attribute |
| `test_no_external_resources` | regex the output for `http://`, `https://`, `//` in `src`/`href` → zero matches (proves offline-safe rendering) |
| `test_rtl_attributes` | Hebrew summary → `dir="rtl"` and `lang="he"` on `<html>`; a line mixing Hebrew and an English product name sits inside a `unicode-bidi: plaintext` container |
| `test_ltr_attributes` | English summary → `dir="ltr"` and `lang="en"`; **golden files exist for both directions** |
| `test_direction_follows_summary_not_meeting` | Hebrew meeting + `summary.language="en"` → the rendered document is LTR |
| `test_no_physical_css_properties` | grep the emitted CSS for `padding-left`, `margin-right`, `text-align: left|right` → zero matches outside a documented allowlist |
| `test_at_ms_present` | every quote and decision with a timestamp emits `data-at-ms` |
| `test_rerender_without_llm` | delete `summary.html`, re-run `render` → identical bytes, and assert the LLM client was never constructed |
| `test_smtp_send_to_fake_server` | **FakeSmtp**: an `aiosmtpd` controller on a random loopback port; assert the received message has both `text/plain` and `text/html` parts, correct subject and recipients |
| `test_smtp_failure_is_retryable` | server refuses → job retries with backoff; meeting remains `RENDERED` |
| `test_draft_mode_does_not_send` | default config → FakeSmtp receives zero messages, meeting reaches `RENDERED` and is marked deliverable |

**Exit criteria:** `selftest pipeline` produces `summary.html` end to end from a fixture WAV.
**This is milestone M0.**

---

## Phase 9 — HTTP API

**Goal:** every route in `TECHNICAL-DESIGN.md` §12, with the security model enforced and tested.
**Depends on:** 2, 8.

**Deliverables**
- `app/main.py` FastAPI app, `app/api/` routers, SSE event bus
- Host-header middleware, cookie auth, CSRF, ranged audio

**Implementation notes**
- Middleware order matters and is asserted: **Host check → auth → CSRF → routing**.
- One-time token issued by the tray, exchanged for `HttpOnly; SameSite=Strict; Max-Age=1y` cookie.
- Audio route supports `Range` and returns 206 with correct `Content-Range`.
- SSE (`/api/events`) publishes recorder state, job transitions, and toasts; heartbeat every 15 s.

**Tests**

| Test | Assertion |
|---|---|
| `test_host_header_rejected` | `Host: evil.com` → **421**, and the route handler is never entered (assert via a counter) |
| `test_host_header_allowed` | `localhost:8000` and `127.0.0.1:8000` → 200 |
| `test_requires_cookie` | no cookie → 401 with the "open from the tray" message; never a redirect that could leak |
| `test_token_exchange_once` | one-time token works once, second use → 401 |
| `test_csrf_required_on_mutations` | POST/PATCH without the CSRF header → 403; with it → 200 |
| `test_no_cors_headers` | no `Access-Control-Allow-Origin` on any response |
| `test_bind_address` | the server socket is bound to `127.0.0.1`, asserted from `server.servers[0].sockets[0].getsockname()` |
| `test_range_request_audio` | `Range: bytes=0-999` → 206, `Content-Range` correct, body length 1000 |
| `test_meetings_filtering` | seeded DB → date range, state and `q` filters each return the expected ids |
| `test_sse_emits_state_change` | subscribe, trigger a job transition → event received within 2 s with the expected payload |
| `test_openapi_snapshot` | the generated OpenAPI document matches a golden file — catches accidental route or schema changes |
| `test_retry_endpoint_reenqueues` | POST retry on a failed stage → job `pending`, attempts reset |

**Exit criteria:** `pytest tests/integration/api` green; `selftest all` starts the server on
an ephemeral port and hits `/api/status`.

---

## Phase 10 — Frontend

**Goal:** the timeline, meeting page, and settings, verified headlessly.
**Depends on:** 9.

**Deliverables**
- `frontend/` — Vite + React + TS + TanStack Query + Tailwind, routes per `TECHNICAL-DESIGN.md` §13
- `wavesurfer.js` player wired to the ranged audio endpoint
- Vitest unit tests; **Playwright** e2e against the real backend with a seeded DB

**Implementation notes**
- `dir="rtl"` on the document root; Tailwind logical properties (`ps-`/`pe-`/`ms-`/`me-`) only — a lint rule bans `pl-`/`pr-`/`ml-`/`mr-` so mirroring never regresses.
- Every element a test needs carries `data-testid`; tests never select by CSS class or visible text (text is Hebrew and will change).
- `npm run build` output is served by FastAPI; Playwright runs against the built bundle, not the dev server, so the tested artifact is the shipped one.
- A `POST /api/test/seed` route exists **only** when `UP_TEST_MODE=1`, and its absence in normal mode is itself asserted.

**Tests**

| Test | Assertion |
|---|---|
| `unit: formatDuration` | 47 min, 3 h 2 min, and < 1 min render correctly |
| `unit: timelineLayout` | overlapping meetings on the same day get non-overlapping grid columns |
| `e2e: timeline_renders_seeded` | seed 5 meetings across 3 days → 5 cards, correct day grouping, newest first |
| `e2e: recording_card_live` | seed a `RECORDING` meeting → a red card with a running elapsed timer and a Stop button |
| `e2e: queue_card_shows_eta` | seed a `TRANSCRIBING` job → card shows a state label and a non-empty ETA |
| `e2e: meeting_page_loads` | open `/m/:id` → summary HTML present, transcript turns rendered, speaker labels visible |
| `e2e: click_transcript_seeks` | click a turn → an audio range request is issued with the expected byte offset (intercept the network call) |
| `unit: no_hardcoded_strings` | static scan of `frontend/src` for user-visible string literals in JSX → zero outside the message catalogues |
| `unit: catalogue_parity` | the `he` catalogue has exactly the same key set as `en` — a missing key fails the build |
| `e2e: default_locale_is_english` | fresh profile → UI renders English, `document.dir === "ltr"` |
| `e2e: locale_switch_no_reload` | switch to Hebrew in Settings → `document.dir` becomes `rtl`, labels change, **no page reload** (assert `performance.navigation` unchanged) |
| `e2e: rtl_direction` | with the Hebrew locale, a mixed Hebrew/English line's bounding boxes are ordered right-to-left |
| `e2e: content_dir_independent_of_chrome` | English UI + Hebrew meeting → chrome is LTR while the transcript block carries `dir="rtl"` and renders right-aligned |
| `e2e: data_root_picker` | Settings shows the current data root; setting a path containing `OneDrive` surfaces the sync warning inline |
| `e2e: attention_lists_failures` | seed a `FAILED` meeting → it appears on `/attention` with a Retry button that calls the retry endpoint |
| `e2e: settings_roundtrip` | change a setting, reload → persisted; assert no secret value is ever present in the DOM |
| `e2e: test_seed_route_absent` | with `UP_TEST_MODE` unset → `POST /api/test/seed` returns 404 |

**Exit criteria:** `npx playwright test` green headless in CI; screenshots archived on failure.

---

## Phase 11 — Tray, notifications, single instance

**Goal:** the app runs as a background tray process and speaks to the user, tested without a desktop.
**Depends on:** 9.

**Deliverables**
- `app/tray.py` — icon lifecycle and menu
- `app/tray_state.py` — **a pure function** `(AppState) -> IconSpec` (colour, tooltip, enabled menu items)
- `app/notify.py` — `Notifier` protocol, `WindowsToastNotifier`, `FakeNotifier`
- Named-mutex single-instance guard

**Implementation notes**
- The icon is dumb: all decisions live in `tray_state.py`, which imports nothing from pystray. That is what makes this phase testable at all.
- Toasts carry buttons whose activation posts to the local API, so the callback path is the same one the UI uses.
- Register the AppUserModelID at startup (needed for toasts to show the app name and persist in Action Center).

**Tests**

| Test | Assertion |
|---|---|
| `test_icon_state_table` | parametrized over all app states → expected colour and tooltip: idle grey, armed amber, recording red, processing blue, error badge |
| `test_menu_items_enabled` | Stop disabled when not recording; Pause disabled when idle; "Don't detect for 1 hour" always present |
| `test_notifications_fire_once` | a recording-started event emits exactly one toast; a duplicate event within the debounce window emits none |
| `test_toast_buttons_present` | the started toast carries `Stop` and `Not a meeting`; summary-ready carries `Open` and `Email` |
| `test_toast_button_posts_to_api` | activating `Not a meeting` on `FakeNotifier` → the corresponding API call is made and the meeting is discarded |
| `test_single_instance` | spawn the entry point twice → second exits with code 3 and logs "already running"; first is unaffected |
| `test_tray_survives_worker_crash` | kill the worker thread → icon reflects an error state, recorder unaffected, app still running |

**Exit criteria:** all green headlessly with `FakeNotifier`. One manual smoke (visual toast)
is recorded in `docs/manual-checks.md` but gates nothing.

---

## Phase 12 — Detection

**Goal:** implement `DETECTION.md` and prove the scoring, the state machine, and the real
Windows signal path — without a human joining a meeting.
**Depends on:** 4, 11.

**Deliverables**
- `app/detect/registry.py`, `sessions.py`, `windows.py`, `evidence.py`, `detector.py`
- `app/selftest.py: detect-shadow`
- Detector event log + `/detector` UI page wiring

**Implementation notes**
- `evidence.py` is **pure**: `score(evidence: list[Evidence], weights: dict) -> int`. No Windows imports. This is what makes the ruleset testable as a table.
- Signal sources sit behind protocols (`MicSource`, `SessionSource`, `TitleSource`, `VadSource`) with fake implementations, so `detector.py`'s four-tier state machine is driven entirely by injected signals and a `FakeClock`.
- Registry watch: `RegNotifyChangeKeyValue` with `bWatchSubtree=True` on the microphone ConsentStore key; **re-arm after every fire** (it is one-shot); wait with a timeout so the thread observes the stop flag. Parse `NonPackaged` subkeys (exe path with `\` → `#`) and package-family subkeys; `LastUsedTimeStop == 0` means in use now.
- Shadow mode is the install default: run everything including the pre-roll, write `detector_events` with `outcome="shadow"`, commit nothing.

**Tests**

| Test | Assertion |
|---|---|
| `test_scoring_table` | every worked example in `DETECTION.md` §5 as a parametrized case → exact expected score and commit/ignore verdict (Zoom-talking 9 ✅, listen-only all-hands 7 ✅, YouTube 2 ❌, dictation 3 ❌, voice note 3 ❌) |
| `test_ignore_list_dominates` | an ignored process with otherwise-strong evidence → score < threshold |
| `test_sustain_required` | score crosses threshold for 9 s then drops → no commit; 10 s → commit |
| `test_tier1_opens_streams_no_disk` | fake mic-acquire → capture started, ring filling, **zero files on disk** |
| `test_discard_leaves_nothing` | evidence decays → ring dropped, no meeting row, no folder, one `near_miss` event |
| `test_commit_flushes_preroll` | commit after 18 s → `0001.wav` contains the full pre-roll and `t0_ms == 0` |
| `test_90s_timeout_gives_up` | mic held with no other evidence → returns to idle at 90 s, logs a near miss |
| `test_end_on_mic_release_with_grace` | release then re-acquire at 45 s → one meeting; re-acquire at 75 s → two meetings |
| `test_end_on_dual_silence` | mic held, both tracks silent 5 min → meeting ends, trailing silence trimmed |
| `test_shadow_mode_commits_nothing` | full positive evidence in shadow mode → `detector_events` row with `outcome="shadow"`, zero meetings |
| `test_registry_sees_self` *(audio_hw, windows)* | **T3**: open a real capture stream from the test process → `registry.current_holders()` includes this process's executable; close it → holder disappears within 2 s |
| `test_registry_rearm` *(windows)* | trigger three consecutive ConsentStore changes → three wakes (proves the one-shot notification is re-armed) |
| `test_window_titles_enumerated` *(windows)* | create a window with a known title → `TitleSource` returns it |
| `test_evidence_recorded_on_meeting` | committed meeting's `evidence_json` deserializes to the exact contributing list, renderable as the "Recorded because…" string |

**Exit criteria:** `selftest detect-shadow` runs 60 s against real signals and writes a
report. **This is milestone M2's core**; ship detection in shadow mode first, and only flip
`detection.mode` to `on` after reviewing a week of `detector_events`.

---

## Phase 13 — Packaging and first run

**Goal:** a frozen executable that passes its own self-test.
**Depends on:** all.

**Deliverables**
- `packaging/upshot.spec` (PyInstaller one-dir), `packaging/installer.iss` (Inno, per-user, no admin), `packaging/build.ps1`
- `app/bootstrap.py` — first-run sequence from `TECHNICAL-DESIGN.md` §17

**Implementation notes**
- Declare hidden imports explicitly: `ctranslate2`, `onnxruntime` (+ its `capi` DLLs), `comtypes`-generated interfaces (ship the generated modules or force early binding at build time), `keyring.backends.Windows`, `pystray._win32`.
- Bundle `ffmpeg.exe` and the templates/prompts as data; resolve every asset through `app.paths.resource()`.
- Bootstrap: probe CUDA → pick profile and model → reuse an existing local model directory if `model_path` is configured → create app home and run migrations → register AppUserModelID and the Task Scheduler logon task → open the setup UI.
- Setup UI's final step is a **5-second two-track test recording with live level meters** — the only pre-flight that proves capture works before a real meeting depends on it.

**Tests**

| Test | Assertion |
|---|---|
| `test_frozen_imports_all` | run the frozen exe with `--selftest imports`; it imports every `app.*` module inside the freeze. This is the classic PyInstaller failure and must be caught by the build, not by a user |
| `test_frozen_selftest_pipeline` | frozen exe runs `selftest pipeline` on a fixture → produces `summary.html`, exit 0 |
| `test_frozen_resources_present` | templates, prompts, `ffmpeg.exe` all resolve inside the freeze |
| `test_installer_builds` | `build.ps1` produces `Setup.exe` and its size is within ±25 % of the recorded baseline (a sudden jump means something large was accidentally bundled) |
| `test_bootstrap_idempotent` | run bootstrap twice on a fresh `UP_HOME` → same result, no duplicate scheduled task, no second migration |
| `test_bootstrap_offline` | with the network blocked and no model present → fails with a clear actionable error, does not hang |
| `test_task_scheduler_registration` *(windows)* | after bootstrap, `schtasks /query` finds the logon task; uninstall removes it |

**Exit criteria:** the frozen build passes `selftest all` on a clean Windows user profile.

---

## Phase 14 — Milestone acceptance

Each gate is a single automated command producing a JSON report. Nothing ships until its
gate is green.

### M0 — the pipeline (Phases 0–3, 5–8)
```
python -m app.selftest pipeline --input tests/fixtures/meeting_10min.wav --report m0.json
```
**Asserts:** `transcript.json`, `transcript.md`, `notes.json` (schema-valid), `summary.html`,
`summary.email.html` all produced; meeting reaches `RENDERED`; total wall time under a
recorded budget; zero errors in `pipeline.log`.

### M1 — capture (Phase 4, 9–11)
```
python -m app.selftest capture-e2e --seconds 120 --report m1.json
```
**Asserts:** starts a recording via the API, plays a T1 **English** speech fixture out the
render endpoint for 120 s (captured on the loopback track), stops via the API, and the full
pipeline runs to `RENDERED`. Language detection resolves `en` from chunk 1 and pins it. Chunk durations sum to 120 s ±0.5 s; the transcript contains
the expected fixture words; the tray state sequence was `idle → recording → processing →
idle`; both `me` and `them` tracks exist and `them` correlates with the source.

*This is the whole product proving itself with no human in the loop.*

### M2 — detection (Phase 12)
```
python -m app.selftest detect-e2e --report m2.json
```
**Asserts:** with fake signal sources driving a real detector and a real recorder, a
simulated meeting is detected, committed, recorded, and processed to `RENDERED`; a
simulated YouTube pattern produces a `near_miss` and no meeting; shadow mode commits
nothing. Then `detect-shadow` runs 60 s against real Windows signals without error.

### Post-V1 — not in scope

Calendar integration, remote-worker mode, diarization, and the clickable
summary-link-in-calendar feature are out of scope for this build. Their seams exist
(Phase 6b) and their designs are recorded, but no phase implements or gates on them.
Whoever picks up calendar later starts with the Workspace consent question in
`SECURITY-AND-AUTH.md` §6 — which has no bearing on anything in V1.

---

## 15. Risk register and stop conditions

| Risk | Detected by | Stop condition / response |
|---|---|---|
| PyAudioWPatch cannot hold two streams | Phase 4 `test_dual_stream_concurrent` | **Halt downstream work.** Descend the `TECHNICAL-DESIGN.md` §4.0 fallback ladder; only `wasapi.py` changes |
| Clock drift > 1 s/hour | Phase 4 soak | Implement drift correction by resampling `them` against reported stream time; revisit §4.3 |
| Hebrew tokens-per-word > 5 | Phase 7 `test_window_hebrew_ratio` | Shrink window target; re-derive the cost estimate before any pricing claim |
| Language misdetected on a short opening | Phase 5 `test_detect_english_fixture` confidence metric | Extend detection to the first two chunks before pinning; if still weak, expose a per-meeting language override in the UI and default to `he` |
| User points the data root at a synced folder | Phase 1 `test_synced_folder_warning` | Warn, don't block — it's their machine — but make the consequence explicit in the UI copy |
| No FTS5 in the bundled SQLite | Phase 1 probe | `LIKE` fallback ships; note the search-quality regression |
| Whisper repetition loops on long audio | Phase 5 `test_no_repetition_loop` | Already mitigated by `condition_on_previous_text=False`; if it recurs, cap segment repeats in post-processing |
| PyInstaller misses a hidden import | Phase 13 `test_frozen_imports_all` | Add to hiddenimports; never ship a build that skipped this test |
| Detector false positives | Shadow-mode review before enabling | Keep `detection.mode="shadow"` until a week of `detector_events` reads clean |

---

## 16. Rules for the implementing agent

1. **Never skip a phase's exit criteria.** A phase is done when its command is green, not when the code looks finished.
2. **Write the test in the same change as the code.** A phase's test table is its definition of done, not a follow-up.
3. **Fakes are selected by config, never by monkeypatching inside a test body.** If a test needs to patch internals to pass, the seam is in the wrong place — move it.
4. **Do not weaken an assertion to make it pass.** If an assertion is wrong, say so explicitly, explain why, and change it deliberately — with the reasoning recorded in the commit message.
5. **Golden files are regenerated only with `--update-goldens`**, and the diff is reviewed in that change.
6. **Report measurements, don't assume them.** Phases 4 and 7 exist to produce numbers that other decisions depend on; write them into the selftest report.
7. **If a phase's exit criteria cannot be met**, stop and report which assertion failed and what it implies — do not proceed to the next phase on the assumption it will be fixed later.
8. **Every commit leaves `selftest all` green** in the current environment (skips are acceptable; failures are not).
