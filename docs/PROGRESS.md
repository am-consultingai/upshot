# Progress

One line per phase: status, the command that proved it, date, deviations.
Statuses: **green** (proved here), **green-linux / windows-pending** (all
environment-independent tests green; `windows`/`audio_hw`/`gpu` tests written, collected
and skipped — see `docs/windows-run.md`), **blocked**.

## Environment

| Fact | Value |
|---|---|
| Probe | `ls /proc/sys/fs/binfmt_misc/WSLInterop` → **INTEROP_OFF** (2026-08-28) |
| Consequence | This shell cannot execute Windows binaries. Everything marked `windows`, `audio_hw` or `gpu` is written and collected but skipped here. |
| Toolchain | uv 0.10.11, CPython 3.13.12 (uv-managed), node 24.15.0 |
| Closing the gap | `docs/windows-run.md` — the exact command sequence from a Windows terminal |

## Phases

| Phase | Status | Proved by | Date | Notes |
|---|---|---|---|---|
| 0 — skeleton + harness | green | `ruff check . && mypy app && pytest -q && python -m app.selftest all` | 2026-08-28 | T1 speech fixture written; skipped off-Windows |
