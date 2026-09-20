# Current state

Where this repository stands on **2026-09-06**. Written to be read cold, by someone who
was not here.

Open problems live in `known-issues.md`; interface gaps against the competition live in
`UX-PRIOR-ART.md`. This file is what the thing *is* and what has actually been run.

The first version of this file described a build that had never executed on Windows. The
second described one that had never made a real LLM call. **Both are now false.** On
2026-09-06 the application produced its first real summaries: a Hebrew meeting, summarized
by Claude Code on the author's own subscription, rendered and filed. Most of what follows
was learned by running it on Windows, and most of the bugs in `known-issues.md` were found
that way rather than by tests.

- **What it is:** a local Windows application that records both sides of a meeting,
  transcribes it locally, summarizes it with an LLM, renders HTML and files it in a
  browsable timeline. No cloud service is required. A Google account can be connected, for
  the calendar only, and read-only: it names recordings and tells the app who was there.
- **Design contract:** `DESIGN.md`, `TECHNICAL-DESIGN.md`, `DETECTION.md`, `STACK.md`,
  `SECURITY-AND-AUTH.md`. Every judgment call is in `DECISIONS.md` (46 entries).
  Phase-by-phase status is in `PROGRESS.md`. The Windows runbook is `windows-run.md`.

---

## 1. Status at a glance

| | |
|---|---|
| Phases 0–14 | complete |
| Python suite | **436 passed, 25 skipped, 5 deselected** (`live_api`) |
| Frontend | **31 vitest** unit tests, **26 Playwright** e2e tests |
| `ruff` / `mypy --strict` | clean, 90 modules |
| M0 / M1 / M2 selftests | green (M1 in synthetic capture mode — §5) |
| Size | 14.9k lines of app, 7.9k lines of tests, 100 config keys |
| Runs on Windows | **yes**, from source via `scripts/windows/run-app.cmd` |
| Real transcription | **yes** — ivrit-ai large-v3 on CUDA, Hebrew, `device=cuda compute=int8` |
| Real summarization | **yes** — Claude Code on the author's own subscription, Hebrew, on a real meeting |
| Summary format | **free-form**: the prompt writes the document; the app imposes only a JSON envelope |
| Echo cancellation | **run on Windows** on a real leaking recording with the real GPU model |
| Retention sweep | **run on Windows**, including a delete that could not complete |

Gate, which every change passes:

```bash
uv run ruff check . && uv run mypy app && uv run pytest -q && uv run python -m app.selftest all
```

---

## 2. What changed since the phases closed

Grouped by what a reader would care about, not chronologically.

### Audio storage: one file per track

Recording used to write a chunk file per minute per track. It now writes **one growing WAV
per track** — `audio/me.wav` and `audio/them.wav` — appended to as the meeting runs, with
the RIFF sizes rewritten after every committed segment. The file is a valid, playable WAV
at every moment; this was verified mid-recording (50.1 s readable while recording
continued). `manifest.jsonl` became an *index* rather than a directory listing: one line
per committed segment carrying its sample `offset`. Lost audio is written as real silence,
so the file's timeline **is** the meeting's timeline. Transcription is now one pass per
track instead of one per chunk. Full reasoning in `DECISIONS.md` **D34**.

### Playback: one stream, mixed on read

`GET /api/meetings/{id}/audio` defaults to `track=mix` — both tracks summed into one mono
stream, synthesised as the request is served. Nothing extra is stored, and because both
tracks are mono 16-bit PCM on the same timeline, a byte range maps 1:1 onto both files, so
seeking stays cheap. Verified sample-identical to `me + them` against a real recording,
with 300 random ranges (including odd offsets) matching exactly. **D35**.

### Settings: device pickers and live meters

The Settings screen now selects **both** endpoints — microphone (`audio.input_device`) and
the playback device whose loopback becomes the `them` track (`audio.output_device`) — each
with a live level meter beside it. The meter is built through `make_capture`, so what it
shows comes through the same path a recording would use. dB scale (speech peaks near 0.05
linear, which would be one segment out of 24), peak-hold with decay, and silence stated in
words rather than implied. **D33**.

