# Upshot — Technical Design

Implementation-level specification. Assumes `DESIGN.md` (architecture), `DETECTION.md`
(detection mechanism), `STACK.md` (dependencies), `SECURITY-AND-AUTH.md` (auth model).

Target: Windows 11, Python 3.13, single user-session process.

> **Amended since implementation.** Audio is no longer stored as per-minute chunk files.
> Each track is a single growing WAV — `audio/me.wav`, `audio/them.wav` — valid and
> playable at every moment, with `manifest.jsonl` recording committed segments as sample
> offsets into it. The durability guarantee is unchanged. See `DECISIONS.md` **D34**;
> **D35** (playback mixes the tracks on read), **D36** (crosstalk detection) and **D37**
> (echo cancellation) also postdate this document. Where this text and `DECISIONS.md` disagree, `DECISIONS.md` is
> what the code does.

**Contents**
1. Process and threading model · 2. Module map · 3. Data model · 4. Audio subsystem ·
5. Detection subsystem · 6. Job queue and pipeline · 7. ASR · 8. Assembly ·
9. Summarization · 10. Rendering · 11. Delivery · 12. HTTP API · 13. Frontend ·
14. Configuration · 15. Errors, logging, diagnostics · 16. Security implementation ·
17. Packaging and first run · 18. Testing · 19. Open questions

---

## 1. Process and threading model

One OS process. Seven threads, with a strict rule: **audio callbacks do nothing but copy
bytes**.

| Thread | Owns | Blocking behaviour |
|---|---|---|
| **main** | pystray icon loop | pystray requires the main thread on Windows. Everything else is spawned from here |
| **http** | uvicorn (`Server.run` in a thread) | serves `127.0.0.1:8000` |
| **detector** | registry watch + evidence scoring | blocks on `RegNotifyChangeKeyValue`; wakes on change |
| **capture-mic**, **capture-loopback** | PyAudio callback threads (one per stream, created by PortAudio) | must return in microseconds — `bytes` → `queue.Queue`, nothing else |
| **writer** | resample, encode, chunk, manifest | drains both audio queues |
| **worker** | the job queue | one job at a time; preemptible |
| **scheduler** | APScheduler | calendar poll, retention sweep, `scheduled` job policy |

**Why the writer is separate from the callbacks:** PortAudio's callback runs on a
high-priority thread with a hard deadline. Any `numpy` work, file I/O, or GIL contention
there produces dropped frames — which is unrecoverable data loss, violating the
"recording is sacred" invariant. The callback does `q.put_nowait(in_data)` and returns.

**Backpressure:** the audio queues are bounded (`maxsize` ≈ 10 s of audio). If the writer
ever falls behind, we log a `WRITER_LAG` event and drop from the *pre-roll* buffer first,
never from a committed recording. If a committed recording would drop frames, we write a
silence-padded gap marker into the manifest rather than silently shortening the timeline.

**Shutdown:** SIGINT/tray-quit sets a stop event; the writer flushes and closes the current
chunk; the worker finishes the current *stage* (not the whole pipeline) and exits; jobs
resume on next launch.

---

## 2. Module map

```
app/
  main.py               composition root: config → db → services → threads
  tray.py               pystray icon, menu, state machine for the icon
  notify.py             windows-toasts wrapper; buttons → callbacks
  config.py             layered config (§14), keyring access
  db/
    schema.sql          DDL (§3.1)
    migrate.py          ordered migrations, schema_version table
    dao.py              typed accessors; no ORM
  audio/
    devices.py          enumerate WASAPI render/capture, resolve defaults
    capture.py          AudioCapture protocol
    wasapi.py           PyAudioWPatch implementation
    ring.py             pre-roll ring buffer
    writer.py           resample → int16 → chunk WAVs → manifest
    vad.py              Silero wrapper; frame-level voiced/unvoiced
  detect/
    registry.py         ConsentStore watch + parse
    sessions.py         pycaw render-session enumeration
    windows.py          EnumWindows titles
    evidence.py         scoring model (DETECTION.md §5)
    detector.py         the four-tier state machine
  pipeline/
    states.py           Meeting/Job state enums + legal transitions
    queue.py            job table operations
    worker.py           drain loop, preemption, retries
    stages/
      transcribe.py  assemble.py  summarize.py  render.py  deliver.py
  asr/
    backend.py          AsrBackend protocol, Segment/Word dataclasses
    local.py            faster-whisper backend (spec: EXECUTION-PLAN.md Phase 5)
    remote.py           POST to another instance
    fake.py             deterministic backend for tests
  llm/
    client.py           Anthropic + Ollama behind one protocol
    prompts/            *.md, versioned
    schema.py           notes.json JSON Schema
  enrich/               source.py protocol + null.py (ships) + fake.py (tests).
                        Calendar/ICS implementations are post-V1 and not in this build.
  mail.py               smtplib
  glossary.py
  paths.py              app home + frozen-resource resolution
```

---

## 3. Data model

### 3.1 SQLite schema

