#!/usr/bin/env bash
# Start the app with fakes wired in: no audio hardware, no ASR model, no API key.
#
# What runs for real: the HTTP API, the job queue and worker, chunking and the manifest,
# assembly, rendering, draft delivery, the timeline UI.
# What is faked: the microphone (a synthetic tone), the transcript, and — unless you pass
# --provider — the summary itself. See docs/current-state.md.
#
#   scripts/demo.sh                      fakes end to end
#   scripts/demo.sh --provider anthropic real summaries (needs ANTHROPIC_API_KEY)
#   scripts/demo.sh --provider gemini    real summaries (needs GEMINI_API_KEY, free tier)
#   scripts/demo.sh --provider ollama    real summaries, local, no key
#   scripts/demo.sh --port 8100 --keep   another port; keep the previous demo's meetings

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PROVIDER="fake"
PORT="${PORT:-8000}"
HOME_DIR="$ROOT/.demo"
KEEP=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --provider) PROVIDER="${2:?--provider needs a value}"; shift 2 ;;
    --port)     PORT="${2:?--port needs a value}";         shift 2 ;;
    --home)     HOME_DIR="${2:?--home needs a value}";     shift 2 ;;
    --keep)     KEEP=1; shift ;;
    -h|--help)  sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
done

command -v uv >/dev/null || { echo "uv is not installed: https://docs.astral.sh/uv/" >&2; exit 1; }

# The UI is a built bundle; FastAPI serves frontend/dist.
if [[ ! -f frontend/dist/index.html ]]; then
  if command -v npm >/dev/null; then
    echo "building the frontend (first run only)..."
    ( cd frontend && [[ -d node_modules ]] || npm ci --silent; npm run build --silent )
  else
    echo "note: no frontend/dist and no npm — the UI will be a placeholder page." >&2
    echo "      run 'cd frontend && npm ci && npm run build' to get the real UI." >&2
  fi
fi

# Warn early rather than failing three stages into a meeting.
case "$PROVIDER" in
  anthropic) [[ -n "${ANTHROPIC_API_KEY:-}" ]] || echo "warning: ANTHROPIC_API_KEY is not set" >&2 ;;
  openai)    [[ -n "${OPENAI_API_KEY:-}"    ]] || echo "warning: OPENAI_API_KEY is not set"    >&2 ;;
  gemini)    [[ -n "${GEMINI_API_KEY:-}"    ]] || echo "warning: GEMINI_API_KEY is not set"    >&2 ;;
  claude-subscription)
    command -v claude >/dev/null || echo "warning: the 'claude' CLI is not on PATH" >&2 ;;
esac

if [[ $KEEP -eq 0 && -d "$HOME_DIR" ]]; then
  rm -rf "$HOME_DIR"
fi
mkdir -p "$HOME_DIR"

# Config comes from the environment layer; values are JSON, hence the inner quotes.
export UP_HOME="$HOME_DIR"
export UP_SERVER__PORT="$PORT"
export UP_AUDIO__CAPTURE='"synthetic"'   # a tone instead of a microphone
export UP_AUDIO__VAD='"energy"'          # Silero would reject a tone as "not speech"
export UP_ASR__BACKEND='"fake"'          # deterministic transcript, no 3 GB model
export UP_LLM__PROVIDER="\"$PROVIDER\""
export UP_DELIVERY__NOTIFIER='"fake"'    # no Windows toasts in this shell
export UP_AUDIO__MIN_MEETING_S=5         # so a short demo is not discarded
export UP_JOB_POLICY='"asap"'            # process immediately, do not wait for idle
# Deliberately *not* set: detection.mode. It used to be pinned to "off" here, which made
# the Settings control a lie — the choice saved to app_config.json and the environment
# layer, which outranks the file, overrode it again on every single start. The default
# ("shadow") watches and logs and never records on its own, and off Windows there is
# nothing for it to watch, so there was nothing to pin it for.

cat <<BANNER

  Upshot — demo
  home       $HOME_DIR
  provider   $PROVIDER $([[ "$PROVIDER" == "fake" ]] && echo "(no model: the summary is a placeholder)")
  audio      synthetic tone, transcript is fixture text

  Press Start recording in the browser, wait, press Stop.
  Ctrl+C here stops the app.

BANNER

exec uv run python -m app.main
