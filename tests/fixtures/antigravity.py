"""A fake ``agy`` executable, for driving the Antigravity provider without Google.

A real script on disk rather than an injected runner, so the tests that use it cross the
same boundary production does: ``PATH`` resolution, a subprocess, stdin, the schema file
named on the command line, the environment the child inherits. It answers the commands
the application sends the way ``agy`` 1.2.12 did on machine B (2026-09-28), and logs
every invocation so a test can see what was sent.

``FAKE_AGY_MODE`` picks the behaviour: ``ok`` (default), ``quota`` or ``signed-out``.
A signed-out ``-p`` run is the sign-in: it prints Google's link and reads a code from
stdin, as the real one does for 60 seconds. The code ``GOOD-CODE`` signs it in (a
``signed-in`` file beside the script, which wins over the mode from then on); anything
else fails the way a wrong code does.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT = r"""
import json, os, sys

mode = os.environ.get("FAKE_AGY_MODE", "ok")
log = os.environ.get("FAKE_AGY_LOG")
args = sys.argv[1:]
here = os.path.dirname(os.path.abspath(sys.argv[0]))
marker = os.path.join(here, "signed-in")
signed_in = mode != "signed-out" or os.path.exists(marker)


def record(**extra):
    if log:
        entry = {"args": args, "cwd": os.getcwd(), "env": sorted(os.environ), **extra}
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def result(**fields):
    return {"conversation_id": "fake-conversation" if fields.get("status") == "SUCCESS" else "",
            "duration_seconds": 0.1, "num_turns": 1, **fields}


if args == ["--version"]:
    record()
    print("1.2.12")
    sys.exit(0)

if args == ["models"]:
    record()
    print("Fetching available models...")
    if not signed_in:
        print("Error: Please sign in to view available models. Launch the CLI without "
              "arguments to sign in.")
        sys.exit(1)
    print("gemini-3.8-flash-high\tGemini 3.8 Flash (High)")
    print("gemini-3.1-pro-high\tGemini 3.1 Pro (High)")
    sys.exit(0)

if args[:1] == ["-p"]:
    record()
    if not signed_in:
        print("Authentication required. Please visit the URL to log in:", file=sys.stderr)
        print("  https://accounts.google.com/o/oauth2/auth?access_type=offline&client_id=FAKE"
              "&redirect_uri=https%3A%2F%2Fantigravity.google%2Foauth-callback&state=FAKE",
              file=sys.stderr)
        print("", file=sys.stderr)
        print("Waiting for authentication (timeout 60s)...", file=sys.stderr)
        print("Or, paste the authorization code here and press Enter:", file=sys.stderr, flush=True)
        code = sys.stdin.readline().strip()
        record(code=code)
        if code != "GOOD-CODE":
            print("error: authentication failed or timed out", file=sys.stderr)
            print(json.dumps(result(status="ERROR", response="",
                                    error="authentication failed or timed out")))
            sys.exit(1)
        open(marker, "w").close()
    print(json.dumps(result(status="SUCCESS", response="OK\n")))
    sys.exit(0)

if "--input-format" in args:
    if args[args.index("--input-format") + 1] != "stream-json":
        print("error: expected --input-format stream-json", file=sys.stderr)
        sys.exit(2)
    # Every run of Upshot's goes through its tool-less agent, kept in its own folder (D79).
    agent = os.path.join(os.getcwd(), ".agents", "agents", "upshot.md")
    chosen = args[args.index("--agent") + 1] if "--agent" in args else ""
    if chosen != "upshot" or not os.path.exists(agent):
        print("fake agy: started without the upshot agent", file=sys.stderr)
        sys.exit(2)
    with open(agent, encoding="utf-8") as handle:
        if "tools: [finish]" not in handle.read():
            print("fake agy: the upshot agent must allow no tools but finish", file=sys.stderr)
            sys.exit(2)
    lines = [line for line in sys.stdin.read().splitlines() if line.strip()]
    event = json.loads(lines[0])
    prompt = event["message"]["content"]
    if "--json-schema" not in args:
        # The assistant: no schema, the answer streamed. It cites the first transcript line
        # Upshot gathered into the prompt, as a real model would.
        record(prompt=prompt, schema=None, events=len(lines))
        print(json.dumps({"event": "init", "conversation_id": "fake-chat",
                          "init": {"model": "gemini-fake"}}))
        if not signed_in:
            print(json.dumps({"event": "result", "result": result(
                status="ERROR", response="", error="authentication failed or timed out")}))
            sys.exit(1)
        import re
        found = re.search(r'"meeting_id":"([^"]+)"[^{}]*?"lines":\[\{"at_ms":(\d+)', prompt)
        cite = f" [[m:{found.group(1)}@{found.group(2)}]]" if found else ""
        recap = "Recapped. " if "The conversation so far" in prompt else ""
        for piece in (recap, "Antigravity found it", cite, "."):
            if piece:
                print(json.dumps({"event": "step_update", "step_update": {
                    "state": "ACTIVE", "step_type": "agent_response", "text_delta": piece}}))
        print(json.dumps({"event": "result", "result": result(status="SUCCESS", response="")}))
        sys.exit(0)
    with open(args[args.index("--json-schema") + 1], encoding="utf-8") as handle:
        schema = json.load(handle)
    record(prompt=prompt, schema=schema, events=len(lines))
    init = {"model": "gemini-3.8-flash-low", "permission_mode": "request-review"}
    print(json.dumps({"event": "init", "conversation_id": "fake-conversation", "init": init}))
    if not signed_in:
        print("Authentication required. Please visit the URL to log in:", file=sys.stderr)
        print(json.dumps({"event": "result", "result": result(
            status="ERROR", response="", error="authentication failed or timed out")}))
        sys.exit(1)
    if mode == "quota":
        print(json.dumps({"event": "result", "result": result(
            status="ERROR", response="",
            error="RESOURCE_EXHAUSTED: Quota exceeded for your Google AI plan")}))
        sys.exit(1)
    props = schema.get("properties", {})
    if "summary_html" in props:
        answer = {
            "summary_html": "<p>Summarized by the fake Antigravity.</p>",
            "title": "Fake Antigravity summary",
            "action_items": [{"who": "ME", "what": "send the deck"}],
        }
    else:
        answer = {name: True for name in schema.get("required", [])}
    print(json.dumps({"event": "step_update", "step_update": {"state": "DONE",
                      "step_type": "agent_response", "text_delta": "Summarizing."}}))
    print(json.dumps({"event": "result", "result": result(
        status="SUCCESS", response=json.dumps(answer), structured_output=answer,
        json_schema=schema)}, ensure_ascii=False))
    sys.exit(0)

print(f"flag provided but not defined: {args[0] if args else ''}", file=sys.stderr)
sys.exit(2)
"""


def install_fake_agy(folder: Path) -> Path:
    """Write ``agy`` into ``folder`` and return its path. POSIX only (a shebang)."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "agy"
    path.write_text(f"#!{sys.executable}\n{SCRIPT}", encoding="utf-8")
    path.chmod(0o755)
    return path