`%LOCALAPPDATA%\upshot\index.db`, `journal_mode=WAL`, `synchronous=NORMAL`.

```sql
CREATE TABLE schema_version (version INTEGER NOT NULL);

CREATE TABLE meetings (
  id              TEXT PRIMARY KEY,          -- '2026-08-28_1400_a1b2c3'
  folder          TEXT NOT NULL,             -- absolute path
  source          TEXT NOT NULL,             -- manual|detected|imported|calendar
  state           TEXT NOT NULL,             -- see states.py
  title           TEXT,
  title_source    TEXT,                      -- window|llm|calendar|user
  language        TEXT,                      -- detected meeting language, e.g. 'he'|'en'
  language_conf   REAL,                      -- detection probability; <0.6 → default used
  summary_language TEXT,                     -- resolved at summarize time
  started_at      TEXT NOT NULL,             -- ISO8601 local w/ offset
  ended_at        TEXT,
  duration_s      INTEGER,
  profile         TEXT NOT NULL,             -- gpu-live|cpu-deferred|remote-worker
  sensitive       INTEGER NOT NULL DEFAULT 0,
  evidence_json   TEXT,                      -- why it was recorded (detected only)
  calendar_json   TEXT,                      -- enrichment payload, verbatim; NULL in V1
  error           TEXT,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);
CREATE INDEX ix_meetings_started ON meetings(started_at DESC);
CREATE INDEX ix_meetings_state   ON meetings(state);

CREATE TABLE jobs (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  meeting_id   TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  stage        TEXT NOT NULL,        -- transcribe|assemble|summarize|render|deliver
  state        TEXT NOT NULL,        -- pending|running|done|failed|cancelled
  attempts     INTEGER NOT NULL DEFAULT 0,
  not_before   TEXT,                 -- backoff / policy gate
  priority     INTEGER NOT NULL DEFAULT 100,
  last_error   TEXT,
  started_at   TEXT, finished_at TEXT,
  created_at   TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(meeting_id, stage)
);
CREATE INDEX ix_jobs_runnable ON jobs(state, not_before, priority);

CREATE TABLE detector_events (          -- observability (DETECTION.md §8)
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  at          TEXT NOT NULL,
  process     TEXT, window_title TEXT,
  peak_score  INTEGER NOT NULL,
  evidence    TEXT NOT NULL,            -- JSON
  outcome     TEXT NOT NULL,            -- committed|near_miss|ignored|shadow
  meeting_id  TEXT
);

CREATE TABLE glossary (
  term TEXT PRIMARY KEY, kind TEXT, aliases TEXT, note TEXT, hits INTEGER DEFAULT 0
);

CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE VIRTUAL TABLE transcripts_fts USING fts5(
  meeting_id UNINDEXED, speaker UNINDEXED, at_ms UNINDEXED, text,
  tokenize = 'unicode61 remove_diacritics 0'      -- Hebrew: keep niqqud distinctions
);
```

`remove_diacritics 0` matters: the default strips Hebrew niqqud and can merge distinct
words. Verify FTS5 exists in the bundled SQLite before relying on it (`STACK.md`).

### 3.2 On-disk artifacts

`<data_root>` defaults to `%LOCALAPPDATA%\upshot\meetings` and is set at first run.
`meetings.folder` stores an **absolute** path, so changing the root later affects only new
meetings — existing ones stay readable with no migration.

```
<data_root>/2026-08-28_1400_a1b2c3/
  meta.json          the meeting record, mirrored from SQLite (disk is recoverable alone)
  audio/
    me/0001.wav …    16 kHz mono s16le
    them/0001.wav …
    manifest.jsonl   one line per closed chunk, appended + fsynced
  transcript.json    segments, both tracks, word timestamps
  transcript.md      speaker-tagged, human-readable
  notes.json         LLM output, schema-validated
  summary.html       UI rendering
  summary.email.html CSS-inlined variant
  pipeline.log
```

**`manifest.jsonl`** — append-only; a torn final line means an unclean shutdown and that
chunk is re-derived from the WAV header:

```json
{"seq":1,"track":"me","file":"me/0001.wav","t0_ms":0,"dur_ms":60000,"samples":960000,"closed":true}
{"seq":1,"track":"them","file":"them/0001.wav","t0_ms":0,"dur_ms":60000,"samples":960000,"closed":true}
{"seq":2,"track":"me","file":"me/0002.wav","t0_ms":60000,"dur_ms":58120,"gap_ms":0,"closed":true}
```

**`transcript.json`**

```json
{
  "version": 1,
  "language": "he",
  "model": {"name":"ivrit-ai/whisper-large-v3-ct2","compute":"int8","device":"cuda"},
  "segments": [
    {"id":0,"track":"them","speaker":"THEM","start":12.30,"end":17.85,
     "text":"...","words":[{"w":"...","s":12.30,"e":12.55,"p":0.98}],
     "avg_logprob":-0.21,"no_speech_prob":0.01}
  ]
}
```

