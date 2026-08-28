# Tech stack

Chosen to keep the installer to one runtime, and biased toward libraries already proven
on this machine's toolchain. Every version below was checked against PyPI on
2026-08-28.

---

## Python 3.13 — the earlier 3.12 caution is resolved

I previously suggested pinning to 3.12 over wheel-availability worries. That's no longer
necessary — every critical package publishes cp313 Windows wheels:

| Package | Version | cp313 win_amd64 |
|---|---|---|
| `ctranslate2` | 4.8.1 | ✅ |
| `PyAudioWPatch` | 0.2.12.8 | ✅ |
| `soxr` | 1.1.0 | ✅ (abi3) |
| `onnxruntime` | 1.29.0 | ✅ |
| `faster-whisper` | 1.2.1 | pure Python |
| `anthropic` | 1.2.0 | pure Python |

So: **Python 3.13**, which you already have installed on Windows. `uv` for the venv and
lockfile, `ruff` for lint and format.

---

## Audio

| Concern | Choice | Why |
|---|---|---|
| Capture | **PyAudioWPatch** | The PyAudio fork that exposes WASAPI **loopback** — the whole reason we're Windows-native. cp313 wheels, no compiler needed |
| Resampling | **python-soxr** | WASAPI hands you 48 kHz stereo float32 (384 KB/s/track). Downsample to 16 kHz mono int16 at capture and a 45-min two-track meeting is ~85 MB instead of ~2 GB |
| Buffers, level meters | **numpy** | RMS per chunk for the live meters |
| Chunk files | stdlib **`wave`** | No dependency; a chunk is a plain 16 kHz mono WAV |
| Container work | vendored **ffmpeg.exe** | Bundled by the build script; used for concatenation and importing arbitrary formats |

Alternatives surveyed in `TECHNICAL-DESIGN.md` §4.0 — including why **ffmpeg cannot do
this** (no native WASAPI loopback on Windows; DirectShow only, which needs a virtual-cable
driver) and why no existing Python library covers mic + system capture. All alternatives
sit behind the same `AudioCapture` interface, so swapping is a day's work if PyAudioWPatch
disappoints.

## ASR

**faster-whisper 1.2.1 + ctranslate2 4.8.1**. The backend implements a CUDA probe, the
Windows lazy-cuBLAS DLL fix, a warmup inference and CPU fallback — full spec in
`EXECUTION-PLAN.md` Phase 5. Silero VAD comes bundled via onnxruntime. Models per
`DESIGN.md` §20.

## LLM

- **`anthropic` 1.2.0** — note the SDK is on **1.x** now. `claude-opus-5`, adaptive thinking, structured output via `output_config.format` (not the deprecated `output_format`), streaming for anything long.
- **Local fallback: Ollama's HTTP API**, not `llama-cpp-python`. Bundling llama.cpp into PyInstaller is genuinely painful, Ollama is already installed on your machine, and it's a plain HTTP call to `127.0.0.1:11434`. The cost is that offline mode requires Ollama installed separately — a fair trade for a feature most users won't enable.

## Backend

| Concern | Choice | Why |
|---|---|---|
| API + static serving | **FastAPI + uvicorn** | Serves the built React bundle in production; async-native for SSE |
| Database | **stdlib `sqlite3`** + a tiny ordered-migration runner | ~5 tables. SQLAlchemy + Alembic is more machinery than schema, and every dependency is PyInstaller surface area |
| Search | **SQLite FTS5** | Verify the Windows Python build has it: `sqlite3.connect(':memory:').execute("CREATE VIRTUAL TABLE t USING fts5(x)")` — one line, do it before designing around it |
| Scheduling | **APScheduler** | Calendar poll, `scheduled` worker policy, retention sweep |
| Worker | one background thread draining the `jobs` table | No Celery, no Redis, no broker. The queue is a table and the worker is a loop |

## Frontend

**React + Vite + TypeScript**.

