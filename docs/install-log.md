# Install / file-creation log

Every install, uninstall, or deletion performed during implementation is recorded here.
All paths are inside the project folder unless stated otherwise. Nothing outside
`/home/am/projects/meeting-agent` was created, altered or deleted.

| Date | Action | Detail |
|---|---|---|
| 2026-08-28 | install | `uv python install 3.13` → CPython 3.13.12 into `~/.local/share/uv/python` (uv-managed toolchain, outside the project; required to run the build at all) |
| 2026-08-28 | install | `uv sync` → project venv at `./.venv` with the dependencies in `pyproject.toml` |
