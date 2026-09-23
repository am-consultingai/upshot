# Upshot — Design

A local Windows application that turns an online meeting into an HTML summary with no
manual steps: it notices the meeting start on its own, records both audio tracks,
transcribes with a local Hebrew Whisper, structures the notes with an LLM, renders HTML
from a template, and files it in a local timeline you browse like a calendar.

Replaces today's manual flow: Audacity → script → paste to LLM → paste to another LLM.

**Two constraints shape the architecture more than anything else:**

1. **Two target machines from day one** — the GTX 1080 desktop (fast path) and any laptop with no GPU (slow path, same features).
2. **Nothing external is load-bearing — and V1 ships with no integrations at all.** No Google account, no calendar, no cloud service is required or built. The meeting record carries the fields an enrichment source would fill, and a protocol exists for one, but the only implementation that ships returns nothing.

---

## 1. Decisions

| Decision | Choice |
|---|---|
| Topology | **Single Windows application** — tray app + local web UI, shipped as a PyInstaller one-dir build behind a per-user Inno installer |
| Transcription | **Local**, faster-whisper + ivrit.ai models — `large-v3-ct2` on GPU, `large-v3-turbo-ct2` on CPU |
| Compute | **Execution profiles**, auto-detected: `gpu-live`, `cpu-deferred`, `remote-worker` |
| Summarization | **Claude API** — Opus 5 default, Sonnet 5 for cost (~$0.13 vs ~$0.05/meeting); a "sensitive" flag forces local Ollama |
| Trigger | **Manual Start/Stop in the UI** (+ hotkey) in M1, **auto-detection** in M2. Calendar arming is post-V1 and out of scope |
| Delivery | **The local timeline is the product**; email over plain SMTP (app password — no Google API, no OAuth) is one optional channel out of it |
| Meeting language | **Auto-detected per meeting** from the first chunk, then pinned. Default `he`; fully-English meetings are transcribed as English |
| UI & summary language | **Multi-language with a selector, English default.** `ui.language` and `summary.language` are independent settings; text direction follows the selected locale |
| Data location | **Configurable data root**, chosen at setup, changeable later. Default `%LOCALAPPDATA%\upshot\meetings` |

---

## 2. Environment (verified on this machine)

**Already on the target machine:**
- **Python 3.13**, **GTX 1080 8 GB** (Pascal — `int8` is the right compute type), **Ollama** installed with no models pulled, Zoom and Audacity installed.
- An **ivrit-ai CTranslate2 model directory** (~2.9 GB `model.bin`) and an unpacked **cuBLAS/cuDNN wheel directory**, both left behind by earlier local work. The app takes a `model_path` and a `cuda_dir` in config, so first run can point at these and skip a 3 GB download. Neither is required — absent them, the bootstrap downloads.
- A working Google OAuth client pattern (desktop client JSON, `token.json`, loopback callback) is re-created from scratch here per `SECURITY-AND-AUTH.md`; no external code is imported.

**Gaps / setup:**
- Pull an Ollama model for the sensitive-meeting path (8 GB VRAM is the ceiling).
- Prefer **Python 3.12** on Windows for wheel availability (`pyaudiowpatch`, `ctranslate2`); verify 3.13 wheels first.
- **WSL interop is disabled** here (`[interop] appendWindowsPath=false`, no binfmt entry), so a WSL shell can write to `/mnt/c/...` but cannot execute Windows binaries. Enabling it (`[interop] enabled=true` in `/etc/wsl.conf`, then `wsl --shutdown`) would let the dev loop run and test directly.

---

## 3. Why Windows-native, and what it buys

