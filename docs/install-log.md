# Install / file-creation log

Every install, uninstall, or deletion performed during implementation is recorded here.
All paths are inside the project folder unless stated otherwise. Nothing outside
`/home/am/projects/meeting-agent` was created, altered or deleted.

| Date | Action | Detail |
|---|---|---|
| 2026-08-28 | install | `uv python install 3.13` → CPython 3.13.12 into `~/.local/share/uv/python` (uv-managed toolchain, outside the project; required to run the build at all) |
| 2026-08-28 | install | `uv sync` → project venv at `./.venv` with the dependencies in `pyproject.toml` |
| 2026-08-29 | install | `npm install` in `frontend/` → 187 packages into `frontend/node_modules` (React, Vite, TanStack Query, Tailwind, Vitest, Playwright) |
| 2026-08-29 | install | `npx playwright install chromium` → Chrome Headless Shell 151 into `~/.cache/ms-playwright` (outside the project; needed to run the e2e gate here) |
\n| 2026-08-29 | install | `uv sync --extra diarization` → `sherpa-onnx` + `sherpa-onnx-core` into `./.venv` (opt-in extra, ~19 MB) |\n| 2026-08-29 | download | diarization ONNX models (~37 MB) into the session scratchpad for testing, plus two public reference recordings from the k2-fsa GitHub releases. Nothing was written to the app home |\n| 2026-08-30 | delete | `scripts/windows/test-recording.ps1` and `.cmd`, replaced by `run-app.ps1`/`.cmd` — the purpose changed from running a test to running the application |\n| 2026-08-30 | download | PowerShell 7.4.6 for linux-x64 into the session scratchpad, to syntax-check the Windows script and execute its env-var assignments against `Config.load()`. Outside the project; not installed system-wide |\n
## 2026-08-30 — Python 3.12 compatibility check
- Created a throwaway venv at `$SCRATCH/venv312` (Python 3.12.3, outside the repo) and
  installed the project's main + dev dependency set into it, to test whether
  `requires-python = ">=3.13"` is a real constraint. Nothing in the repo was installed
  to or removed from. The scratch venv is disposable.

## 2026-09-01 — Windows launcher made self-contained
- `scripts/windows/run-app.ps1` no longer installs uv via winget (machine-wide, PATH
  shim). It downloads the release zip, verifies its SHA-256, and keeps `uv.exe` in
  `<repo>\.uv-bin`, invoked by full path.
- `UV_PYTHON_INSTALL_DIR`, `UV_CACHE_DIR`, `UV_TOOL_DIR` and `HF_HOME` now point inside
  the repo / app home, so no downloads land in `%LOCALAPPDATA%\uv` or `~/.cache`.
- Added `-Uninstall`, which deletes only the folders the script creates, after asking,
  and asks separately before touching the user's recordings.
- Nothing is written to PATH or the registry by any path through this script.
- Follow-up: all Windows artifacts moved out of the source tree into `-WorkDir`
  (default `%LOCALAPPDATA%\upshot-win`). Added `PYTHONPYCACHEPREFIX`, switched
  to `uv sync --frozen`, moved the audio probe's report out of `docs\`, and made the
  npm build ask first since it is the only step that must write in place.
log: killed stale Linux app.main (pids 49716, 49726), started Aug 30 by scripts/demo.sh
log: SIGKILLed leftover app.main (49716/49726) — SIGTERM released the port but did not exit
log: deleted 3 test meetings and their DB rows at the user's request (2026-09-02)
log: removed app/audio/stitch.py, app/audio/merge.py and test_audio_stitch.py — the single-file writer makes reassembly unnecessary
- 2026-09-02: run-app.ps1 now stops any running instance and auto-selects a free port
  (probing 127.0.0.1 so WSL/Docker listeners are seen). Removed the "Start anyway?"
  prompt, which only ever led to a bind failure.
- 2026-09-03: Ctrl+C now stops the app outright. Removed the unconditional "Press Enter
  to close" prompt (kept only when startup failed), and run-app.cmd launches the app in
  its own console so cmd.exe's un-suppressable "Terminate batch job (Y/N)?" never fires.