### Google Calendar

Connected in Settings with loopback OAuth and PKCE; the refresh token lives in Windows
Credential Manager. The events of the next few hours are polled every minute and thirty
days either side every ten, into a local `calendar_events` cache that is safe to delete.
What the calendar buys: recordings take the meeting's name and its attendees, the calendar
view shows real events behind the recordings, and an event on now adds 3 to the detector's
score — never enough on its own to start a recording. Attendee email addresses and event
descriptions are dropped before anything is stored, and Settings says so.

Run against a real account end to end on 2026-09-20: 42 real events cached, a live
recording matched to the meeting that was on, named from it and given its attendees.

### Main screen: calendar view

A **List / Calendar** toggle, and within the calendar **Day / Week / Month**. Day and week
share a time-grid that reuses the existing tested overlap packer; month is a cell grid. All
date arithmetic is pure functions in `frontend/src/lib/calendar.ts`, covered by 14 unit
tests including DST, year boundaries and a month starting on Sunday. Labels come from
`Intl.DateTimeFormat`, so Hebrew is correct with no translation table. The choice persists
as `ui.view` / `ui.calendar_span`.

### Crosstalk detection, and its removal

A microphone endpoint that also carries system audio records the far side twice, and
nothing downstream can tell that from two people saying the same thing. The transcribe
stage measures correlation between the tracks, logs it, and above **0.85** adds a review
reason. Measured on real recordings: a clean one scores 0.000, the leaking one 0.974.
**D36**.

That leak is now **removed** as well. It is not an acoustic echo — it never leaves the
machine — so it is a digital copy of `them` at one gain and one delay. Fitted by least
squares on the 60 s window where the far side is loudest, then subtracted in the two
places it matters: the ASR input (`audio/clean/me.wav`, written, transcribed, deleted)
and the playback mixer (on read, at a shifted offset, so range requests stay cheap).
Measured on the author's recordings, one tap removes **94.8%** and **96.0%** of the near
track's energy — 99% inside the windows where the far side is actually talking — and an
8-, 32- or 128-tap filter was measured to buy at most two further points. The recording
itself is never modified; `?track=me` still serves exactly what the device produced.
`audio.echo_cancel` is `auto | on | off`. **D37**.

### Retention finally sweeps

`retention.audio_days` (30) now deletes something. The raw WAVs go; the transcript, the
summary and the meeting row stay. `retention.transcript_days` (`null`) removes a meeting
outright and is off by default. Nothing is swept while it is recording, while a job for it
is queued, or before a transcript exists — until then the audio *is* the meeting — and
every refusal is reported by name. `GET /api/retention` shows what the policy would do
without doing it, `POST /api/retention/sweep` runs it now, and the worker runs it when the
queue is idle, at most every `retention.sweep_hours` (6). The meeting page says the
recording was deleted on purpose rather than showing an empty player. **D38**, closing
**D27**.

### Deleting a recording

`DELETE /api/meetings/{id}` removes the folder and the row (jobs and indexed turns cascade),
with a **Delete** action on each card. It refuses a meeting that is currently recording,
and refuses any folder that is not inside the data root — the folder path comes from the
database and must never be able to aim the delete somewhere else.

### The Windows launcher

`scripts/windows/run-app.cmd` is the whole interface. It stops any previous instance,
picks a port that is actually free (probing `127.0.0.1`, because a Linux listener inside
WSL answers there while Windows reports the port free), prints the resolved configuration
before starting, and confines every download to `%LOCALAPPDATA%\upshot-win` —
nothing is written into the source tree and nothing touches PATH. `-Uninstall` removes it
all. Ctrl+C stops the app with no prompt. See `windows-run.md`.

---

## 3. Bugs found by running it on Windows

Each of these was invisible to the test suite as it stood, and each now has a test.
The last row was found this way too — by running the new sweep on Windows rather than
by reasoning about it.

