#!/usr/bin/env bash
# One-time setup of the unattended loop on this machine. See RUNBOOK.md.
#
#   bash setup.sh a     # machine A (the driver)
#   bash setup.sh b     # machine B (the test bench); b/setup-b.sh calls this
#
# It checks the machine, copies scripts/agent/* from origin/main into
# ~/upshot-agent, and registers the Windows scheduled task "upshot-agent-<role>"
# for the current Windows user (no admin needed). It changes nothing in the
# repo's working tree. Safe to run again.
set -euo pipefail
ROLE="${1:-}"
[[ "$ROLE" == a || "$ROLE" == b ]] || { echo "usage: setup.sh a|b"; exit 2; }
REPO="${REPO:-$HOME/projects/upshot}"
AH="$HOME/upshot-agent"
PS=/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe
say() { printf '\n== %s\n' "$*"; }
fail() { printf '\nSTOPPED: %s\nNothing was registered. Fix that and run this again.\n' "$*" >&2; exit 1; }

say "Checking this machine"
[[ -n "${WSL_DISTRO_NAME:-}" && -x "$PS" ]] || fail "run this inside WSL on Windows"
[[ -d "$REPO/.git" ]] || fail "no clone at $REPO (git clone git@github.com:am-consultingai/upshot.git $REPO)"
git -C "$REPO" fetch -q origin main || fail "git fetch in $REPO failed (GitHub access?)"
command -v python3 >/dev/null || fail "python3 is missing"
command -v flock >/dev/null || fail "flock is missing (util-linux)"
CLAUDE="$(command -v claude || echo "$HOME/.local/bin/claude")"
[[ -x "$CLAUDE" ]] || fail "Claude Code is not installed in this WSL"
"$CLAUDE" auth status 2>/dev/null | grep -q '"loggedIn": *true' \
  || fail "Claude Code is not signed in here: run 'claude' once, sign in, exit, then run this again"
[[ -f "$HOME/.config/gcloud/drive_sheets_token.json" ]] || fail "no Drive token at ~/.config/gcloud/drive_sheets_token.json"
echo "repo $(git -C "$REPO" rev-parse --short origin/main) · claude $("$CLAUDE" --version | head -1) · distro $WSL_DISTRO_NAME"

say "Copying the agent into $AH"
mkdir -p "$AH/logs" "$AH/jobs"
for f in agent.py drive.py notify.ps1 loop.sh RUNBOOK.md "prompt-$ROLE.md" "allowlist-$ROLE.json"; do
  git -C "$REPO" show "origin/main:scripts/agent/$f" >"$AH/.$f.new" && mv -f "$AH/.$f.new" "$AH/$f"
done
chmod +x "$AH/loop.sh"
# Headless Claude refuses commands that touch folders outside its working folder unless they are
# listed in additionalDirectories; spell out ~ there, since this machine's home differs from A's.
python3 - "$AH/allowlist-$ROLE.json" "$HOME" <<'PY'
import json, sys
path, home = sys.argv[1], sys.argv[2]
d = json.load(open(path))
dirs = d["permissions"].get("additionalDirectories", [])
d["permissions"]["additionalDirectories"] = [home + x[1:] if x.startswith("~/") else x for x in dirs]
json.dump(d, open(path, "w"), indent=1)
PY
python3 -m py_compile "$AH/agent.py" "$AH/drive.py"
{
  echo "ROLE=$ROLE"
  echo "REPO=$REPO"
  if [[ "$ROLE" == a ]]; then echo "export UPSHOT_A_REPO=$AH/work"; fi
} >"$AH/env"

say "Checking Drive"
(cd "$AH" && python3 drive.py ls >/dev/null) || fail "cannot reach the Drive folder with the token"
echo "Drive folder projects/upshot is reachable"

if [[ "$ROLE" == a ]]; then
  say "Driver's working clone"
  if [[ ! -d "$AH/work/.git" ]]; then
    git clone -q "$REPO" "$AH/work"
    git -C "$AH/work" remote set-url origin "$(git -C "$REPO" remote get-url origin)"
  fi
  git -C "$AH/work" fetch -q origin main && git -C "$AH/work" checkout -q main && git -C "$AH/work" merge -q --ff-only origin/main
  echo "$AH/work at $(git -C "$AH/work" rev-parse --short HEAD)"
  [[ -f "/mnt/c/Program Files (x86)/Inno Setup 6/ISCC.exe" || -f "$(wslpath "$("$PS" -NoProfile -Command '$env:LOCALAPPDATA' | tr -d '\r')")/Programs/Inno Setup 6/ISCC.exe" ]] \
    || echo "NOTE: Inno Setup 6 is not installed; the run will ask you for it (Needs you) when it reaches the installer."
else
  say "Windows Sandbox"
  if [[ -f /mnt/c/Windows/System32/WindowsSandbox.exe ]]; then
    echo "Windows Sandbox is enabled"
  else
    echo "NOTE: Windows Sandbox is NOT enabled yet. Jobs that need it will report 'blocked' until it is (see RUNBOOK: one-time setup)."
  fi
fi

say "Registering the scheduled task upshot-agent-$ROLE (current Windows user, no admin)"
WINTMP="$(wslpath "$("$PS" -NoProfile -Command '$env:TEMP' | tr -d '\r')")"
cat >"$WINTMP/upshot-agent-register.ps1" <<PS1
\$ErrorActionPreference = 'Stop'
\$action = New-ScheduledTaskAction -Execute 'conhost.exe' -Argument '--headless wsl.exe -d $WSL_DISTRO_NAME -- bash -lc ~/upshot-agent/loop.sh'
\$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(30) -RepetitionInterval (New-TimeSpan -Minutes 15)
\$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -DontStopOnIdleEnd
Register-ScheduledTask -TaskName 'upshot-agent-$ROLE' -Action \$action -Trigger \$trigger -Settings \$settings -Description 'Upshot Windows testing loop (machine $ROLE). See ~/upshot-agent/RUNBOOK.md in WSL. Disable to stop.' -Force | Out-Null
Start-ScheduledTask -TaskName 'upshot-agent-$ROLE'
PS1
"$PS" -NoProfile -ExecutionPolicy Bypass -File "$(wslpath -w "$WINTMP/upshot-agent-register.ps1")" | tr -d '\r'

say "Waiting for the loop to start"
for _ in $(seq 1 12); do
  sleep 5
  if ! flock -n "$AH/loop.lock" true 2>/dev/null; then
    echo "The loop is running. Heartbeat: Drive projects/upshot/status/${ROLE^^}.json"
    echo "Log: $AH/logs/agent.log"
    echo
    echo "Done. You can close this window; the loop keeps running while this Windows user is signed in."
    exit 0
  fi
done
echo "WARNING: the task is registered but the loop did not start within a minute." >&2
echo "Look at Task Scheduler -> upshot-agent-$ROLE -> History, or run it by hand to see the error: bash $AH/loop.sh" >&2
exit 1