- **TanStack Query** for server state. The UI is mostly "poll job status until it changes", which is exactly its sweet spot.
- **Tailwind** with `dir="rtl"` and logical properties (`ps-`/`pe-`, not `pl-`/`pr-`) so the Hebrew UI mirrors correctly instead of being patched per-component.
- **Timeline: hand-rolled CSS grid**, not a calendar library. FullCalendar and react-big-calendar bring drag-drop, recurrence, and timezone machinery for a view that is "past meetings in day columns". Not worth the weight or the RTL fight.
- **wavesurfer.js** for the transcript player — waveform plus click-a-line-to-seek is exactly what it's for.

> **The lighter alternative:** Jinja + htmx, server-rendered, no Node, no build step, a much
> smaller installer. Genuinely viable for a UI this modest. I'm recommending React anyway
> because your `build.ps1` already does Vite → PyInstaller and consistency across your two
> apps is worth more than the megabytes. If the installer size ever becomes the complaint,
> this is the lever.

## Rendering & delivery

- **Jinja2** → `summary.html`, with **two renderings**: the rich page for the local UI, and an email-safe variant with CSS inlined by **premailer** (Gmail's handling of `<style>` is unreliable).
- **Fonts: system stack only.** Segoe UI covers Hebrew. No Google Fonts — the page must render offline and inside an email client.
- **Email: stdlib `smtplib` + `email.message`.** Zero dependencies.

## Integrations & secrets

- **No Google packages in V1.** Calendar is out of scope; only the `EnrichmentSource` protocol ships (`EXECUTION-PLAN.md` Phase 6b), and a lint test asserts zero `google*` imports.
- **keyring** → Windows Credential Manager for the Anthropic key, SMTP app password, and OAuth refresh token.

## Tray & packaging

**pystray + Pillow** for the tray, **PyInstaller** one-dir, **Inno Setup** per-user
installer. Autostart via Task Scheduler at logon
(not a Startup shortcut: it restarts on failure). Not a Windows Service — Session 0 has no
audio devices.

## Testing

**pytest**, with a **fake ASR backend** so pipeline tests run without a GPU or a 3 GB
model, a short fixture WAV end-to-end, and golden-file tests on the renderer.

---

## Deliberately not using

| Rejected | Why |
|---|---|
| **Electron / Tauri** | The UI is a local web app in the browser. A second runtime doubles the installer for a native window we don't need. (Meetily uses Tauri because it wants one.) |
| **SQLAlchemy / Alembic** | More machinery than schema |
| **Celery / Redis / Docker** | The queue is a SQLite table. No external services, ever — it's a desktop app |
| **Node at runtime** | Vite emits static files; FastAPI serves them. Node is build-time only |
| **PyTorch** | ~2.5 GB install. Only diarization needs it — which is exactly why diarization is deferred |

---

## Repo layout

```
meeting-agent/
  pyproject.toml            uv-managed, cp313
  app/
    main.py                 FastAPI + startup wiring
    tray.py                 pystray launcher, PyInstaller entry point
    config.py               .env + app_config.json + keyring
    db/                     schema.sql, migrations/, dao.py
    audio/
      capture.py            AudioCapture interface
      wasapi.py             PyAudioWPatch implementation
      chunker.py            resample, write, manifest
    asr/
      transcriber.py        CUDA probe, warmup, CPU fallback
      backends.py           local-gpu | local-cpu | remote
    pipeline/
      states.py             the state machine
      worker.py             queue drain loop + policies
      assemble.py           two-track merge → speaker-tagged timeline
      summarize.py          Claude / Ollama, schema'd
      render.py             Jinja2 → html (+ premailer for email)
      deliver.py            smtplib
    calendar/               M2 — google.py, ics.py behind one interface
    glossary.py
  templates/summary.html.j2
  prompts/*.md
  frontend/                 React + Vite + TS
  packaging/                sHaRe-style .spec, installer.iss, build.ps1
  tests/
```
