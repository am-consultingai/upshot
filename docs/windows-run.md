# Running the Windows-only gates

This repository was implemented from a **WSL2 Ubuntu** shell with Windows interop **off**
(`/proc/sys/fs/binfmt_misc/WSLInterop` absent, `/etc/wsl.conf` has
`[interop] appendWindowsPath = false`). That shell can read and write `/mnt/c/...` but
cannot execute Windows binaries, so every test marked `windows`, `audio_hw` or `gpu` is
written and collected there but **skipped**. This file is how those gates get closed.

---

## 0. (Optional) enable WSL interop, so one shell can do both

Append to `/etc/wsl.conf` (requires sudo, **outside** the project folder — not done by the
implementing session):

```ini
[interop]
enabled = true
appendWindowsPath = false
```

Then, from a **Windows** terminal (PowerShell or cmd), restart WSL:

```powershell
wsl --shutdown
```

After that a WSL shell can invoke `/mnt/c/Users/am/AppData/Local/Programs/Python/Python313/python.exe`
directly and the sequence below can be driven from either side.

---

## 0. Run the application: double-click one file

```
scripts\windows\run-app.cmd
```

Double-click it. It works out **everything that needs downloading and asks once, up
front**, then installs, starts the app with real two-track capture and real local
transcription, and opens the browser. You drive it from there: Start, talk, play audio
through the speakers, Stop, read the transcript.

The summarizer is a placeholder unless you ask for a real one, so **no API key is needed**
to test recording and transcription.

```powershell
# a different model folder, a real summarizer, and the loopback probe first
powershell -ExecutionPolicy Bypass -File scripts\windows\run-app.ps1 `
  -ModelPath 'D:\path\to\ivrit_model' -Provider gemini -CheckAudio
```

Notes:

- Dependencies install into **`.venv-win`**, not `.venv` — the latter is a Linux
  environment if this repo came from WSL, and `uv sync` would otherwise replace it.
- Recordings land in `%LOCALAPPDATA%\meeting-agent\meetings`.
- Detection is off: nothing records until you press Start.

---

## 1. One-time setup on Windows

```powershell
cd $HOME\projects\meeting-agent      # or wherever this repo is checked out
winget install --id=astral-sh.uv -e  # if uv is not installed
uv sync
```

`uv sync` installs the Windows-only dependencies (`PyAudioWPatch`, `pywin32`, `pycaw`,
`comtypes`, `windows-toasts`, `pystray`) that are excluded by environment marker on Linux.

---

## 1b. The frontend

```powershell
cd frontend
npm ci
npm run typecheck
npm test                       # 17 vitest unit tests
npm run build                  # FastAPI serves frontend\dist
npx playwright install chromium
npm run e2e                    # 15 Playwright tests against the built bundle
cd ..
```

**Expected:** all green. `npm run e2e` starts the backend itself
(`.venv\Scripts\python scripts\e2e_server.py` via the `webServer` block — set
`MA_E2E_PYTHON` if your venv lives elsewhere).

---

## 2. The standard per-phase gate

```powershell
uv run ruff check .
uv run mypy app
uv run pytest -q
uv run python -m app.selftest all --report selftest-windows.json
```

**Expected:** ruff and mypy clean; `pytest` green with **zero** skips other than
`live_api` (which needs an Anthropic key) and `gpu` (which needs CUDA);
`selftest all` prints `all: OK` and exits 0.

To run only what this Linux session could not:

```powershell
uv run pytest -q -m "windows or audio_hw"
```

---

## 3. The hardware gates, in order

| Gate | Command | Expected |
|---|---|---|
| Audio spike (Phase 4) | `uv run python -m app.selftest audio --report docs\spike-report.json` | `audio: OK`; cross-correlation ≥ 0.8, offset < 500 ms, xruns 0 |
| Dual-stream stability | `uv run pytest -q tests/e2e/test_audio_hw.py -k dual_stream` | passes; on failure descend the fallback ladder in `TECHNICAL-DESIGN.md` §4.0 |
| 2-hour soak (optional) | `uv run pytest -q -m "audio_hw and slow" -k soak` | drift ≤ 1 s/hour, 0 xruns |
| Detector, real signals (Phase 12) | `uv run python -m app.selftest detect-shadow --seconds 60 --report shadow.json` | `detect-shadow: OK`, events written, nothing committed |

---

## 4. The three milestone gates (Phase 14)

```powershell
uv run python -m app.selftest pipeline    --input tests\fixtures\meeting_10min.wav --report m0.json
uv run python -m app.selftest capture-e2e --seconds 120                            --report m1.json
uv run python -m app.selftest detect-e2e                                           --report m2.json
```

M0 and M2 run anywhere (fakes end to end). **M1 runs anywhere too, but in one of two
modes**, and the report says which:

| Mode | When | What it proves |
|---|---|---|
| `synthetic` | no WASAPI render endpoint (this WSL2 shell, CI) | the API round trip, chunking and durations, the manifest, both tracks, the `them`-track correlation against the fixture, language pinning, the tray sequence, and the pipeline to `RENDERED` |
| `wasapi` | a Windows host | all of the above **plus** the real render endpoint, the real loopback stream, and the real capture device |

So on Windows the same command closes M1's hardware half:

```powershell
uv run python -m app.selftest capture-e2e --seconds 120 --report m1.json
```

Expect `capture_mode: wasapi capture` in the report. With a local ASR model configured
(`asr.backend = "local"`), `capture_transcript_words` also stops reporting `skipped` and
asserts the spoken fixture words survive into the transcript.

The M0 input is generated, never committed:

```powershell
uv run python scripts\make_fixture.py --minutes 10
```

---

## 5. Frozen build (Phase 13)

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
dist\meeting-agent\meeting-agent.exe --selftest imports
dist\meeting-agent\meeting-agent.exe --selftest pipeline
uv run pytest -q tests/e2e/test_phase13_frozen.py
```

**Expected:** both `--selftest` runs exit 0, and the frozen tests stop skipping once
`dist\meeting-agent\meeting-agent.exe` exists. The first is the PyInstaller
hidden-import tripwire and must never be skipped before shipping a build.
`build.ps1` runs both itself and fails the build if either does.

First run on a clean profile:

```powershell
dist\meeting-agent\meeting-agent.exe --bootstrap
```

prints the first-run report as JSON (profile, model, schema version, logon task).