---

## 4. Audio subsystem

### 4.0 What we build vs. what we take (checked 2026-08-28)

We are **not** writing an audio engine. PortAudio+WASAPI is the engine, soxr is the
resampler, Silero is the VAD, ffmpeg does format work, stdlib `wave` encodes. The
question is only how much glue sits between them — so here is the honest ledger.

| Layer | Off the shelf | Ours |
|---|---|---|
| WASAPI binding / loopback | **PyAudioWPatch** | — |
| Resampling | **soxr** | — |
| VAD | **Silero** (+ WebRTC pre-gate) | thin wrapper |
| WAV encoding | stdlib **`wave`** | — |
| Concat, import of arbitrary formats | **ffmpeg** | — |
| ASR | **faster-whisper** | — |
| Two-stream orchestration, pre-roll ring, chunk+manifest durability, device-change recovery | **nothing exists** | ~400 lines |

**The simplification that does not exist: ffmpeg.** It would be ideal to spawn two ffmpeg
processes with `-f segment -segment_time 60` and delete the callback thread, the
resampler, the chunker and the WAV writer outright. **FFmpeg has no native WASAPI loopback
input on Windows** — its only capture device is DirectShow, which enumerates DirectShow
sources, not render-side WASAPI endpoints. Capturing desktop audio through ffmpeg requires
installing a virtual cable (VB-Cable, "Stereo Mix") or a shim DLL that re-exposes loopback
as a DirectShow device. Requiring a driver install is exactly what going Windows-native was
supposed to avoid, so ffmpeg stays a post-processing tool.

**No library does meeting capture for us either.** Surveyed:

