# Known issues

Open problems, in the order I would close them. Written to be read cold: each entry says
what happens, what is known versus suspected, and what evidence exists.

Split out of `current-state.md` on **2026-09-06**. Entries 1–6 moved from there unchanged
except where this session's work touched them; 7 onward were found on 2026-09-06 while
getting the first real summaries out of the application.

---

## Blocking a first release

### 1. First-run setup — **rebuilt 2026-09-26** (D63), still no installer around it

`bootstrap.py` and `preflight()` exist and are tested. On top of them the interface opens
on **`/welcome`** until setup is done: the stepped flow of epic z8tj1hb01k
(`frontend/src/setup/`) — Welcome → Google Calendar → AI summaries → sound check →
recording (D64) → Done. Calendar and AI are optional; skipping AI leaves
`llm.provider = none`, and meetings stop at the transcript without failing. The step
reached is saved in `setup.step`, so closing the app half-way resumes there. An install
whose `app_config.json` predates `setup` and already has a model on disk is marked done
on load, so existing users never see the screen.

The screens were confirmed on a mock (`/setup-mock` in `vite dev`, or
`npm run build:mock` for one HTML file) before they were wired to the real backend.

Still open: Claude Code's sign-in code is typed into its own window, not the page
(Setup 4); the chained install → sign-in → test has only run against the specs' stubbed
status, never a real Claude Code or Codex install from this screen (Setup 10). The
Download has only been exercised against a fake hub in tests and, for the model manager
behind it, with the tiny model from the frozen build (#2). A first run through the screen
on a second machine is Windows-testing job 012.

### 2. The frozen build: built and signed, not yet installed on a clean machine

First produced on 2026-09-23 on machine A (`packaging\build.ps1 -Sign`, commit e55b25c):
`Upshot-0.1.0-Setup.exe`, 108 MB, a 374 MB one-dir freeze. Getting there found two things
that had never worked: the build script did not parse on Windows PowerShell 5.1, and the
frozen `upshot.exe` exited 0 without running at all (`app/tray.py` never called `main()`;
the build's selftest "passed" because it only read the exit code). Both are fixed and
tested. On A the freeze passes `--selftest imports` and `pipeline`, serves the UI, falls
back from a taken port, hands a second launch to the running one, and transcribes an
imported file under a home path with Hebrew letters and spaces.

Still open: a silent install, run and uninstall on a clean Windows (Windows Sandbox on
machine B, currently broken on that host), and SmartScreen's view of a self-signed
installer. The freeze bundles no CUDA libraries, so it transcribes on CPU unless the
machine has them.

### 3. The interface is well behind the category

`UX-PRIOR-ART.md` (2026-09-04) compares this against Granola, Circleback, Fireflies, Otter,
Fathom, Meetily and Hyprnote.

**That document is now stale and overstates the gap.** It claims we have no search across
meetings, no list view beside the calendar, no click-to-seek and a flat settings screen; all
four shipped afterwards, and it still describes the `notes.json` schema removed on
2026-09-06. It was the first thing read in the 2026-09-20 review and it sent that review the
wrong way for twenty minutes. Rewrite it before quoting it again.

What was still missing on 2026-09-20, and has since been done: results that show the
sentence that matched rather than a bare title, Markdown export, action items as objects with
an owner and a checkbox (**D47**), and a calendar that opens on the working day instead of at
00:00.

Still open from that list: an editable summary, chat scoped to a meeting, speaker names in
place of `ME`/`THEM`, and organisation by person or tag.

---

## Windows-specific, found while shipping the Claude Code provider

### 4. `CreateProcess` refuses PATH-resolved `powershell.exe` from the app process

Launching the sign-in or install console fails with `[WinError 5] Access is denied` on the
first attempt, and succeeds on the second, which uses the absolute
`%SYSTEMROOT%\System32\WindowsPowerShell\v1.0\powershell.exe`.

**Not reproduced outside the app.** Every variant — bare name, with `cwd`, with the
stripped environment, with `CREATE_NEW_CONSOLE`, with the real 1,730-character argument
list — spawns cleanly from a fresh Python on the same machine. The difference is the app's
parent chain (`run-app.ps1` → `uv` → `python`, streams redirected to files, no console).
Suspected an ASR/EDR policy keyed on that chain; **this is a hypothesis, not a finding**.

Mitigated rather than fixed: `launch_console` tries both and logs every attempt with
executable, working directory and argument length. Left open because the cause is unknown.

### 5. A captured-stream install of Claude Code fails silently

`install.ps1` sets `$ErrorActionPreference = "Stop"`, and PowerShell converts a native
command's stderr into error records whenever its streams are captured (a job, a pipe,
`2>&1`). The first byte `claude.exe install` writes to stderr then becomes a *terminating*
error: the 220 MB download and checksum succeed, the final step aborts, and it leaves a
**zero-byte stub** in `.local\share\claude\versions\` with no launcher — an install that
reports success and produces nothing.

This is almost certainly why the author's machine sat on Claude Code 2.1.4 for months with
zero-byte `2.1.29` and `2.1.76` stubs beside it: the auto-updater hits the same thing.

Our installer therefore runs in the foreground and shows activity from a **sibling**
process. A regression test forbids `Start-Job`, `Receive-Job`, `2>&1` and `| Out-String`
around the installer. **Consequence: there can be no true progress bar** — every route to
one captures the streams of a script we do not control and that treats stderr as fatal.

### 6. winget installs Claude Code where nothing can find it

`winget install Anthropic.ClaudeCode` installs the package *portable*: the binary lands in
`%LOCALAPPDATA%\Microsoft\WinGet\Packages\Anthropic.ClaudeCode…\claude.exe` and the symlink
into `WinGet\Links` — the directory actually on `PATH` — is only created where the machine
permits symlinks (Developer Mode, or an elevated install). On an ordinary non-admin machine
the result is an install that works from nowhere and never auto-updates.

`resolve()` globs that folder so the application finds it anyway, but `claude` will not run
in the user's own terminal. This is why the Install button uses Anthropic's native
installer instead.

---

## Consequences of the free-form summariser

The structured summariser was removed on 2026-09-06 (`NOTES_SCHEMA`, the map/reduce
pipeline, the Jinja template, the sanity gates). These are the costs, recorded so they are
decisions rather than surprises.

### 7. Nothing validates a summary any more

The sanity gates read `action_items` and their owners; there are no such fields. A summary
that invents an owner, or omits every action item from a two-hour meeting, now passes
silently. The gates caught a real problem the day they were removed — an imported
transcript gave the model no participants, and it produced `ME ודני` as an owner.

### 8. Meeting titles no longer update from the summary

They used to be set from `notes["title"]`. The envelope still carries an optional `title`;
wiring it back is a few lines, deliberately not done because silently rewriting a user's
meeting titles is a decision.

### 9. Free-form is slower and more expensive

Measured on the AppsFlyer meeting (25 KB of Hebrew, two 6,000-token windows plus a merge):

| | |
|---|---|
| structured | ~4m40s, ~16 KB output |
| free-form, designed HTML prompt | **9m17s**, 36.6 KB output |

It generates the whole document including layout, so output tokens rise sharply. Worth
knowing before pointing it at a backlog.

### 10. No structured data for cross-meeting features — **closed 2026-09-20**

Action items, decisions and participants were the basis for anything that reads across
meetings — an action-item inbox, per-person views, "what did I promise this week". None of
that is possible against opaque HTML.

**Closed** by **D47**: the envelope now carries an optional `action_items` array beside
`summary_html`, stored in an `action_items` table and read across meetings by
`GET /api/action-items` and the inbox screen. The document itself is still entirely the
prompt's — the list repeats what the model already wrote rather than shaping it.

Decisions and participants are still HTML-only. Nobody has asked for them yet, and the same
mechanism would extend to them if they do.

---

## The assistant (branch `ai-assistant`, 2026-09-26)

### A. Codex has not met the real CLI
The route is built from codex-rs's event types and the config reference, and tested
against a fake that insists on the safety flags. Unconfirmed on a real Codex: that
`mcp_servers.upshot.default_tools_approval_mode="approve"` is what lets `exec` call
Upshot's tools unasked, and the exact wording when it is not signed in. To check: install
Codex, sign in, choose it in Settings → AI, ask one question.

### B. The frozen build has not carried the MCP server yet
`mcp` 2.x is a new dependency, imported inside a function; its modules are listed in
`packaging/upshot.spec`, but no installer has been built since. The next Windows build
should open the panel and ask once.

### C. Four end-to-end specs outside the assistant are timing-sensitive
Seen failing in full runs and passing alone during this work (ClickUp tech debt):
`polish the_balloon_counts_open_items_and_follows_the_ticks` (checks `chip.count()` before
the calendar has drawn and then pages to the wrong week), `polish
key_features_explain_themselves_on_hover`, `redesign the_calendar_rail_has_up_next_and_open_items`
(near midnight), and pytest `test_the_meter_releases_the_device_when_the_client_goes_away`.
A fifth, `meeting search_finds_a_transcript`, was not flaky: it caught search returning a
deleted meeting, fixed in the same branch.

## Older, still open

### 11. Cancelling the echo loosens one timestamp

Measured on the author's recording: the closing ME turn starts at 14.85 s with cancellation
and 21.01 s without, because Whisper extends a segment's start backwards over the silence
the subtraction leaves. The speech and its end are right; the start is ~6 s early, so
click-to-seek on that turn lands early. Worth trying `vad_filter` on the cleaned track
before anything more clever. **D37**.

### 12. Track skew between the two streams is uncorrected

Measured at ~105–112 ms on one machine and 69 ms on another recording from the same one, so
it is per-session, not a constant. It does **not** drift within a recording: the lag stayed
at exactly 1683 samples in every window of `597be5`. Nothing today is tight enough to care.
Worth confirming over an hour rather than half a minute before correcting anything.

### 13. `KeyringSecrets.set` raises where no keyring backend exists

WSL has none, so the Settings API-key field fails there. Reading already degrades
gracefully; writing does not.

### 14. Settings exposes a fraction of the config

100 keys; about 18 deserve a UI.

### 15. One intermittent test warning is not fully explained

A thread raising inside a test surfaces as `PytestUnhandledThreadExceptionWarning`
attributed to whichever test happens to be running. One source was found and fixed. It is
confined to the test harness — no shipped code path spawns a thread that can raise this way.

---

### 16. The local UI has no access control — **opened 2026-09-23**

The one-time-link sign-in was removed (D57) because it signed every browser out on every
restart and was hard to use with several browser profiles. Anything that can reach
`127.0.0.1:<port>` — another Windows account on the same machine, or any local program —
can now read every transcript and summary, use the AI provider and drive the recorder.
Websites are still kept out by the Host check and CSRF. Research for a fix that is both
secure and friendly is tracked in ClickUp: https://app.clickup.com/t/z8tj1ha63r

## Fixed on 2026-09-06, recorded because the pattern matters

Every one of these presented as *silence* rather than as an error, which is why the first
real summary took a full day to extract. The lesson is in the pattern, not the individual
bugs.

| Fault | Symptom |
|---|---|
| `subprocess` text mode defaulted to cp1252 | Hebrew could not be written to the CLI's stdin; five retries, no tokens spent, nothing on screen |
| The schema instruction was a user-turn argument | It lost to a 6,000-token transcript and Claude Code's own system prompt; now `--system-prompt` |
| The worker returned early on `NEEDS_REVIEW` | Skipped `enqueue_next_stage`, so a flagged meeting was summarized and never rendered |
| The summary query cached its 404 | The file appeared and the page never asked again |
| A range-based edit deleted `/api/llm/prompt` | The prompt editor rendered `null` and simply was not there |
| Staleness ignored the prompt | Editing it and pressing Summarize did nothing |
| `config.save()` wrote back the environment layer | The launcher's `UP_LLM__PROVIDER=fake` became permanent on any unrelated save |
| Page freshness depended on a flag set by the click | A page opened mid-run reported "running" long after the work finished |

The defences added in response are worth keeping: launch attempts are logged with full
context, failed stages appear in red on the meeting screen with their error, long stages log
per window, and a test walks every `/api/…` string in the frontend and asserts the server
serves it.
