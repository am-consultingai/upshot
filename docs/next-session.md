# Next session — operating brief

You are implementing the Meeting Agent from a completed design. **Everything you need is
in `docs/`. Nothing is left to decide.** Build it.

---

## 1. Mission

Implement `docs/EXECUTION-PLAN.md` in full — Phase 0 through Phase 13, then the milestone
gates in Phase 14 — until every test in every phase passes.

**Work autonomously. Do not ask for permission. Do not stop to confirm decisions. Do not
pause between phases for approval.** The design is settled; the plan is the contract. When
something is ambiguous, the answer is in the docs; if it genuinely is not, pick the option
most consistent with the surrounding design, record the choice in `docs/DECISIONS.md`, and
keep going.

---

## 2. Read first, in this order

| Doc | Why |
|---|---|
| `docs/EXECUTION-PLAN.md` | **The contract.** Phases, deliverables, exact tests, exit criteria, operating rules |
| `docs/TECHNICAL-DESIGN.md` | The spec: threading, schema, algorithms, API, config |
| `docs/DESIGN.md` | Architecture and the *why* behind constraints you must not violate |
| `docs/DETECTION.md` | The detection mechanism implemented in Phase 12 |
| `docs/STACK.md` | Exact dependencies and versions; what is deliberately not used |
| `docs/SECURITY-AND-AUTH.md` | §1 and §§9–11 apply to this build. §§2–8 are post-V1 reference — **ignore them** |
| `docs/PRIOR-ART.md`, `docs/PRODUCT-PATH.md` | Context only. Do not act on these |

Read all of them before writing code. They are ~3,000 lines and they answer nearly every
question you will have.

---

## 3. Non-negotiables

These are invariants, not preferences. Violating one is a defect even if tests pass.

1. **Recording is sacred.** Audio goes to disk continuously as chunk files. A meeting must be reconstructible from disk with the app dead. Never buffer a whole meeting in RAM.
2. **Audio callbacks copy bytes and return.** No numpy, no file I/O, no logging inside a PortAudio callback. Ever.
3. **A chunk does not exist until its manifest line is fsynced.** The durability order in `TECHNICAL-DESIGN.md` §4.4 is asserted by a test; do not "optimize" it.
4. **No Google packages, no OAuth, no calendar.** V1 ships with zero integrations. `test_no_google_imports` enforces this.
5. **ASR and LLM are never loaded simultaneously.** `unload()` before the summarize stage.
6. **Fakes are selected by config, never by monkeypatching inside a test body.** If a test needs to patch internals, the seam is wrong — move the seam.
7. **Never weaken an assertion to make it pass.** If an assertion is genuinely wrong, change it deliberately and record why in the commit message and in `docs/DECISIONS.md`.
8. **Do not reference or import from any sibling project folder.** This repo is self-contained.
9. **Do not add scope.** No features that are not in the plan. Post-V1 items stay unbuilt.

---

## 4. Environment — read this before Phase 0

The primary working directory is a **WSL2 Ubuntu** shell. The product is a **Windows**
application. That split is the single most important operational fact in this session.

**First action: determine where you can execute.**

```bash
# Is Windows interop available from this shell?
ls /proc/sys/fs/binfmt_misc/WSLInterop 2>/dev/null && echo INTEROP_ON || echo INTEROP_OFF
```

### If INTEROP_OFF (the current known state)

`/etc/wsl.conf` has `[interop] appendWindowsPath = false` and the binfmt handler is not
registered, so this shell **cannot execute Windows binaries**. You can still read and write
`/mnt/c/...`.

Do this, in order:

1. **Develop in WSL**, running everything not marked `windows` / `audio_hw` / `gpu`. That is the large majority of the plan: Phases 0, 1, 2, 3, 5 (fake + logic), 6, 6b, 7, 8, 9, 10, and all pure-logic tests in 11 and 12.
2. **Write the Windows-marked tests anyway.** They are part of each phase's deliverable. They must be complete, correct, and collected — just skipped in this environment.
3. **Enable interop yourself if you can.** Append `enabled = true` under `[interop]` in `/etc/wsl.conf`. It takes effect only after `wsl --shutdown` from Windows, which this shell cannot invoke — so write the exact instruction into `docs/windows-run.md` and continue.
4. **Produce `docs/windows-run.md`** — the exact command sequence to run the Windows-only gates from a Windows terminal, with expected output. Keep it current as phases land.
5. **Do not treat a skip as a pass.** The final report must state exactly which gates are green, which are skipped-for-environment, and what command closes each one.

### If INTEROP_ON

Run the full suite, including `windows`, `audio_hw`, and `gpu` markers, via the Windows
Python interpreter (`/mnt/c/Users/am/AppData/Local/Programs/Python/Python313/python.exe`).
Then everything in the plan is achievable in this session and "done" means *all* of it.

