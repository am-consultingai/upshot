# Decisions taken during implementation

Every judgment call the plan left open, what the alternatives were, and why.

## D1 — Windows-only and heavy dependencies are imported lazily, inside functions
**Choice.** `pyaudiowpatch`, `pywin32`, `pycaw`, `windows-toasts`, `pystray`,
`faster_whisper`, `anthropic` and `keyring` backends are imported inside the function that
needs them, never at module scope.
**Alternatives.** Module-scope imports with try/except at the top; a compatibility shim
package.
**Why.** Phase 0's `test_imports_all_modules` walks `app/**` and imports every module. That
test is the tripwire for circular imports and typos, and it has to pass on Linux, in CI, and
inside a PyInstaller freeze. Lazy imports are the only way every module stays importable
everywhere without weakening that assertion.

## D2 — `/etc/wsl.conf` was not modified
**Choice.** Interop stays off; `docs/windows-run.md` carries the exact enabling steps and
the command sequence for the Windows-only gates.
**Alternatives.** `docs/next-session.md` §4.3 invites appending `enabled = true` under
`[interop]`.
**Why.** The session's operating instruction is that nothing outside the project folder may
be created, referenced, altered or deleted. `/etc/wsl.conf` is outside it, and enabling
interop requires a `wsl --shutdown` this shell cannot issue anyway, so the instruction costs
nothing here.

## D3 — Skips are reported as `ok` with a `skipped:` detail, not as a fourth check state
**Choice.** A self-test check that cannot run in this environment is emitted as
`{"ok": true, "detail": "skipped: <why>", "metrics": {"skipped": 1}}`.
**Alternatives.** A `"skipped"` key on the check object; a third value for `ok`.
**Why.** The report shape is fixed by `EXECUTION-PLAN.md` Phase 0 and must never change.
`metrics` is a free-form object by that same contract, so the skip is machine-readable
without touching the schema, and the suite's `ok` stays a straight conjunction.

## D4 — `ruff` never sees Markdown
**Choice.** `docs` and `*.md` are excluded in `[tool.ruff]`.
**Why.** `ruff format .` reformatted the Python code blocks inside `EXECUTION-PLAN.md` and
`TECHNICAL-DESIGN.md` on the first run. The design docs are the contract; a formatter must
not edit them. The two files were restored byte-for-byte and the exclusion makes it
impossible to repeat.
