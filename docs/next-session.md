# Next session — operating brief

The application is **built and running on Windows**. This is no longer an implementation
session; it is a continuation. Read `docs/current-state.md` first — it is the accurate
picture as of 2026-09-03, including what is not proved and what is broken.

---

## 1. Where things stand

Phases 0–14 are complete. The app records two real tracks on the author's Windows machine,
transcribes Hebrew locally on the GPU, assembles, renders and files. 420 Python tests, 31
vitest, 26 Playwright, `ruff` and `mypy --strict` clean.

Since the last brief, items 1 and 2 of the queue below were built: **echo cancellation**
(D37) and the **retention sweep** (D38). Both have now been **run on Windows** against
real audio and the real GPU model — driven directly, not through the UI. Running the sweep
there found a delete that reported success while leaving half the audio on disk; see D38.

The single most important fact: **almost every bug worth fixing in the last few days was
found by running it on Windows, not by the test suite.** Ten such bugs are listed in
`current-state.md` §3. Prefer running the real thing over adding more tests against fakes.

---

## 2. Environment — this changed

**Windows interop is available from this WSL shell.** The previous brief said it was not.
It is:

```bash
/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -Command "..."
```

That means you can, from here:

- inspect and stop Windows processes (`Get-CimInstance Win32_Process`, `Stop-Process`)
- check what holds a port (`Get-NetTCPConnection`)
- **read the Windows crash log** — `Get-WinEvent` with `Id=1000` gives the faulting module
  and exception code, which is how the PortAudio access violation was identified

**An isolated instance costs one environment variable.** `UP_HOME` overrides the config
file, the database, the data root and the log directory together, so a whole second
instance can be pointed at a scratch folder and driven from the launcher's venv without
going near the real library. Use it before running anything that deletes.

**Read the app's own logs directly. Do not ask the user to paste them:**

```
/mnt/c/Users/am/AppData/Local/upshot/logs/app.log        the app's log
/mnt/c/Users/am/AppData/Local/upshot/console.err.log     the same, plus stdout
/mnt/c/Users/am/AppData/Local/upshot/meetings/<id>/      audio, transcript, summary
/mnt/c/Users/am/AppData/Local/upshot/app_config.json     saved settings
```

Recordings are real audio: measure them with numpy rather than reasoning about them. Peak,
RMS and FFT cross-correlation have settled several arguments in this project that opinion
could not.

### The author's machine

| | |
|---|---|
| Model | `D:\deprecated_project\Learning Managers\temp\Scripts\ivrit_model` (ivrit-ai large-v3, CT2) |
| CUDA libs | `D:\deprecated_project\Learning Managers\temp\Scripts` — cuBLAS 12 + cuDNN 9 |
| GPU | Pascal, so `compute_type=int8` — **not** float16 |
| Audio | Voicemeeter. The microphone endpoint carries system audio, which is the crosstalk problem |
| Port 8000 | taken by a Docker container (`billers4-backend-1`). The launcher moves to 8010 |
| Repo | on `\\wsl.localhost\...`, read by Windows over the bridge; the runtime lives on the Windows disk |

---

## 3. How the user wants to work

These were stated explicitly. They are not preferences to re-litigate.

- **Ctrl+C in the launcher stops everything.** When a change needs a restart, say so and
  move on. Do not check for stray processes, do not ask permission to stop them.
- **Never publish an Artifact** unless explicitly asked (global instruction).
- **No `Co-Authored-By` or Claude/Anthropic attribution** in commit messages, ever.
- Report what was measured, not what is expected. If something has never been executed,
  say so plainly rather than implying coverage.

---

## 4. The work queue

In the order I would take it.

1. **Record a live meeting with both features on.** They have run on Windows against
   audio already on disk; what has not happened is a meeting recorded, cancelled and
   swept while the app runs normally. Record with the Voicemeeter bus selected (the
   default fault on this machine) and check that `meta.json` gains an `echo` block near
   gain 1.7 / delay 1683, that the far side appears **once** in `transcript.md`, and that
   the mix plays without the slap echo. Then listen for the timestamp cost in
   `current-state.md` §6.1 — click a turn that follows a stretch of far-side-only audio
   and see how early it lands.
2. **A real LLM call.** Every summary so far is placeholder text. `-Provider gemini`,
   `anthropic`, or `claude-subscription` (which uses the signed-in Claude Code CLI and
   holds no credential). This is the last major path that has never executed.
3. **Track skew** — measure before correcting. See `current-state.md` §6.2. D37 narrowed
   this: the offset does not drift *within* a recording at five-second resolution. What
   is unmeasured is an hour rather than half a minute.
4. **First-run setup UI**, then the frozen build and installer.
5. **Settings for the keys that now delete things.** `retention.audio_days` deletes user
   recordings and is not in the UI.

To run either of them on Windows without touching the real library, set `UP_HOME` to a
scratch folder — it overrides the config, the database, the data root and the logs
together — and drive the stage or the sweep from the venv the launcher built at
`%LOCALAPPDATA%\upshot-win\venv`. That is how D37 and D38 were verified, and it is
much faster than recording a meeting each time.

---

## 5. Verification

```bash
uv run ruff check . && uv run mypy app && uv run pytest -q
cd frontend && npx tsc --noEmit && npx vitest run && npx playwright test
```

Playwright takes ~2.5 minutes; run it in the background and do something else.

---

## 6. Traps this project has already fallen into

Each of these cost real time. They are not hypothetical.

- **`pkill -f "app.main"` kills your own shell**, because the pattern appears in its own
  command line. Use `[a]pp[.]main`.
- **SSE cannot be tested through `TestClient`** — it deadlocks (D20). Use the `serve()`
  helper in `tests/fixtures/api.py`, which runs a real uvicorn socket.
- **Never `await` in a `finally` that runs during cancellation.** A closing browser cancels
  the task and the await raises before your cleanup runs.
- **React effects must not depend on `t` or anything else recreated each render.** One such
  dependency reopened the microphone four times a second.
- **A Linux listener inside WSL answers `127.0.0.1` on the Windows side** while Windows
  reports the port as free. Probe by connecting, not by asking the OS.
- **Regenerate goldens deliberately** with `--update-goldens`, and read the diff. The
  OpenAPI snapshot changes whenever a route or parameter does.
- **Do not weaken an assertion to make it pass.** Two fixtures in this repo were wrong and
  were fixed; both times the test was right and the fixture was lying.
- **String-slicing a Python file by `t.index("...")` is dangerous** — `self._lock` matches
  `_lock` and truncates a class. Anchor on something unique.

---

## 7. Invariants that still hold

Unchanged from the original brief, and still load-bearing:

1. Recording is sacred — audio reaches disk continuously and a meeting must be
   reconstructible with the app dead. The mechanism changed in D34; the guarantee did not.
2. Audio callbacks copy bytes and return. No numpy, no I/O, no logging.
3. Audio does not exist until its manifest line is fsynced. The durability order is
   asserted by test.
4. No Google packages, no OAuth, no calendar.
5. ASR and the LLM are never resident together.
6. Fakes are chosen by config, never by monkeypatching inside a test body.
7. Record every judgment call in `docs/DECISIONS.md` — 38 entries and counting.