---

## 5. Execution order

Follow `docs/EXECUTION-PLAN.md` phase by phase. Do not reorder, with one exception noted
below.

```
Phase 0   skeleton + the four self-testing techniques   ← everything depends on this
Phase 1   config, paths, DB, migrations
Phase 2   state machine, job queue, worker
Phase 3   audio, synthetic only (no hardware)
Phase 4   audio on real hardware — THE SPIKE  ← see §6
Phase 5   ASR backends
Phase 6   assembly
Phase 6b  enrichment seams (no integrations)
Phase 7   LLM + summarization
Phase 8   rendering + delivery              → M0 gate
Phase 9   HTTP API
Phase 10  frontend
Phase 11  tray, notifications, single instance
Phase 12  detection                          → M2 gate
Phase 13  packaging + first run
Phase 14  milestone gates M0, M1, M2
```

**The one permitted reorder:** if you are INTEROP_OFF, Phase 4 cannot execute. Implement it
fully, mark its tests, and continue to Phase 5. Do not let it block the other twelve phases.

**Every phase ends with:**

```bash
uv run ruff check . && uv run mypy app && uv run pytest -q && uv run python -m app.selftest all
```

A phase is done when that line is green — not when the code looks finished.

---

## 6. The one real stop condition

Phase 4 `test_dual_stream_concurrent`: if PyAudioWPatch cannot hold a capture stream and a
loopback stream simultaneously for an hour, **do not proceed downstream on that assumption.**

This is *not* a reason to stop working. Descend the fallback ladder in
`docs/TECHNICAL-DESIGN.md` §4.0 — SoundCard (master) → sounddevice/PortAudio → a vendored
C++ loopback helper piping PCM to stdin — and re-run the phase. Only `app/audio/wasapi.py`
changes; everything above the `AudioCapture` protocol is unaffected. That is why the
protocol exists.

Stop and report **only** if the entire ladder is exhausted, or if the environment
physically cannot run a gate (see §4).

---

## 7. Definition of done

**Done means:** every phase's exit criteria met, and the three milestone gates green:

```bash
python -m app.selftest pipeline    --input tests/fixtures/meeting_10min.wav --report m0.json
python -m app.selftest capture-e2e --seconds 120                            --report m1.json
python -m app.selftest detect-e2e                                           --report m2.json
```

M1 is the one that matters most: it starts a recording through the API, plays a speech
fixture out the render endpoint for two minutes, stops through the API, and runs the full
pipeline to `RENDERED` — the whole product proving itself with no human in the loop.

Done is **not**: "the code is written", "the tests would pass on Windows", or "only the
hardware tests are failing". Report status precisely.

---

## 8. Working protocol

- **Track progress in `docs/PROGRESS.md`** — one line per phase: status, the command that proved it, date, and any deviation. Update it as each phase closes, not at the end.
- **Record every judgment call in `docs/DECISIONS.md`** — what you chose, what the alternatives were, and why. This is how the design docs stay trustworthy.
- **Commit per phase**, message `phase N: <name>`, body listing the tests that now pass. Do not add `Co-Authored-By` or any Claude/Anthropic attribution.
- **Write the test in the same change as the code.** A phase's test table is its definition of done, not a follow-up.
- **Regenerate golden files only with `--update-goldens`**, and review the diff in that change.
- **Report measurements.** Phases 4, 5 and 7 exist partly to produce numbers other decisions depend on — clock drift, language-detection confidence, Hebrew tokens-per-word. Write them into the selftest report and into `docs/PROGRESS.md`.
- **When a test fails, fix the code.** Only change the test if the assertion is genuinely wrong, and say so explicitly.

---

## 9. Secrets and external calls

- The Anthropic API key comes from `keyring` or `ANTHROPIC_API_KEY`. If neither is present, `live_api`-marked tests skip — that is expected and is not a failure.
- **`live_api` tests are excluded from the default run.** Never make them a prerequisite for a phase's exit criteria.
- No test may send real email. `FakeSmtp` (an `aiosmtpd` server on a loopback port) is the only SMTP target in the suite.
- Nothing in this build talks to Google. If you find yourself reaching for an OAuth flow, you have misread the scope — see §3.4.

---

## 10. First actions

1. Read the seven docs in §2.
2. Run the interop probe in §4 and record the result in `docs/PROGRESS.md`.
3. Start Phase 0: `pyproject.toml`, the package tree, `app/clock.py`, `app/selftest.py`, and the four self-testing techniques (SAPI fixtures, loopback echo, self-held mic, config-selected fakes). Everything later depends on these existing and working.
4. Do not stop.
