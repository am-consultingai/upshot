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

## 1. One-time setup on Windows

```powershell
cd $HOME\projects\meeting-agent      # or wherever this repo is checked out
winget install --id=astral-sh.uv -e  # if uv is not installed
uv sync
```

`uv sync` installs the Windows-only dependencies (`PyAudioWPatch`, `pywin32`, `pycaw`,
`comtypes`, `windows-toasts`, `pystray`) that are excluded by environment marker on Linux.

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

M0 also runs on Linux (it is fakes-only end to end). **M1 and M2 are Windows-only** — M1
needs a real render endpoint and a real capture device; M2 needs the microphone
ConsentStore.

---

## 5. Frozen build (Phase 13)

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
dist\meeting-agent\meeting-agent.exe --selftest imports
dist\meeting-agent\meeting-agent.exe --selftest pipeline
```

**Expected:** both exit 0. The first is the PyInstaller hidden-import tripwire and must
never be skipped before shipping a build.
