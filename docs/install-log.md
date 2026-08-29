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
\n| 2026-08-29 | install | `uv sync --extra diarization` → `sherpa-onnx` + `sherpa-onnx-core` into `./.venv` (opt-in extra, ~19 MB) |\n| 2026-08-29 | download | diarization ONNX models (~37 MB) into the session scratchpad for testing, plus two public reference recordings from the k2-fsa GitHub releases. Nothing was written to the app home |\n