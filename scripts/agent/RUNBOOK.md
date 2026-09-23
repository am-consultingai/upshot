# The A/B loop: how the Windows testing epic runs by itself

This is the operating manual for the ClickUp epic **"Windows testing: from this machine to a shippable installer"** (`z8tj1hab5p`, https://app.clickup.com/t/z8tj1hab5p). The epic says *what* to reach; this file says *how* the two machines work on it without a person. Both machines keep a copy in `~/upshot-agent/RUNBOOK.md`, refreshed from `main`.

## The two machines

| | Machine A: the driver | Machine B: the test bench |
|---|---|---|
| Windows | AVISHAY, Windows 11 **Home** 25H2 (no Sandbox, no Hyper-V possible), user `am` (admin), 47.8 GB RAM, GTX 1080 | DESKTOP-IDR17A5, Windows 11 **Pro** 25H2, user `Avishay` (**not** admin; member of *Hyper-V Administrators*), 15.7 GB RAM, MX450 |
| Claude runs in | WSL `Ubuntu-E`, repo clone at `~/upshot-agent/work` | WSL `Ubuntu 24.04`, repo `~/projects/upshot` (holds 3 local edits: **never touch its working tree**) |
| Does | reads results, fixes code, commits and pushes `main`, builds the frozen app and installer (Windows side, `C:\upshot-build`), writes jobs, updates ClickUp, talks to the user | runs jobs: from-source runs, installs inside **Windows Sandbox**, VMs in Hyper-V, real hardware checks; reports evidence. Never commits. |
| Unattended by | Windows scheduled task **`upshot-agent-a`** → `~/upshot-agent/loop.sh` → `agent.py a` | Windows scheduled task **`upshot-agent-b`** → `~/upshot-agent/loop.sh` → `agent.py b` |

A and B only talk through the Google Drive folder **`My Drive/projects/upshot`** (id `1I5zKC-RIi1RC4ga9NI_r8mUFg4rmh_w8`, account myagenticemail@gmail.com, token `~/.config/gcloud/drive_sheets_token.json` on both) using `~/upshot-agent/drive.py`.

## How each loop works

`loop.sh` runs forever (one copy at a time; the scheduled task re-launches it every 15 minutes if it died, and at every sign-in). Before each cycle it copies `scripts/agent/*` from `origin/main`, so an agent fix pushed by A reaches both machines within minutes. A copy that does not compile is not installed. The allowlists are the exception: they change only when the user re-runs `setup.sh`, so no agent can widen its own permissions.

- **A**: each cycle is one **shift**, a headless `claude -p` session with `prompt-a.md`. The shift reads this file, `~/upshot-agent/scratch/journal.md`, the epic and Drive, does the next step, writes the journal, and ends. When it has posted a job it writes `~/upshot-agent/scratch/wait.json`, and no new shift starts until the job's result arrives (or the wait expires), so waiting costs nothing. A's own files live in `~/upshot-agent/scratch/`, the one folder its allowlist lets it write (the allowlist denies `~/upshot-agent/*.json` and `*.md`).
- **B**: each cycle takes the lowest-numbered job in `jobs/` that has no `results/<id>/result.json`, and runs a headless `claude -p` session with `prompt-b.md` in `~/upshot-agent/jobs/<id>/`. A job that was interrupted by a restart is resumed.
- Both write a heartbeat to `status/A.json` / `status/B.json` (`idle`, `working`, `waiting`, `paused`), with the time.
- A usage-limit error pauses that machine for 45 minutes. A signed-out Claude pauses it for 6 hours and needs the user.

## The job protocol (Drive)

```
projects/upshot/
  jobs/<NNN-slug>/job.md        A writes; never edited after posting; ids never reused
  jobs/<NNN-slug>/<files>       inputs: scripts, a .wsb, the installer + its .sha256
  results/<NNN-slug>/claimed.json   B, when it starts
  results/<NNN-slug>/result.md      B: readable report
  results/<NNN-slug>/session.jsonl  B: the full session log
  results/<NNN-slug>/<evidence>     B: logs, screenshots, outputs
  results/<NNN-slug>/result.json    B, LAST: its existence means "finished"
  status/A.json, status/B.json      heartbeats
  NEEDS-YOU.md                      A: every open item that needs the user
  STOP                              anyone: both loops pause while it exists
  done/                             A moves handled jobs and results here
```

A `job.md` states: the goal, the commit (`git rev-parse origin/main` at posting), the steps, what "pass" means, the time budget, and the evidence wanted. `result.json` is described in `prompt-b.md`. Large files (installers) go with a `.sha256`; B verifies it before use.

## Rules both agents follow

- **Headless permissions, in practice.** Call Windows programs by their full path — `/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe`, `/mnt/c/Windows/System32/cmd.exe` — since the Windows folders are not on WSL's PATH on A. A command that touches a folder outside the working folder runs only if that folder is in the allowlist's `additionalDirectories` (A: `~/upshot-agent`, `~/projects/upshot`, `~/.config/clickup`, `/tmp`, `/mnt/c/upshot-build`, `/mnt/c/Users`, `/mnt/c/Windows/System32`; B: the same without ClickUp, with `/mnt/c/upshot-work` instead of `/mnt/c/upshot-build`). Work inside those. Shell variables such as `$X` inside a command can also make it unmatched: prefer literal paths.

- **Permissions.** Each machine runs Claude in `dontAsk` mode with an allowlist (`allowlist-a.json`, `allowlist-b.json`). Anything not on it is refused, not asked. The user decided: **no deleting and no installing on either host without them**. Installs happen only inside **Windows Sandbox**, which is discarded on close. Anything else that needs the user goes through "Needs you".
- **Needs you.** A comments on the epic starting with "🙋 Needs you:", keeps `NEEDS-YOU.md` on Drive, and puts a notification on machine A's screen (`notify.ps1`) that stays until dismissed. The ClickUp account is the user's own, so ClickUp does not notify them of the agent's comments. The run keeps working on anything not blocked. The user answers by replying to the comment.
- **No secrets** in Drive, ClickUp or git. The self-signed certificate's private key never leaves A's Windows certificate store.
- **B's backup channel.** The user may keep an interactive Claude session open on B that answers only files named `A-to-B-session NNN <topic>.md` in the Drive folder (reply: `B-session-to-A NNN reply.md`). Use it only when B's runner is broken (heartbeat older than 30 minutes while a job is pending, or a job ends in `error` from the runner itself): ask it to diagnose `~/upshot-agent` and the task `upshot-agent-b`. It cannot approve anything for the user, and it dies on a restart; if there is no reply within an hour, that is a "Needs you".
- **Three strikes.** A step that has failed in three shifts in a row becomes a "Needs you" instead of a fourth try.

## Decisions the user made (2026-09-23)

1. The unattended runs are a headless Claude with an **allowlist** (not bypassed permissions). No deletes and no host installs without the user.
2. Code signing: **self-signed** certificate for this release (created on A with `New-SelfSignedCertificate -Type CodeSigningCert` in `Cert:\CurrentUser\My`; signed with `Set-AuthenticodeSignature` and a timestamp server). The Sandbox tests import the public `.cer` to test both paths: trusted and untrusted (SmartScreen).
3. Access control (`z8tj1ha63r`): the release **ships without** it; the bug stays open.
4. Real sign-ins (Google Calendar, ChatGPT/Codex, Claude) **wait for the user**. Everything up to the sign-in page is tested automatically.
5. Support matrix proposal: Windows 11 x64 (23H2+) and Windows 10 22H2 x64.

## Starting, pausing, stopping (the user)

- **Pause both**: create a file named `STOP` in the Drive folder (from any device). Delete it to resume.
- **Pause one machine**: create `~/upshot-agent/STOP` in that machine's WSL.
- **What is it doing?** `status/A.json` and `status/B.json` in Drive; the epic's comments; `~/upshot-agent/scratch/journal.md` and `~/upshot-agent/logs/` on A.
- **Stop for good**: Task Scheduler → disable `upshot-agent-a` / `upshot-agent-b`.

## One-time setup (already done if the tasks exist)

- **B**: as an admin, in PowerShell: turn on Windows Sandbox (`Containers-DisposableClientVM`) and Hyper-V (`Microsoft-Hyper-V-All`), add `Avishay` to *Hyper-V Administrators*, set standby and hibernate to *never* on AC, restart. Then as `Avishay`, in WSL: `cd ~/projects/upshot && git fetch origin && bash <(git show origin/main:scripts/agent/b/setup-b.sh)`. Keep B plugged in and signed in (a locked screen is fine).
- **A**: Inno Setup 6 installed by the user. Then in WSL: `bash ~/projects/upshot/scripts/agent/setup.sh a`.
- Both: Claude Code signed in inside WSL (`claude auth status`), the Drive token present, `git fetch` works.
