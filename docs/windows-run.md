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
- Recordings land in `%LOCALAPPDATA%\upshot\meetings`.
- Detection is off: nothing records until you press Start.

---

## 1. One-time setup on Windows

```powershell
cd $HOME\projects\upshot            # or wherever this repo is checked out
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
npm test                       # vitest unit tests
npm run build                  # FastAPI serves frontend\dist
npx playwright install chromium
npm run e2e                    # ~110 Playwright specs against the built bundle
cd ..
```

**Expected:** all green. `npm run e2e` starts the backend itself
(`.venv\Scripts\python scripts\e2e_server.py` via the `webServer` block — set
`UP_E2E_PYTHON` if your venv lives elsewhere).

---

## 2. The standard per-phase gate

```powershell
uv run ruff check .
uv run mypy app
uv run pytest -q
uv run python -m app.selftest all --report selftest-windows.json
```

**Expected:** ruff and mypy clean; `pytest` green; `selftest all` prints `all: OK`
and exits 0. Skips are expected, each with its reason in `pytest -rs`: on a Windows
developer machine without a local model or frozen build (machine B, job 007) there
were 38 — POSIX-only fake CLIs (shebang scripts), `chmod` tests that Windows cannot
express, no `dist\upshot\upshot.exe`, no `sherpa_onnx`, no `asr.model_path`, the
opt-in soak (`UP_SOAK_HOURS`), `live_api` and `gpu`. Any other skip is a finding.

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
powershell -ExecutionPolicy Bypass -File packaging\build.ps1 [-Sign] [-SkipInstaller]
uv run pytest -q tests/e2e/test_phase13_frozen.py
```

**Expected:** `dist\upshot\upshot.exe`, `dist\Upshot-<version>-Setup.exe` and its
`.sha256`. The build stamps the version (pyproject) and commit into the freeze
(`app/build_info.json`, shown in `/api/status` as `build` and on the first line of
`app.log`), and runs `--selftest imports` and `--selftest pipeline` against the freeze,
failing if either does. The first is the PyInstaller hidden-import tripwire and must
never be skipped before shipping a build. `upshot.exe` is windowed, so run a selftest
by hand with `Start-Process -Wait` and `--report <file>`: it prints nothing.

`-Sign` signs `upshot.exe`, Setup and the uninstaller Inno generates
(`packaging\sign.ps1`, SHA-256, timestamped) with the code-signing certificate
`CN=Upshot test signing` in `Cert:\CurrentUser\My`. It is self-signed for now; its
private key never leaves the build machine, and only the public `.cer` travels.

Keep `build.ps1`, `sign.ps1` and `installer.iss` ASCII: Windows PowerShell 5.1 and
Inno read a file without a BOM in the ANSI code page, and one em dash made the whole
build script fail to parse.

First run on a clean profile:

```powershell
dist\upshot\upshot.exe --bootstrap
```

prints the first-run report as JSON (profile, model, schema version, logon task).


## Where files go, and removing it all

Nothing is written into the source folder. The launcher keeps every Windows-side
artifact in `%LOCALAPPDATA%\upshot-win` (override with `-WorkDir`), and your
recordings in `%LOCALAPPDATA%\upshot`. Nothing touches PATH, the registry or
Program Files.

| What | Where | Size |
|---|---|---|
| uv (the package manager) | `<work>\bin\uv.exe` | ~20 MB |
| Python interpreter | `<work>\python\` | ~110 MB |
| Dependencies | `<work>\venv\` | ~600 MB |
| Package cache | `<work>\cache\` | ~600 MB, safe to delete any time |
| Byte-code cache | `<work>\pycache\` | small |
| Recordings, transcripts, database | `%LOCALAPPDATA%\upshot\` | grows with use |

(Sizes measured from the Linux environment; Windows differs somewhat.)

This matters most when the source folder is a WSL path shared with a Linux checkout:
a 600 MB Windows venv does not belong in it, and writing that much over the
`\\wsl.localhost` bridge is slow and can fail on file locking. Keeping the runtime on
a local Windows disk means only the application's own source files are read across
the bridge.

Five settings do the work, all applied before uv runs: `UV_PROJECT_ENVIRONMENT`,
`UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR`, `UV_TOOL_DIR` and `HF_HOME`, plus
`PYTHONPYCACHEPREFIX` so the interpreter does not leave `__pycache__` behind.
`uv sync --frozen` guarantees the lockfile is read and never rewritten. uv itself is
fetched as the release zip (SHA-256 verified) rather than through winget, because
winget installs machine-wide and puts a shim on PATH.

**The one exception**: building the web UI runs `npm`, which requires `node_modules`
beside `package.json`. If `frontend/dist` is missing the script says so and asks before
building; declining leaves you with a placeholder page. Both folders are gitignored.

To remove everything:

```powershell
.\run-app.ps1 -Uninstall
```

It deletes the runtime folder after listing it, and asks separately about the data
folder so recordings are never removed by surprise.


## Starting it: one command, no flags

`run-app.cmd` is the whole interface. Double-click it. Before starting it:

1. **Stops any instance still running.** Two copies is the failure that cost a day: the
   older one keeps the socket and shadows the newer, so the app you are looking at is
   not the app you just launched. There is never more than one.
2. **Picks a port that is actually free** — the requested one (8000 by default), then
   8010-8040. It probes by connecting to `127.0.0.1`, not by asking Windows, because a
   Linux listener inside WSL (a Docker container, say) answers there while Windows
   reports the port as free. On this machine `billers4-backend-1` publishes 8000, so the
   app lands on 8010 and says so.

`-Port` still exists to express a preference, but nothing requires it: the script will
move off a busy port on its own rather than failing.


## Stopping it

**Ctrl+C stops the app and closes the window.** Nothing asks for confirmation, because
Ctrl+C is already the instruction.

Getting there took two changes. The script used to end with *"Press Enter to close this
window"*, which turned every deliberate stop into a second keypress; it now only holds
the window open when the app failed to start and there is an error worth reading.

And `run-app.cmd` no longer hosts the app in its own console. A batch file interrupted
with Ctrl+C makes cmd.exe ask *"Terminate batch job (Y/N)?"*, and that prompt cannot be
suppressed from inside the batch file — so the launcher starts the app in its own console
and exits immediately, leaving no batch file to ask. The window you double-click closes
at once; the app runs in the window that opens next to it.