| Bug | Cause |
|---|---|
| Process vanished mid-meeting, no traceback | Use-after-free in PortAudio: the writer thread called `is_active()` on a stream the stop path was closing. A native access violation, so no `except` could catch it. Fixed with a per-stream lifecycle lock plus a process-wide PortAudio lock |
| Transcription died with `'str' object has no attribute 'dtype'` | `WhisperModel.detect_language` needs a float32 array, not a path. The test stub had modelled the wrong contract |
| Recording failed with `-9999` | The Settings meter held the microphone; recording could not open it. The meter now yields, and the open retries across the handover |
| The microphone was reopened ~4×/second | A React effect depended on `t`, which is a fresh function every render. Guarded now by a counter and a browser test |
| An idle Settings tab reopened the mic every 5 minutes | A server-side cap that `EventSource` simply reconnected around. The browser now owns the meter's lifetime |
| Closing a tab never released the microphone | `await asyncio.to_thread(release)` inside a `finally` — awaiting in a cancelled task raises before the release runs |
| A stale instance shadowed a new one for two days | `uvicorn` had no `timeout_graceful_shutdown`, so an open SSE stream blocked shutdown forever after the port was released |
| Notification buttons never appeared | `WindowsToaster` does not support actions; it warns and drops them. Since the buttons *are* the detector's learning mechanism, this had silently disabled it |
| Toasts failed with `RPC_E_WRONG_THREAD` | WinRT objects belong to their creating thread's apartment; one toaster was shared across threads |
| Every fixture meeting looked like a broken machine | `write_chunks` seeded both tracks identically, so all fixtures had perfect crosstalk |
| The retention sweep reported deleting audio it had not deleted | `shutil.rmtree(ignore_errors=True)` cannot remove a file Windows has open. It removed one track, swallowed the error, and the mark was written anyway — so the meeting was recorded as swept, with half its audio still on disk and no retry. Linux unlinks open files, so nothing local could show it |

---

### Summaries became real, and free-form

The first real LLM call in this project's life was made on 2026-09-06, through **Claude
Code on the author's own subscription** (`llm.provider = claude-subscription`). The
application never holds a credential: it spawns the CLI the user signed into themselves.
Settings detects the install, offers to install it (Anthropic's own installer, in a visible
console that then carries straight on into the login), and reports whether it is signed in.

Getting there took four distinct faults in a row, each hiding the next — encoding, prompt
placement, a worker that abandoned the pipeline, and a page that cached a 404. All are in
`known-issues.md` under "Fixed on 2026-09-06", because the pattern is the lesson: every one
presented as silence rather than as an error.

Then the summariser was **replaced entirely**. The old one made the model fill a nine-field
schema (`NOTES_SCHEMA`) which a Jinja template laid out into six fixed sections. That meant
an edited prompt could change wording but never structure — which is not what a prompt
editor implies. Removed: the schema, the map/reduce pipeline, `prompts/map.md`,
`prompts/reduce.md`, `templates/`, the sanity gates, `NEEDS_REVIEW`, and the jinja2 and
premailer dependencies.

What remains imposed is one JSON envelope, `{summary_html, title?}`, kept only because it
is what makes an answer extractable across providers. Everything inside is the prompt's:
sections, order, headings, layout, inline CSS. Two things are stripped from the HTML before
it reaches the page — `<script>` and `on*=` handlers — and nothing else.

The prompt itself is editable in Settings, in full, and the summary records which prompt
produced it (`prompt_versions.system = custom:<hash>`), so a summary can always say what
made it. Editing the prompt and pressing Summarize redoes the work with no staleness check.

### Importing a transcript recorded elsewhere

`app/transcript_import.py` brings in text and audio produced somewhere else as a finished
meeting, landing in `TRANSCRIBED` with nothing queued — the transcribe and assemble stages
are skipped rather than run. Timestamps are spread across the recording in proportion to
line length, which is a guess but a defensible one. Speakers cannot be recovered from a
plain-text export, so every turn is labelled `THEM`; attribution in summaries of imported
meetings is unreliable by construction.

---

## 4. How to run it

**Windows (the real thing):** double-click `scripts\windows\run-app.cmd`. Everything else
is in `docs/windows-run.md`.