WSL2 has no audio device (`/dev/snd` is empty; only WSLg's RDP pulse sink), so it cannot
hear a Teams/Zoom call. Running natively on Windows gives direct **WASAPI loopback**
capture — the same host Audacity uses — with no virtual-cable driver to install.

The important consequence: **two independent tracks**.

- `me.wav` — microphone (WASAPI capture)
- `them.wav` — system output loopback (everyone else)

That is free, deterministic speaker separation on the boundary that matters most. Your
voice never bleeds into the remote track, so "who said this" is a fact, not a model
guess. Diarization later only has to split speakers *within* `them.wav`
(`ivrit-ai/pyannote-speaker-diarization-3.1` is published and is the obvious choice).

---

## 4. The invariant: recording is sacred

This is a **recorder** that happens to summarize, not a summarizer that happens to
record. Everything else in this document bends to two rules:

1. **A recording must never be lost.** Audio goes to disk continuously as small chunk
   files, never buffered in RAM, never held in one big handle that a crash truncates. A
   meeting is fully reconstructible from what's on disk with the app dead.
2. **Recording must never degrade the meeting.** No heavy compute on the machine during
   a call unless we know it's free. On a CPU-only laptop this means: record now,
   think later.

Rule 2 is what the no-GPU requirement really costs, and it's why capture and processing
are separate subsystems rather than one pipeline.

---

## 5. Roles and execution profiles

### Three roles, one binary

| Role | Cost | Needs |
|---|---|---|
| **Recorder** | ~0 CPU — WASAPI capture and WAV writes | audio devices |
| **Worker** | expensive — Whisper + LLM | GPU, or patience, or a network hop |
| **Library** | cheap — SQLite, FastAPI, UI, search | nothing |

On the desktop, all three run in one process. On a laptop, Recorder and Library are
local while the Worker may be somewhere else. Same installer, different config.

### Profiles (probed at startup, user-overridable)

| Profile | Chosen when | Transcribes | Model | Summary arrives |
|---|---|---|---|---|
| `gpu-live` | CUDA present, ≥4 GB VRAM on GPU 0 (read with `nvidia-smi`; unreadable → CPU) | during the meeting, chunk by chunk | `whisper-large-v3-ct2`, int8 (Pascal) | ~1 min after the call |
| `cpu-deferred` | no CUDA | **after** the meeting, on policy | `whisper-large-v3-turbo-ct2`, int8, cores−2 threads | 0.5–2× meeting length later |
| `remote-worker` | a worker URL is configured and reachable | chunks POSTed to the GPU box as they close | whatever that box runs | ~1 min after the call |
| `cloud-asr` | opt-in only, never a silent fallback | after the meeting | provider API | minutes |

Fallback chain is explicit and logged: `remote → local GPU → local CPU`. If the desktop
is asleep when the laptop tries to reach it, the laptop transcribes locally and the UI
says so — it does not silently queue forever.

### Why `turbo` on CPU

See **§20** for exact model sizes and RAM figures. `ivrit-ai/whisper-large-v3-turbo-ct2` is published (6.4K downloads) in the exact
CTranslate2 format faster-whisper loads. Turbo keeps the full encoder but cuts
the decoder from 32 layers to 4, which is where CPU time goes — several times faster than
`large-v3` for a modest accuracy cost, and still a **Hebrew fine-tune**, which the generic
`small`/`base` fallbacks everyone else uses are not.

This matters because the usual CPU escape hatch is closed for us: the fast English-only
engines (Parakeet) don't do Hebrew at all, and generic small Whisper does it badly. Hebrew
forces us into the large-class models, so `turbo` + int8 + deferred execution is the only
combination that is both fast enough and accurate enough on a laptop.

### The work queue and its policy

Jobs live in SQLite; a single worker drains them. Policy is a setting, defaulted per profile:

- `asap` — start immediately (default on `gpu-live` / `remote-worker`)
- `after_meeting` — start when recording stops (default on `cpu-deferred`)
- `when_idle` — no mic in use, no fullscreen app, CPU below a threshold, on AC power
- `scheduled` — a fixed nightly hour, e.g. 02:00

Two hard rules regardless of policy: **a starting recording preempts transcription**
(the worker pauses mid-job and resumes later), and the worker never uses more than
cores−2 threads. The meeting always wins.

---

## 6. Components

```
┌─ Upshot (single Windows process) ─────────────────────────┐
│                                                                  │
│  Tray launcher (pystray)  ── status icon, Start/Stop/Pause,      │
│                              "open dashboard", "skip this one"   │
│  DETECTOR         mic-in-use + dual-track VAD → meeting start/stop │
│  Scheduler        (optional) Google Calendar → arm plan, enrichment│
│       ↓                                                           │
│  RECORDER         WASAPI loopback + mic → rolling 60s WAV chunks  │
│       ↓           (straight to disk; the only real-time subsystem)│
│  ──────────────── queue boundary ──────────────────────────────  │
│  WORKER           drains jobs per policy; preempted by recording  │
│    ├ transcribe   faster-whisper — local GPU / local CPU / remote │
│    ├ assemble     merge tracks by timestamp → speaker-tagged md   │
│    ├ summarize    Claude (or Ollama if sensitive) → notes.json    │
│    ├ render       Jinja2 + RTL CSS → summary.html                 │
│    └ deliver      Gmail API: draft or send                        │
│  LIBRARY          FastAPI :8000 + React UI, SQLite index + FTS    │
└──────────────────────────────────────────────────────────────────┘
```

---

## 7. Where meetings come from

**Nothing outside the app is load-bearing.** A meeting is created because you pressed
Start, or because you dropped a file in. Everything else — auto-detection, calendar
arming — is an additional *source* that produces the same record and runs the same
pipeline, and can be added later without touching anything downstream.

### Four sources, one meeting record

| Source | Creates a meeting when | Milestone |
|---|---|---|
| `manual` | you press Start in the UI, or hit the hotkey | **M1 — the only one that ships first** |
| `imported` | you drop an audio file into the watch folder or the UI | **M0** |
| `calendar` | an event is coming up — arms early, and enriches whatever gets recorded | **post-V1, out of scope**; only the seam is built |
| `detected` | evidence score crosses the threshold — see `DETECTION.md` | M2 |

The record carries `source`, and every field a later source would fill (`title`,
`participants`, `agenda`, `recipients`) exists from day one — filled by you, by the LLM,
or left empty. That's what makes adding calendar and detection later a matter of
populating fields rather than reworking the flow.

### Auto-detection

Fully specified in **`DETECTION.md`** — signals, the four-tier state machine, the pre-roll
buffer, the evidence-scoring model, notifications, and the shadow-mode test plan. The
summary: the obvious objection is junk — what stops it recording a YouTube video, or a
40-second "can you hear me?" Track activity is the core discriminator:

Detection-first raises an obvious question: what stops it recording a YouTube video, or a
40-second "can you hear me?"

The two-track capture answers it almost for free. **Require activity on both tracks**:

| Situation | Mic | Loopback | Recorded? |
|---|---|---|---|
| Real meeting | speech | speech | ✅ |
| Watching a video | silent | audio | ❌ |
| Dictation / voice note | speech | silent | ❌ |
| Music while working | silent | audio | ❌ |

A conversation is the only thing that lights up both. That single rule kills nearly all
false positives without a process allowlist, and it's a benefit of the two-track design
beyond speaker attribution. Backed by three cheap guards: a **minimum duration** (under
~2 minutes → `DISCARDED` rather than deleted), an **app blocklist**, and a **60-second
release grace** so a network blip doesn't split one meeting into three.

The two guards that matter even for manual recording — the release grace and the minimum
duration — ship in M1 anyway, because you will forget to press Stop.

### Naming a meeting with no calendar

A meeting starts life untitled — you shouldn't have to name it before you can record it.
It gets a name through a chain that degrades gracefully:

1. **Foreground window title at start** — "Zoom Meeting", "Weekly Sync | Microsoft Teams", the browser tab for a Meet call. Often exactly right, and free.
2. **The LLM's `title` field** after summarization — it read the whole conversation, so this is usually better than the window title and replaces it.
3. **You rename it** in the UI. Sticks forever.

Participants degrade the same way: with a calendar you get real names before transcription;
without one, `ME` / `THEM` from the two tracks, plus whatever names the LLM recovers from
the transcript, plus the glossary. Recipients default to yourself.

### What a calendar *would* add — post-V1, recorded as rationale for the seam

Not built in this version. Kept here because it explains why the meeting record carries
fields the current build fills by other means, and why the seam is shaped as it is.

| | Without Google | With Google |
|---|---|---|
| Start/stop | mic + dual-track VAD | same signal, plus arming from `start−2min` |
| Title | window title → LLM → you | the event's real title, immediately |
| Participants | `ME`/`THEM` + names from the transcript | real names **before** transcription → into Whisper's `initial_prompt` |
| Agenda context | none | description feeds the summary prompt, plus a "was every item covered" check |
| Recipients | yourself | attendees, prefilled |
| Timeline view | past meetings | past **and** upcoming |
| Back-to-back split | speech gap / mic release | exact event boundaries |

The one thing worth arming *early* for is pre-warming the model on a GPU box and
running the disk/device preflight before the call rather than during it.

**Matching (post-V1):** when a recording starts and an enrichment source exists, look up the event covering
that moment and attach it. This keeps the predictive benefit — attendee names are in hand
before transcription — without the recorder ever waiting on a calendar.

**Auth (post-V1 reference only):** see `SECURITY-AND-AUTH.md` §§2–8 — `calendar.events.readonly` only, published to
Production, and a work-Workspace gate that needs testing before anything is built on it.

---

## 8. The flow, end to end

### The happy path (M1): you press Start

**Idle** — tray icon grey, UI open in a tab. Nothing is running but the web server.

**Start** — you click the big button in the UI (or hit the hotkey). Preflight runs: both
audio devices present, ≥2 GB free disk, model resolvable. The recorder opens both WASAPI
streams, the folder is created, `chunk_0001.wav` lands in each track within a minute, the
tray goes red. The provisional title comes from the foreground window title — usually
"Weekly Sync | Microsoft Teams" or similar, and free.

**During** — on a GPU box, chunks transcribe as they close and a live transcript builds in
the UI. On a CPU box, the machine writes WAV files and does nothing else. The UI shows
elapsed time, a live level meter per track (so you can *see* that both sides are being
captured — the single most valuable trust signal in the whole app), and a Stop button.

**Stop** — chunks concatenated, manifest verified, state `RECORDED`, job enqueued. If you
forgot to press Stop, the meeting just runs long; a configurable max duration caps it, and
trailing silence is trimmed.

**Worker runs** (immediately on GPU, on policy on CPU) — transcribe → assemble the two
tracks into one speaker-tagged timeline → glossary correction → LLM → `notes.json` →
Jinja2 → `summary.html`. The LLM's title replaces the window-title guess.

**Done** — Windows toast: *"Summary ready — Weekly Sync (47 min)"*. Clicking it opens the
meeting page. The summary is in the library whether or not you ever set up email.

### What would change with a calendar connected (post-V1, not built)

- **T−2min:** the meeting appears as `ARMED` on the timeline; preflight runs; the model pre-warms.
- **At start:** the recording attaches to the event covering that moment — real title and attendee names immediately, so `initial_prompt` gets them before the first chunk is transcribed.
- **At the end:** delivery has a recipient list, so the email draft is actually useful rather than a note to self.
- **On the timeline:** tomorrow shows up, not just yesterday.

Nothing in the recording path changes. That's the point.

### Imported

 drop a `.wav`/`.mp3`/`.m4a` into the watch folder or the UI. A meeting record
is created with `source=imported`, timestamps taken from the file's mtime, and the pipeline
runs from `TRANSCRIBING`. Single-track, so speakers come from the LLM rather than the track
split. This is how the existing Audacity archive gets ingested — and it's the path to build
first (§17).

### When something fails

Every stage failure leaves the meeting parked at its last good state with the audio intact
and a Retry button. Specifically: **transcription failing never loses audio, summarization
failing never loses the transcript, and delivery failing never loses the HTML.** The tray
shows a badge; the UI shows why.

---

## 9. Data layout

One self-contained folder per meeting — everything reproducible from what's on disk.

```
%LOCALAPPDATA%\upshot\meetings\2026-08-27_1400_weekly-sync\
  meta.json          calendar event, attendees, timings, flags, profile, model versions
  audio\
    me\chunk_0001.wav …            (mic)
    them\chunk_0001.wav …          (loopback)
    manifest.json                  chunk sequence + durations; detects a torn write
    me.wav  them.wav               concatenated after the call
  transcript.json    segments with word timestamps + track tag
  transcript.md      human-readable, speaker-tagged
  notes.json         the structured LLM output (source of truth for rendering)
  summary.html       rendered artifact — the thing emailed
  pipeline.log
```

**SQLite** (`index.db`): meetings (id, title, start, duration, state, attendees,
sensitive flag, delivery status), a `jobs` table for the queue and state machine, and
FTS5 over transcripts for cross-meeting search.

`notes.json` is kept, so restyling every past summary is a re-render.
`transcript.json` is kept, so re-summarizing with a better prompt costs no GPU time.

---

## 10. Pipeline state machine

States: `ARMED*` → `RECORDING → RECORDED → TRANSCRIBING → ASSEMBLED → SUMMARIZED →
RENDERED → DELIVERED`, plus `FAILED(stage)`, `INTERRUPTED` (sleep/crash mid-recording),
`DISCARDED` (too short, or junked by you) and `NEEDS_REVIEW`.
`ARMED` is reserved for a future calendar source and is unreachable in this build — every
other state is reachable with no integrations at all.

Every stage is idempotent and restartable, and `RECORDED` is a real resting state — on
`cpu-deferred` a meeting can legitimately sit there for hours. If the machine reboots
mid-meeting the chunks are still on disk and the pipeline resumes at the first
incomplete stage. If the Claude call fails, the transcript is untouched and only the
summarize stage retries.

Chunk boundaries are cut on VAD silence where possible so words aren't sliced in half.
Note the tuning lesson from Meetily's [#711](https://github.com/Zackriya-Solutions/meetily/pull/711):
segmenting too aggressively (400 ms of silence) fragmented speech into sub-3-second
snippets and made Whisper hallucinate boilerplate; they raised it to 2000 ms. Our 60-second
chunks are far past that threshold, which is one of the quiet advantages of not going live.

---

## 11. Transcription details

- Device selection: CUDA probe → `float16`/`int8_float32`/`int8`, a warmup inference to force the lazy GPU library load at startup, and automatic CPU fallback on any cuBLAS-shaped error. Full spec in `EXECUTION-PLAN.md` Phase 5. On Pascal (GTX 1080) `int8` is correct.
- **Language is detected once per meeting, then pinned.** Most meetings are Hebrew, some are entirely English; per-chunk detection is not used, because Whisper's mid-audio language switching is unreliable and a wrong switch corrupts a whole chunk. Detection runs on the first chunk that contains speech — preferring the `them` track, which usually carries more of it — and the result is stored on the meeting and reused for every subsequent chunk. If confidence is below 0.6 the configured default (`he`) is used and the meeting is flagged for review.
- VAD filter on, word timestamps on (needed to interleave the two tracks).
- **Glossary file** (`glossary.yaml`): people, company/product names, acronyms, Hebrew↔English jargon. Used twice — as Whisper's `initial_prompt` (biases decoding) and as a correction pass before summarization. Highest-leverage quality knob for Hebrew technical meetings, and it compounds: every correction improves the next meeting.
- **Assembly:** the two tracks transcribe independently, then merge into one timeline by timestamp, tagged `ME` / `THEM`. Later: pyannote inside `them.wav` to split `THEM` into speakers, with the LLM mapping them to real names from the attendee list and in-conversation cues ("תודה יוסי").

**Model resolution order** (per profile, all local): configured path → app-home download
→ download. With `model_path` pointed at an existing local model directory, first run downloads nothing.

**The meeting language picks the repo.** Hebrew, and per-meeting detection, use the ivrit-ai
fine-tunes above. A language pinned to anything else (first-run setup's "English") uses
stock Whisper of the same size — `Systran/faster-whisper-large-v3` on GPU,
`mobiuslabsgmbh/faster-whisper-large-v3-turbo` on CPU — because the Hebrew fine-tune's
detection leans to Hebrew and pinned an English meeting as Hebrew.

---

## 12. Summarization

**Two stages, but only the first is an LLM.**

**Stage 1 — transcript → `notes.json`** against a fixed schema:

```json
{
  "title": "...", "date": "...", "duration_min": 47,
  "tldr": ["...", "..."],
  "participants": [{"name": "...", "role": "..."}],
  "topics": [{"heading": "...", "points": ["..."], "quotes": [{"who":"...","text":"..."}]}],
  "decisions": [{"what": "...", "rationale": "...", "who_decided": "..."}],
  "action_items": [{"who": "...", "what": "...", "due": "...", "confidence": 0.0}],
  "open_questions": ["..."], "risks": ["..."],
  "follow_up_email": {"subject": "...", "body_md": "..."}
}
```

A forced schema makes the output *checkable* — empty `action_items` on a 45-minute
planning call is a detectable failure, not a silently bad summary.

Long meetings use map-reduce: per-chunk notes → global synthesis. Keeps within context
limits, makes the stage resumable, lets partial results survive a failure.

**Stage 2 — `notes.json` → HTML via Jinja2, no LLM.** Deterministic, identical styling
every time, RTL-correct by construction, zero tokens, and restyling all history is one
re-render. An LLM emitting HTML is the flakiest link in the current manual chain.

**Output language** is a setting, not the transcript's language. `summary.language` takes
`en` (default), `he`, or `auto` (match the transcript). A Hebrew meeting summarised into
English is a normal, supported case — it is what makes notes shareable outside the team —
and it changes only the prompt, never the pipeline. Direction of the rendered HTML follows
the summary language, not the meeting's.

**Model:** Opus 5 by default; Sonnet 5 is ~2.5× cheaper per meeting and likely sufficient
for structured extraction — worth A/B-ing on real transcripts once M1 works.

**Sensitive meetings:** a per-meeting flag (from a calendar keyword rule or the tray)
routes stage 1 to local Ollama. Nothing leaves the machine. Expect weaker Hebrew — the
flag is for meetings where that trade is obviously right.

---

## 13. Delivery

**The local library is the product; email is one channel out of it.** A summary is
"delivered" the moment it's rendered and visible in the UI with a toast — everything below
is optional and must never be a prerequisite for the flow completing.

**SMTP** (`smtp.gmail.com:587`) with a Google App Password — deliberately *not* the Gmail
API, which is a restricted scope requiring an annual third-party CASA assessment. See
`SECURITY-AND-AUTH.md` §1.

- Default: **create a draft**, don't send. Recipients default to you; a later enrichment source can prefill them.
- Per-meeting or global setting to auto-send once trusted.
- Transcript attached only if you opt in — often the summary is shareable and the raw transcript isn't.
- Failure is non-fatal: the HTML is on disk, the meeting still reads as complete, and the UI offers Retry.

---

## 14. Web UI (localhost:8000)

**The timeline is the app's own calendar** — and it is the reason the product works with
Google disconnected. Meetings are laid out on a day/week grid exactly as a calendar would
show them, sourced from what actually happened rather than what was scheduled.

> **The timeline always shows the past. Connecting Google adds the future.**
> That is the entire visible difference between the two modes.

- **Timeline** — day/week view. Each meeting is a card: title, duration, state, a one-line TL;DR. Recording now = a live red card with elapsed time and a Stop button. Queued/transcribing = a card with a progress state and honest ETA (the CPU profile lives here). The component accepts an optional `future` list, empty in this build, so a later calendar source needs no UI rework.
- **Meeting page** — the HTML summary, the speaker-tagged transcript with click-a-line-to-seek playback, actions: re-summarize, re-render, email, rename, mark sensitive, discard.
- **Search** — FTS5 across every transcript and summary; filter by date, participant, state.
- **Inbox / needs attention** — failures, `DISCARDED` items awaiting a verdict, meetings with no title, delivery retries. A single place that answers "is anything stuck?"
- **Glossary editor** — the thing you'll actually use weekly.
- **Settings** — Google connect (optional, with a clear "you don't need this" framing), SMTP, Anthropic key, profile override, worker URL, model paths, retention, detection rules, sensitive keywords.

**Localisation:** the interface is translated, not hardcoded. `ui.language` defaults to
**English**, with Hebrew available and a selector in Settings; `document.dir` follows the
active locale. Every layout uses CSS logical properties, so one stylesheet serves both
directions — the decision that was made for Hebrew now pays for itself in reverse.

React + Vite, served by FastAPI in production. Auth and
DNS-rebinding defenses per `SECURITY-AND-AUTH.md` §9.

---

## 15. Configuration

- **Windows Credential Manager** (via `keyring`) — Anthropic key, SMTP app password, OAuth refresh token. Not `.env`.
- `.env` — non-secret paths only: `GOOGLE_CREDENTIALS_PATH`, `WHISPER_MODEL_PATH`, `FFMPEG_PATH`, `OLLAMA_MODEL`, `WORKER_URL`.
- `app_config.json` (UI-editable) — **data root**, `ui.language`, `summary.language`, default meeting language, profile override, job policy, arm rules, retention days, delivery mode, sensitive keywords, chunk length.
- `glossary.yaml` — vocabulary.
- `templates/summary.html.j2` — restyle without touching code.
- `prompts/*.md` — prompts as versioned files; `meta.json` records which version produced a summary.

---

## 16. Packaging

PyInstaller one-dir, Inno Setup per-user installer (no admin prompt), pystray launcher,
and a first-run bootstrap that will adopt an existing local model and CUDA directory when
`model_path`/`cuda_dir` are configured, instead of re-downloading 3 GB. On a no-GPU
machine the bootstrap fetches `turbo-ct2` (~2 GB) and skips the CUDA wheels entirely.
Autostart on login — the app must be running *before* the meeting to catch it.

---

## 17. Build order

**M0 — the pipeline, on files you already have.** Import a `.wav` → transcribe (ivrit,
profile-aware) → LLM → `notes.json` → Jinja2 → `summary.html` → library UI. No audio
capture, no Google, no email. This is deliberately first: it replaces the paste-into-two-LLMs
half of the manual flow **immediately**, it lets you tune prompts and the RTL template
against your existing Audacity archive, and it proves transcription quality before a single
line of WASAPI code exists. Everything downstream depends on this being good; nothing
about it depends on anything else.

**M1 — capture, manually driven.** Two-track WASAPI recording chunked to disk, **Start /
Stop / Pause in the UI** plus a global hotkey, per-track live level meters, window-title
naming, max-duration cap and short-recording discard, the queue and its policies. No
detection, no calendar. After this you never open Audacity again.

**M2 — the tray app becomes automatic.** Auto-detection per `DETECTION.md` (registry watch,
pycaw sessions, window titles, dual-track VAD, evidence scoring, pre-roll buffer), toast
notifications with the *"not a meeting"* feedback loop, the detector log, and **shadow mode
first**. No Google, no network, no accounts — the app is feature-complete for V1 at this point.

**M3 — quality.** Glossary loop, transcript editor with audio seek, re-summarize, prompt
versioning, map-reduce for long meetings. No integrations.

**M4 — memory.** Cross-meeting FTS search, per-person and per-project rollups, "what did we
decide about X", retention/auto-delete of raw audio.

**Post-V1 (not scoped):** Google Calendar behind the Phase 6b seam, remote-worker mode,
diarization inside `them.wav`, the "Meeting Summaries" secondary calendar with clickable
links, live rolling summary, action items to a tracker.

---

## 18. Prior art and product path

See `PRIOR-ART.md` for the landscape and `PRODUCT-PATH.md` for the decisions that keep a
product possible without building one now. Summary: Meetily (30K⭐, MIT) is the same product, minus a custom
Hebrew model (open PRs, unmerged), minus calendar arming (PRO), minus email (PRO). The
paid tools are all cloud and Granola doesn't support Hebrew at all.

---

## 19. Risks and open questions

| Risk | Mitigation |
|---|---|
| `pyaudiowpatch` wheel on Python 3.13 | Verify first; fall back to Python 3.12, or `soundcard` |
| Loopback also captures your own Zoom echo / notification sounds | Zoom's echo cancellation keeps your voice out of the output stream in practice; mute notification sounds while armed |
| CPU profile falls badly behind on a long meeting | Deferred by design, so it can't affect the call; `turbo` + int8 + `when_idle`; show an honest ETA; remote-worker as the real fix |
| 8 GB VRAM shared between Whisper and a local LLM | Sequential by stage — Ollama loads only after Whisper unloads |
| Calendar events with no meeting link (phone 1:1s, ad-hoc calls) | Mic-in-use catches them even unarmed; tray toggle covers the rest |
| Disk fills mid-meeting | Preflight free-space check before arming; retention policy prunes raw audio; recorder halts cleanly rather than corrupting |
| Recording consent | Tray icon visibly red while recording; app blocklist; retention deletes raw audio after N days |

**Decisions cemented** (previously open):

| Question | Decision |
|---|---|
| Calendar account | Moot for V1 — no calendar. Recorded for later: work Workspace for reads, personal Gmail for SMTP |
| Email mode | **Draft-only.** Render, store, notify; sending is one click. `auto_send` stays opt-in |
| Meeting language | **Mostly Hebrew, some fully English.** Detect once per meeting on the first chunk, then pin for every remaining chunk (§11.1) |
| UI / summary language | **Multi-language with a selector, English default.** Two independent settings; direction derived from locale (§14) |
| Raw audio retention | Audio auto-deleted after **30 days**; transcripts and summaries kept indefinitely |
| Data location | **Configurable root**, defaulted at setup (§15) |
| No-GPU machine | A **second personal machine**, tested when available. The `cpu-deferred` profile is a first-class, gate-tested path; `remote-worker` stays a later convenience, not a requirement |

**Nothing is open that blocks the build.** Every remaining unknown is a measurement with a
scheduled phase in `EXECUTION-PLAN.md`.

**Out of scope for V1:** Google Calendar and all Google OAuth. The product must be complete
and useful with no Google account in existence. Only the *enrichment seam* is built
(`EXECUTION-PLAN.md` Phase 6b) so calendar can later be added as one implementation of an
existing protocol. SMTP delivery is unaffected — it uses a plain SMTP server and an app
password, with no Google API involved.
5. Is the no-GPU machine a laptop **you** use, or other people's machines? (Decides whether `remote-worker` is worth building early, and whether the installer needs to be non-technical-friendly.)

---

## 20. Appendix — models and resource budget

All figures below are the **actual** published artifact sizes (queried from Hugging Face),
not estimates. RAM figures are calculated from each model's config and should be confirmed
by measurement in M0.

### 20.1 What has to run

| Component | Required? | Model |
|---|---|---|
| **ASR** | always | `ivrit-ai/whisper-large-v3-ct2` (GPU) or `ivrit-ai/whisper-large-v3-turbo-ct2` (CPU) |
| **VAD** | always | Silero VAD — bundled inside faster-whisper, ~2 MB, negligible RAM |
| **Summarizer** | one of | Claude API (nothing local) **or** a local Hebrew LLM (below) |
| **Diarization** | later | `ivrit-ai/pyannote-speaker-diarization-3.1` — small weights but drags in PyTorch (~2.5 GB install, +2–3 GB RAM). Deferred for good reason |

### 20.2 ASR — real sizes

CTranslate2 stores weights in float16 on disk and quantizes to int8 **at load time**, so
the download is large and the resident set is roughly half of it, plus activations.

| Model | Disk (float16) | Weights @ int8 | Peak RSS (est.) |
|---|---|---|---|
| `whisper-large-v3-ct2` | **3.09 GB** | ~1.55 GB | ~2.0–2.5 GB |
| `whisper-large-v3-turbo-ct2` | **1.62 GB** | ~0.81 GB | ~1.3–1.8 GB |

A copy of the 3.09 GB `large-v3` artifact already exists on the target machine (a
2,945 MB `model.bin` from earlier local work); pointing `model_path` at it skips the
download. A CPU-only machine downloads the 1.62 GB turbo model and nothing else.

**Speed lever that matters more than the model choice:** the VAD filter means only actual
speech is transcribed. In a 45-minute meeting the `me` track might hold 10 minutes of
speech and `them` 30 — so ~40 minutes of audio goes through the model, not 90. Budget CPU
transcription at roughly **0.7–1.5× the meeting's length** with turbo+int8 on a modern
4–8 core laptop, and measure it properly in M0 rather than trusting that range.

### 20.3 Local Hebrew LLM — the real options

**DictaLM 3.0 shipped** (I checked — it's newer than the 2.0 most write-ups mention) and
DICTA publishes official GGUFs, which removes the usual "find a trustworthy quant" problem.

| Model | Base | Context | Q4_K_M | KV @20K ctx | Total RAM |
|---|---|---|---|---|---|
| `DictaLM-3.0-1.7B-Instruct` | Qwen3 | 62K | **1.11 GB** | ~1.1 GB (q8 KV) | **~3 GB** |
| `dictalm2.0-instruct` (7B) | Mistral | 32K | **4.37 GB** | ~2.6 GB (f16) | **~7 GB** |
| `DictaLM-3.0-Nemotron-12B-Instruct` | Nemotron-H | 131K | **7.49 GB** | small — hybrid | **~9 GB** |
| `DictaLM-3.0-24B-Thinking` | — | — | ~14 GB | — | ~17 GB+ |

Two notes that matter for this workload specifically:

- **Nemotron-H is a hybrid Mamba/Transformer.** Only a few layers are attention, so the KV cache barely grows with context — exactly the right property for a 20K-token transcript, where a conventional 7B spends ~2.6 GB just on KV. If it runs well under llama.cpp on your hardware, the 12B is the better long-context citizen despite being larger on disk.
- **Hebrew tokenization is the hidden cost.** Standard tokenizers spend 2–4 tokens per Hebrew word. DictaLM 2.0's expanded vocabulary (33,152 vs Mistral's 32,000) exists precisely to fix that, and it cuts prefill time proportionally. This applies to Claude too — measure real Hebrew token counts in M0 before trusting any cost estimate.

**Speed, honestly:** a 20K-token transcript on a 7B Q4 CPU model is roughly 4 minutes of
prefill (~90 tok/s) plus ~5 minutes generating a 2K-token summary (~6 tok/s) — call it
**~10 minutes per meeting**, or ~3 minutes on the 1.7B. Fine for a deferred nightly job;
not fine interactively. Claude does the same work in ~20 seconds.

### 20.4 System RAM guidance

The design's sequential-stages rule does the heavy lifting: **ASR and the LLM are never
loaded at the same time.** Peak RAM is the larger of the two stages, not their sum.

| System RAM | What's viable |
|---|---|
| **8 GB** | ASR (turbo, ~1.8 GB peak) + **Claude API** for summaries. A local LLM does not fit alongside Windows and a video call. |
| **16 GB** | Comfortable. ASR turbo or large-v3, plus a local 7B or the 12B for sensitive meetings, sequentially. |
| **32 GB** | Everything, including keeping a model resident between meetings to skip load time. |

Disk footprint for a fresh no-GPU install: **~1.6 GB** (ASR only) or **~2.7–9 GB** with a
local LLM, plus ~50 MB app and ~80 MB ffmpeg. Audio is the real consumer over time — a
45-minute meeting at 16 kHz mono ×2 tracks is ~85 MB, so the retention policy earns its keep.

### 20.5 Recommended defaults

- **Your desktop:** `large-v3-ct2` on CUDA int8 + Claude. Local LLM only for meetings flagged sensitive.
- **A no-GPU laptop:** `turbo-ct2` int8 + Claude, deferred to `after_meeting`. Ship with no local LLM at all — make it an opt-in download for people who want fully-offline mode.
- **Fully offline mode:** `turbo-ct2` + `DictaLM-3.0-Nemotron-12B-Instruct-Q4_K_M` on 16 GB+, or the 1.7B on 8 GB with visibly lower summary quality.
