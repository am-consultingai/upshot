#!/usr/bin/env bash
# Machine B's one-time setup. Run inside B's WSL, as the user Avishay:
#   cd ~/projects/upshot && git fetch origin && bash <(git show origin/main:scripts/agent/b/setup-b.sh)
# See scripts/agent/RUNBOOK.md.
set -euo pipefail
REPO="${REPO:-$HOME/projects/upshot}"
exec bash <(git -C "$REPO" show origin/main:scripts/agent/setup.sh) b