**Linux, with fakes:** `bash scripts/demo.sh` (override the port with `PORT=8055`).

```bash
# headless
uv run python -m app.main
# fakes end to end
UP_AUDIO__CAPTURE='"synthetic"' UP_ASR__BACKEND='"fake"' UP_LLM__PROVIDER='"fake"' \
UP_AUDIO__VAD='"energy"' uv run python -m app.main
```

The frontend must be built once (`cd frontend && npm ci && npm run build`).

---

## 5. What is still **not** proved

- **Neither D37 nor D38 has run during a live recording.** Both have now run on Windows
  against real audio and the real GPU model, driven directly rather than through the UI.
  What has not happened is a meeting recorded, echo-cancelled and swept end to end while
  the app runs normally.
- **Only one provider has made a real call.** `claude-subscription` has now summarized a
  real Hebrew meeting several times. Anthropic, OpenAI, Gemini and Ollama remain tested
  only against injected transports; the free-form path in particular has never run against
  any of them.
- **Phase 4's stop condition has never been evaluated.** `test_dual_stream_concurrent`
  decides whether a capture and a loopback stream survive together for hours. In practice
  both streams have run together for minutes at a time without trouble, which is
  encouraging but is not the test.
- **The microphone signal the detector is built on is only half verified.** A probe
  (`scripts/windows/probe-mic.py`) established on Windows that the ConsentStore follows
  the host API rather than the device — WASAPI and DirectSound register, MME does not —
  and that `voicemeeter.exe` holds the microphone continuously on this machine. What is
  still unanswered is `DETECTION.md` §7's central claim that **muting does not release
  the stream**, which the release grace and the whole end-of-meeting path assume. The
  protocol is in `manual-checks.md`; it needs a real call.
- **M1 still runs in synthetic capture mode** rather than playing a fixture out a real
  render endpoint.
- **No frozen build exists.** `dist/` has never been produced; there is no installer.
- **Diarization has never run on this machine** (`asr.diarization = off`).
- **Hebrew transcription quality is unmeasured.** It produces plausible Hebrew; nobody has
  scored it.

---

## 7. Reading across meetings (2026-09-20)

A product review, done in the character of a product manager in six or seven meetings a
day, found the core loop good and the product unadopted for a reason none of the interface
work had touched: **nothing read across meetings.** Seven well-written documents, and no
way to answer "what did I promise this week, and to whom".

What shipped in response:

- **Action items as data.** The envelope is now `{summary_html, title?, action_items?[]}`.
  The model still writes whatever document it likes and then repeats the commitments in it;
  nothing rearranges `summary_html`. Stored in an `action_items` table, optional and
  forgiving, and `done_at` is the user's and survives a re-summarize. **D47**, closing
  `known-issues.md` #10.
- **An inbox across meetings** at `/actions`, grouped by owner with mine first, checkable
  in place. The empty detail pane on the library screen shows what is still open instead of
  a sentence.
- **Capture stops being decided in silence.** `detection.mode` still defaults to `shadow`
  — watching, never recording — but `detection.decided` is false until the user has been
  asked, and the library asks. An install that reaches its second day recording nothing by
  accident was the failure worth preventing.
- **Search answers with the sentence.** `GET /api/search` returns hits — the matching line
  with the term marked, the speaker, the moment — and a result lands on that moment.
- **Failures speak to the user.** "summarize failed after attempts (0)" became what
  stopped, that the recording is safe, and what the button will do, with the trace behind a
  disclosure. The invitation line no longer appears on healthy meetings.
- **Markdown export**, beside Copy. SMTP delivery stays deferred: `app/mail.py` implements
  it and nothing in the frontend calls it.
- **The calendar opens on the working day** rather than at 00:00, and opening it no longer
  silently range-filters the meeting list beside it.

Not done from that review: speaker names in place of `ME`/`THEM`.

---

## 6. Known issues

Moved to `known-issues.md` on 2026-09-06, and extended there with everything this session
turned up. Fifteen open entries, ordered by what I would close first: no first-run setup,
no frozen build, and an interface well behind the category (`UX-PRIOR-ART.md`).
