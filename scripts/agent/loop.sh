#!/usr/bin/env bash
# The forever loop behind the Windows scheduled task "upshot-agent-<role>".
# One copy runs at a time (flock). Between cycles it refreshes its own files
# from origin/main, so a fix A pushes reaches both machines without a human.
# Setup writes ~/upshot-agent/env with ROLE and REPO. See RUNBOOK.md.
set -u
HOME_DIR="${UPSHOT_AGENT_HOME:-$HOME/upshot-agent}"
# shellcheck source=/dev/null
. "$HOME_DIR/env"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

exec 9>"$HOME_DIR/loop.lock"
flock -n 9 || exit 0   # already running; the task's 15-minute repeat is only a watchdog

# The allowlist is NOT refreshed: it is what the user installed with setup.sh, and an agent
# must not be able to widen its own permissions by pushing to main.
FILES="agent.py drive.py notify.ps1 loop.sh prompt-$ROLE.md RUNBOOK.md"

refresh() {
  git -C "$REPO" fetch -q origin main 2>>"$HOME_DIR/logs/agent.log" || return 0
  local tmp; tmp=$(mktemp -d)
  for f in $FILES; do
    git -C "$REPO" show "origin/main:scripts/agent/$f" >"$tmp/$f" 2>/dev/null || return 0
  done
  # Never install a broken agent: keep the last good copy if this one does not compile.
  if python3 -m py_compile "$tmp/agent.py" "$tmp/drive.py"; then
    # cp then mv: a new inode, so the bash running this very file keeps reading the old one.
    for f in $FILES; do cp "$tmp/$f" "$HOME_DIR/.$f.new" && mv -f "$HOME_DIR/.$f.new" "$HOME_DIR/$f"; done
    chmod +x "$HOME_DIR/loop.sh"
  fi
}

mkdir -p "$HOME_DIR/logs"
while true; do
  refresh
  python3 "$HOME_DIR/agent.py" "$ROLE" >>"$HOME_DIR/logs/loop.out" 2>&1
  sleep 5
done