| Project | Stars | Verdict |
|---|---|---|
| [RealtimeSTT](https://github.com/KoljaB/RealtimeSTT) | 10.1k, MIT, active | **Mic only.** System audio requires an OS-level loopback device first. It also owns the recording loop, which inverts control against our "write raw to disk, decide later" invariant. **Read it, don't import it** — §4.7 |
| [SoundCard](https://github.com/bastibe/SoundCard) | 763, BSD | Does loopback with a nicer API, but the released version has known Windows bugs (master is recommended). Our **fallback** if the spike fails |
| [python-sounddevice](https://github.com/spatialaudio/python-sounddevice) | 1.3k, MIT | PortAudio; loopback exposure is awkward. Second fallback |
| [audiotee-wasapi](https://github.com/huxinhai/audiotee-wasapi) | 15, no license | A C++ loopback binary piping PCM to stdout. Tiny and unmaintained, but proves the escape hatch works |
| Meetily, anarlog | 30k / 9.2k | **Both wrote their own capture layer** (Rust/cpal). Nobody in this space found something to reuse either |

So the ~400 lines are not reinvention — they are precisely the parts that encode *our*
product invariants (pre-roll before commit, a chunk isn't real until its manifest line is
fsynced, a USB event must not lose a meeting). No library would supply those, because they
are decisions rather than mechanisms.

**Fallback ladder** if the §19.2 spike shows PyAudioWPatch can't hold both streams for
hours: SoundCard (master) → sounddevice/PortAudio → vendor a small C++ loopback helper
piping PCM to stdin, which is what every native-language competitor effectively does.

### 4.1 Streams

Two independent WASAPI streams opened via PyAudioWPatch:

| | Device | Mode |
|---|---|---|
| `me` | default **capture** endpoint | shared, event-driven |
| `them` | default **render** endpoint | shared, **loopback** |

Both opened at the device's native mix format (typically 48 kHz, 2 ch, float32) —
requesting a format WASAPI must convert invites silent failures. Conversion to the storage
format is ours.

### 4.2 Callback → queue → writer

```python
def _cb(in_data, frame_count, time_info, status):
    if status: self.xruns += 1
    try: self.q.put_nowait((time_info['input_buffer_adc_time'], in_data))
    except queue.Full: self.dropped += frame_count
    return (None, pyaudio.paContinue)
```

The writer thread, per track: `bytes → np.frombuffer(float32) → mean(axis=1)` (stereo→mono)
`→ soxr.resample(48000, 16000) → int16 → wave.writeframes`.

### 4.3 Timebase and drift

The two streams run on **different hardware clocks** and will drift — typically tens of
milliseconds per hour, occasionally more.

- Each track timestamps by its own cumulative frame count: `t_ms = frames_written / 16`.
- The two are anchored to a shared `t0` captured at stream start (`time.perf_counter_ns()`).
- Drift is **not** corrected. Merge granularity is the utterance (§8), so sub-second skew is invisible in the output. This is a deliberate simplification; `manifest.jsonl` records each track's independent duration so drift is measurable after the fact.

### 4.4 Chunking

Target 60 s. The writer prefers to cut on a **VAD silence boundary within ±10 s** of the
target; if none exists (continuous speech), it hard-cuts at 70 s. Rationale in `DESIGN.md`
§10 — over-eager segmentation makes Whisper hallucinate, so a hard cut mid-word is the
lesser evil at this scale, and word timestamps let the assembler stitch across it.

Each chunk: write WAV → `flush()` → `os.fsync()` → append manifest line → `fsync` manifest.
A chunk is not considered to exist until its manifest line is durable.

### 4.5 Device change mid-meeting

Unplugging a headset kills the stream. Handling, in order: catch the stream error → close
the current chunk cleanly → re-resolve the default device → reopen → write a
`{"gap_ms": N}` marker → continue **the same meeting**. Losing a meeting to a USB event
is not acceptable; a 300 ms hole is.

### 4.6 Pre-roll ring

`collections.deque` of raw post-resample int16 frames, bounded at `preroll_s` (default 60)
per track: 1.9 MB each. Committing flushes it as chunk `0001`; discarding drops it. It
never touches disk before commit.

### 4.7 What to take from RealtimeSTT

Not as a dependency — as prior art whose hard-won details are worth copying:

- **Two-stage VAD:** a cheap WebRTC energy gate running always, with Silero only verifying candidates. Meaningfully lower idle CPU than running Silero on every frame, which matters when two streams are open speculatively during Tier 1 (`DETECTION.md`).
- Its recorder state machine's handling of speech-start/speech-end hysteresis, which is the same problem as our chunk-boundary selection.
- Its faster-whisper parameter set, which has been tuned against a lot of real audio.

MIT-licensed, so lifting specific logic with attribution is clean.

---

## 5. Detection subsystem

Implements `DETECTION.md`. Notes that only matter at the code level:

**Registry watch.** `win32api.RegNotifyChangeKeyValue(hkey, bWatchSubtree=True,
dwNotifyFilter=REG_NOTIFY_CHANGE_LAST_SET, hEvent, fAsynchronous=True)` on
`HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone`.
Wait on the event with a timeout so the thread can observe the stop flag. On wake,
enumerate: `NonPackaged\<exe path with '\'→'#'>` and package-family subkeys; a QWORD
`LastUsedTimeStop == 0` means in use. **Re-arm the notification after every fire** — it is
one-shot.

**Evidence collection** runs on a 1 s tick while awake; each contributor is a pure function
returning `(weight, reason)` so the vector is self-describing:

```python
@dataclass(frozen=True)
class Evidence:
    weight: int
    code: str          # 'mic.known_app'
    detail: str        # 'Zoom.exe'
```

`evidence_json` on the meeting is the list of these at commit — that is what renders as
*"Recorded because: …"* in the UI.

**Scoring** is a pure function of the evidence list and the config weights, so it is
directly unit-testable against recorded fixtures with no Windows APIs involved.

**Shadow mode** (`detection.mode = "shadow"`) runs everything including the pre-roll but
returns `outcome="shadow"` instead of committing. This is the default for the first run
after install.

---

## 6. Job queue and pipeline

### 6.1 States

```
Meeting: RECORDING → RECORDED → TRANSCRIBING → TRANSCRIBED → SUMMARIZING
       → SUMMARIZED → RENDERED → DELIVERED
         ⊕ FAILED  ⊕ INTERRUPTED  ⊕ DISCARDED  ⊕ NEEDS_REVIEW
```

Transitions are declared as a `frozenset` of legal `(from, to)` pairs and asserted in the
DAO. An illegal transition is a bug, not a runtime condition — it raises.

### 6.2 The worker loop

```
while not stop:
    if recorder.is_active() and policy != 'asap': sleep(5); continue   # meeting wins
    job = queue.claim_next()          # UPDATE ... SET state='running' WHERE id=(SELECT ...)
    if not job: sleep(2); continue
    try:
        stage_fn[job.stage](meeting)
        queue.complete(job); queue.enqueue_next_stage(meeting)
    except Preempted:  queue.release(job)
    except Exception as e:
        queue.fail(job, e, backoff=min(2**attempts, 3600))
```

`claim_next` is a single UPDATE…RETURNING guarded by the WAL — no lock file, no race with a
second instance (which the named mutex already prevents anyway).

**Idempotency:** every stage checks for its own output first and returns immediately if
present and newer than its input. Re-running a completed pipeline is a no-op; re-running
`summarize` after editing a prompt is one DELETE of the job row.

**Preemption:** stages poll `should_yield()` between units of work (per chunk in transcribe,
per window in summarize) and raise `Preempted`, which releases the job without counting an
attempt.

**Retries:** exponential backoff capped at 1 h; 5 attempts then `FAILED` with the error
surfaced in the UI. Delivery failures never block `RENDERED`.

---

## 7. ASR

> **Superseded by D60 (2026-09-25).** Language is no longer detected before
> transcription: the one model, `ivrit-ai/whisper-large-v3-ct2`, is always given Hebrew,
> and the meeting's language is read from the transcript (`app/asr/language.py`). The
> `asr.language_mode`/`asr.default_language` keys below are gone.

```python
class AsrBackend(Protocol):
    name: str
    def transcribe(self, wav: Path, *, language: str = "he",
                   initial_prompt: str | None = None,
                   word_timestamps: bool = True) -> list[Segment]: ...
    def unload(self) -> None: ...
```

- **`local.py`** — CUDA probe, the Windows `add_dll_directory` **+ PATH prepend** fix (CTranslate2 loads cuBLAS lazily at the first matmul via a plain `LoadLibrary`, which does not search `add_dll_directory` paths), warmup inference, CPU fallback. Full build spec in `EXECUTION-PLAN.md` Phase 5. Model selected by profile (`DESIGN.md` §20). VAD filter on. `condition_on_previous_text=False` — it is a major source of Whisper's repetition loops on long meetings.
- **`remote.py`** — POSTs the WAV to another instance's `/api/asr`, same protocol, with a health check and automatic fallback to local.
- **`fake.py`** — returns fixture segments; makes the whole pipeline testable with no GPU and no 3 GB model.

`initial_prompt` is built from: glossary terms (capped ~200 tokens), calendar attendee
names if present, and the previous chunk's trailing sentence.

**Language resolution (once per meeting, then pinned).** Meetings are mostly Hebrew with
some entirely English; per-chunk detection is deliberately *not* used because Whisper's
mid-audio language switching is unreliable and one wrong switch corrupts a whole chunk.

```python
def resolve_language(meeting, first_chunks: dict[str, Path], cfg) -> tuple[str, float]:
    if cfg.language_mode == "fixed":
        return cfg.default_language, 1.0
    for track in ("them", "me"):                 # 'them' usually carries more speech
        wav = first_chunks.get(track)
        if wav and vad.has_speech(wav, min_s=3.0):
            lang, prob = backend.detect_language(wav)
            if prob >= 0.6:
                return lang, prob
            return cfg.default_language, prob    # low confidence → default, flag review
    return cfg.default_language, 0.0
```

The result is written to `meetings.language` and passed as `language=` for every remaining
chunk. `language_conf < 0.6` adds a `NEEDS_REVIEW` reason rather than silently guessing.

**Model lifecycle:** loaded lazily on first use, `unload()` before the LLM stage on
CPU/GPU-constrained profiles — the sequential-stages rule from `DESIGN.md` §20.4 is
enforced here, in code, not by convention.

---

## 8. Assembly (two-track merge)

Input: two segment lists. Output: one ordered, speaker-tagged timeline.

1. **Tag** each segment `ME` / `THEM` from its track.
2. **Merge** by `start`, stable.
3. **Echo suppression.** If Zoom's AEC leaks, your voice appears on both tracks. For each `ME` segment, look for a `THEM` segment overlapping in time with high text similarity (`rapidfuzz.ratio ≥ 85`); drop the `THEM` copy. Log the count — a high rate means the user should enable AEC.
4. **Coalesce** consecutive same-speaker segments separated by < 2 s into one turn.
5. **Emit** `transcript.md`:

```markdown
**[00:12] THEM:** ...
**[00:18] ME:** ...
```

Overlapping speech (both talking) is preserved as two adjacent turns — the timestamps make
the overlap visible, and the LLM handles it fine.

Later (diarization): `THEM` splits into `THEM_1/2/3`, with name mapping deferred to the LLM.
The interface is designed for it now: `Segment.speaker` is a string, not an enum.

---

## 9. Summarization

### 9.1 Shape

Map-reduce, always — even for short meetings, so there is one code path.

- **Map:** the transcript is windowed at ~6 000 tokens with 300 tokens of overlap. Each window produces a partial extraction against a reduced schema (`topics`, `decisions`, `action_items`, `quotes`).
- **Reduce:** partials + meeting metadata → the full `notes.json`.

Windowing counts **real tokens**, not characters — Hebrew inflates token counts 2–4× per
word and a character heuristic will silently blow the window. Use
`client.messages.count_tokens` once per window boundary search, cached per meeting.

### 9.2 The call

```python
resp = client.messages.parse(
    model="claude-opus-5",
    max_tokens=16000,
    thinking={"type": "adaptive"},
    output_config={"effort": "high", "format": {"type": "json_schema", "schema": NOTES_SCHEMA}},
    betas=["server-side-fallback-2026-07-01"], fallbacks="default",
    system=[
        {"type": "text", "text": SYSTEM_PROMPT,
         "cache_control": {"type": "ephemeral"}},          # stable prefix
        {"type": "text", "text": glossary_block,
         "cache_control": {"type": "ephemeral"}},          # changes weekly at most
    ],
    messages=[{"role": "user", "content": transcript_window}],   # volatile — after the breakpoints
)
notes = resp.parsed
```

Three deliberate choices:

- **`messages.parse()` with a JSON Schema**, not free-text-then-parse. The schema is the contract; validation and retry happen inside the SDK.
- **Prompt caching on the system prefix.** Map-reduce sends the same system prompt and glossary N+1 times per meeting; the cached prefix makes windows 2..N cheap. Verify with `usage.cache_read_input_tokens` — if it's ever zero across windows, something volatile leaked into the prefix.
- **Server-side fallbacks enabled by default.** Meeting content occasionally trips safety classifiers (an HR discussion, a security incident review); `stop_reason == "refusal"` on a summarization job would otherwise dead-end a meeting the user can't re-run. With `fallbacks="default"` the request is re-routed by refusal category instead of failing. Always check `stop_reason` before reading content.

**Sensitive meetings** route to `llm/client.py`'s Ollama implementation instead — same
protocol, same schema, `format: json` on the Ollama side, plus a validation-and-repair loop
because local models honour schemas less reliably.

### 9.3 `notes.json` schema (abridged)

```jsonc
{
  "type":"object","additionalProperties":false,
  "required":["title","tldr","topics","decisions","action_items"],
  "properties":{
    "title":{"type":"string","maxLength":120},
    "tldr":{"type":"array","items":{"type":"string"},"minItems":2,"maxItems":6},
    "participants":{"type":"array","items":{"type":"object","properties":{
        "name":{"type":"string"},"role":{"type":"string"},"track":{"enum":["ME","THEM"]}}}},
    "topics":{"type":"array","items":{"type":"object","required":["heading","points"],
      "properties":{"heading":{"type":"string"},
        "points":{"type":"array","items":{"type":"string"}},
        "quotes":{"type":"array","items":{"type":"object","properties":{
          "who":{"type":"string"},"text":{"type":"string"},"at_ms":{"type":"integer"}}}}}}},
    "decisions":{"type":"array","items":{"type":"object",
      "properties":{"what":{"type":"string"},"rationale":{"type":"string"},
                    "who_decided":{"type":"string"},"at_ms":{"type":"integer"}}}},
    "action_items":{"type":"array","items":{"type":"object",
      "properties":{"who":{"type":"string"},"what":{"type":"string"},
                    "due":{"type":["string","null"]},
                    "confidence":{"type":"number","minimum":0,"maximum":1}}}},
    "open_questions":{"type":"array","items":{"type":"string"}},
    "risks":{"type":"array","items":{"type":"string"}},
    "follow_up_email":{"type":"object","properties":{
        "subject":{"type":"string"},"body_md":{"type":"string"}}}
  }
}
```

`at_ms` on quotes/decisions is what makes the summary **clickable back into the audio** —
the single highest-value field for trusting a summary, and it costs nothing to ask for.

### 9.3b Output language

`summary.language` ∈ {`en` (default), `he`, `auto`}. `auto` means "match
`meetings.language`". The resolved value is stored on the meeting and passed to the prompt
as an explicit instruction; it also determines the rendered document's direction (§10) —
a Hebrew meeting summarised into English produces an **LTR** document.

Cross-language summarisation (Hebrew audio → English notes) is a first-class case, not a
translation step bolted on: the transcript goes in as-is and the model is instructed to
produce the notes in the target language, preserving proper nouns and technical terms
verbatim.

### 9.4 Prompts

`llm/prompts/{system,map,reduce}.md`, each with a `version:` front-matter line recorded in
`meta.json`. Output language follows the transcript (Hebrew in, Hebrew out) with an
explicit instruction to keep technical English terms untranslated — the alternative is
"מכולה" for "container".

**Sanity gates** after reduce, surfaced as `NEEDS_REVIEW` rather than silent acceptance:
zero `action_items` on a meeting > 20 min, `tldr` shorter than 2 items, or any
`action_items[].who` not matching a known participant.

---

## 10. Rendering

Jinja2 → two outputs from one template:

| Output | Use | Build |
|---|---|---|
| `summary.html` | local UI, browser | `<style>` block, CSS custom properties, responsive |
| `summary.email.html` | SMTP body | same template → **premailer** inlines every rule; tables for layout; no custom properties |

**Direction follows the summary language, not the meeting's.** The template receives
`lang` and `dir` and is rendered accordingly; one stylesheet serves both because every rule
uses logical properties. Golden files exist for **both** directions.

RTL specifics: `<html dir="rtl" lang="he">` when the output is Hebrew, logical CSS properties throughout,
`unicode-bidi: plaintext` on quote blocks so an embedded English product name doesn't
reverse the line, and a system font stack (`"Segoe UI", "Arial Hebrew", sans-serif`) — no
webfonts, because the page must render offline and inside a mail client.

Re-rendering is decoupled from the LLM: `notes.json` is the source of truth, so restyling
every past meeting is a batch `render` job with no API cost.

---

## 11. Delivery

`mail.py`, stdlib only:

```python
msg = EmailMessage()
msg["Subject"], msg["From"], msg["To"] = subject, cfg.smtp.from_addr, recipients
msg.set_content(plaintext_fallback)                       # text/plain
msg.add_alternative(email_html, subtype="html")           # text/html
with smtplib.SMTP(cfg.smtp.host, cfg.smtp.port, timeout=30) as s:
    s.starttls(); s.login(cfg.smtp.user, keyring_get("smtp"))
    s.send_message(msg)
```

Default mode is **draft**, which without the Gmail API means: render, store, and notify —
the user sends from the UI with one click, which also handles the no-recipients case
(personal meetings) without a special path. `auto_send` is opt-in per meeting or globally.

---

## 12. HTTP API

All routes under `/api`, JSON, `127.0.0.1` only.

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/status` | profile, detector state, recorder state, queue depth, disk free |
| POST | `/api/recording/start` · `/stop` · `/pause` | manual control |
| GET | `/api/meetings?from=&to=&q=&state=` | timeline + search (FTS) |
| GET | `/api/meetings/{id}` | full record incl. evidence and job states |
| PATCH | `/api/meetings/{id}` | rename, mark sensitive, discard |
| GET | `/api/meetings/{id}/transcript` · `/notes` · `/summary.html` | artifacts |
| GET | `/api/meetings/{id}/audio?track=me&t=123` | ranged audio for the player |
| POST | `/api/meetings/{id}/jobs/{stage}/retry` | re-run one stage |
| POST | `/api/import` | multipart upload → `source=imported` |
| GET/PUT | `/api/glossary` | |
| GET/PUT | `/api/settings` | |
| GET | `/api/detector/events?limit=50` | the tuning instrument |
| POST | `/api/detector/ignore` | add a process to the ignore list |
| GET | `/api/events` (SSE) | live recorder/queue state for the UI |

SSE rather than WebSockets: the traffic is one-way server→client and SSE reconnects itself.

---

## 13. Frontend

Vite + React + TS + TanStack Query + Tailwind (`dir="rtl"`, logical properties).

| Route | Screen |
|---|---|
| `/` | **Timeline** — day/week CSS grid; live card while recording; queue cards with ETA |
| `/m/:id` | **Meeting** — summary, transcript with wavesurfer.js click-to-seek, actions |
| `/search` | FTS results with snippets |
| `/attention` | failures, discarded-pending-review, untitled, delivery retries |
| `/glossary` | term editor |
| `/settings` | data root, languages, profile, keys, SMTP, detection weights, retention |
| `/detector` | last 50 wakes with evidence — visible while tuning, hidden behind a flag later |

**Localisation.** No user-visible string is hardcoded. A flat message catalogue per locale
(`en`, `he`) with a typed key union so a missing translation is a compile error.
`ui.language` defaults to `en`; changing it sets `document.dir` and `document.lang` at
runtime with no reload. A lint rule bans physical CSS direction properties
(`pl-`/`pr-`/`ml-`/`mr-`/`text-left`/`text-right`) in favour of logical ones, so the layout
mirrors correctly in both directions by construction.

Content direction is independent of chrome direction: a transcript turn or a summary block
carries its own `dir` from the meeting/summary language, so an English UI renders Hebrew
content correctly inside it.

State: TanStack Query for everything, invalidated by SSE events. No Redux; there is no
client-side state worth centralising.

---

## 14. Configuration

Three layers, later overriding earlier:

1. **defaults** — in code.
2. **`app_config.json`** in the app home — UI-editable, the only file the user's settings screen writes.
3. **environment** — `.env` for dev, non-secret paths only.

**Secrets never appear in any of them.** `keyring` (Windows Credential Manager) holds
`anthropic`, `smtp`, `google_refresh_token`, under service name `upshot`.

```jsonc
{
  "data_root": null,                       // null → %LOCALAPPDATA%\upshot\meetings
  "ui":      {"language": "en"},           // en|he — interface locale, drives document dir
  "summary": {"language": "en"},           // en|he|auto  (auto = match the transcript)
  "asr":     {"language_mode": "detect",   // detect|fixed
              "default_language": "he",
              "detect_min_confidence": 0.6},
  "profile": "auto",                       // auto|gpu-live|cpu-deferred|remote-worker
  "worker_url": null,
  "job_policy": "auto",                    // auto|asap|after_meeting|when_idle|scheduled
  "audio": {"chunk_s":60,"preroll_s":60,"max_meeting_h":4,"min_meeting_s":120},
  "detection": {
    "mode": "shadow",                      // shadow|on|off  — shadow is the install default
    "threshold": 5, "sustain_s": 10,
    "weights": { "mic.known_app":3, "mic.unknown_app":1, "vad.loopback":2,
                 "vad.mic":2, "window.title":2, "session.render":1,
                 "camera":1, "calendar":3, "ignored":-5 },
    "known_apps": ["Zoom.exe","ms-teams.exe","Teams.exe","chrome.exe","msedge.exe","slack.exe"],
    "ignore":     ["VoiceAccess.exe","NVIDIA Broadcast.exe"]
  },
  "llm": {"provider":"anthropic","model":"claude-opus-5","effort":"high",
          "local_model":"dictalm3-nemotron-12b"},
  "delivery": {"mode":"draft","attach_transcript":false},
  "retention": {"audio_days":30,"transcript_days":null},
}
```

---

## 15. Errors, logging, diagnostics

- **Logging:** stdlib `logging`, `RotatingFileHandler` (10 MB × 5) at the app home, plus a per-meeting `pipeline.log`. Format includes `meeting_id` via a `ContextVar` filter so grep by meeting works.
- **Error taxonomy:** `RecoverableError` (retry with backoff) vs `PermanentError` (fail the job, surface in `/attention`) vs `Preempted` (release, no attempt counted). Anything uncaught is treated as recoverable twice, then permanent.
- **Egress log:** every outbound request — Anthropic, SMTP, Google — logged with meeting id, destination, and byte count. This is what makes "what left this machine" answerable, and it enforces the `sensitive` flag at the boundary rather than in the UI (`SECURITY-AND-AUTH.md` §9).
- **Diagnostics bundle:** a UI button producing a zip of logs + `meta.json` files + config **with secrets and transcript text stripped** — so a bug report is possible without shipping meeting content.

---

## 16. Security implementation

- **Bind** `127.0.0.1` explicitly, never `0.0.0.0`.
- **Host-header middleware** rejecting anything not in `{localhost:8000, 127.0.0.1:8000}` → 421. This is the DNS-rebinding defense; it runs before routing.
- **Auth:** on first launch the tray opens `http://127.0.0.1:8000/?k=<one-time token>`; the server exchanges it for an `HttpOnly; SameSite=Strict; Max-Age=1y` cookie. No cookie → 401 with *"open Upshot from the tray"*. Calendar deep-links work because the cookie already exists.
- **CSRF:** double-submit token on all mutating routes; `SameSite=Strict` is the primary defense.
- **CORS:** no `Access-Control-Allow-Origin` header at all.
- **Audio streaming** is range-request only, gated by the same cookie.
- **Single instance:** named mutex `Local\upshot` (one per signed-in user); a second launch
  opens the first one's page, from the port it recorded in `<home>\server.port`.

---

## 17. Packaging and first run

PyInstaller one-dir, Inno Setup per-user
installer, `build.ps1` chain: deps → ffmpeg → `vite build` → PyInstaller → Inno.

**Hidden imports** that PyInstaller will miss and that must be declared: `ctranslate2`,
`onnxruntime` (and its `capi` DLLs), `pycaw`/`comtypes` generated interfaces (force
early-binding or ship the generated modules), `keyring.backends.Windows`.

**First run** (`app_core/bootstrap.py`, ported):
1. Probe CUDA → choose profile → pick the model (`large-v3-ct2` vs `turbo-ct2`).
2. **Adopt an existing local model/CUDA directory** when `model_path`/`cuda_dir` are configured — skips a 3 GB download on a machine that already has the artifact.
3. Create app home, DB, run migrations.
4. Register the AppUserModelID (toasts need it) and the Task Scheduler logon task.
5. Open the setup UI: audio device pick + a 5-second two-track test recording with level meters — the one check that proves capture works before a real meeting depends on it.
6. Anthropic key. Everything else is optional and skippable.

---

## 18. Testing

| Layer | Approach |
|---|---|
| **Pipeline** | `fake.py` ASR + a recorded fixture WAV → full pipeline in CI, no GPU, no model, no network |
| **LLM** | recorded response fixtures; schema validation tested against deliberately malformed outputs |
| **Scoring** | pure function over recorded evidence vectors — the detection logic is testable with zero Windows APIs |
| **Renderer** | golden-file comparison for both HTML variants, including an RTL/bidi case with embedded English |
| **Audio** | a synthetic 48 kHz float32 generator → assert sample counts, timestamps, chunk boundaries, and manifest durability after a simulated kill |
| **Manual** | the `DETECTION.md` §11 scripted positives/negatives, on the real machine |

CI runs everything except audio-device and Windows-API tests; those are a documented
manual pass before each release.

---

## 19. Open implementation questions

1. **FTS5 present** in the Windows Python build? One line to check; fallback is `LIKE` over a plain table.
2. **PyAudioWPatch dual-stream stability** over hours — does opening capture + loopback simultaneously survive device changes and sleep? The first thing to prototype in M1, before any structure is built on it.
3. **`nemotron_h` support** in the installed Ollama/llama.cpp build, for the local-LLM path.
4. **Real Hebrew token ratio** — measure with `count_tokens` on an actual transcript in M0; every cost and window-size estimate depends on it.
5. **Clock drift magnitude** between the two WASAPI clocks over a 2-hour meeting — measure before deciding §4.3's "don't correct it" is safe.
6. **Language-detection accuracy on short first chunks** — does 60 s of meeting opening ("שלום, אתם שומעים אותי?") reliably classify? If not, extend detection to the first two chunks before pinning.

All six are measurements with scheduled owners in `EXECUTION-PLAN.md`. **No open item
requires a human decision before this build ships.** Google Calendar is out of scope for
V1 — only the enrichment seam exists (`EXECUTION-PLAN.md` Phase 6b) — so no question about
Google policy, scopes, or admin configuration bears on any phase here.
