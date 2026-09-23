"""A fake ``codex`` executable, for driving the Codex provider without OpenAI.

A real script on disk rather than an injected runner, so the tests that use it cross the
same boundary production does: ``PATH`` resolution, a subprocess, stdin, the files named
on the command line, the environment the child inherits. It answers the three commands
the application sends — ``--version``, ``login status``, ``exec`` — the way the real
0.156.1 build does (checked against its ``--help`` and its own message strings on
2026-09-23), and logs every invocation so a test can see what was sent.

``FAKE_CODEX_MODE`` picks the behaviour: ``ok`` (default), ``quota``, ``signed-out``,
``old`` (a build without ``login status``) or ``api-key`` (signed in, but with a key).
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT = r"""
import json, os, sys

mode = os.environ.get("FAKE_CODEX_MODE", "ok")
log = os.environ.get("FAKE_CODEX_LOG")
args = sys.argv[1:]


def record(**extra):
    if log:
        entry = {"args": args, "cwd": os.getcwd(), "env": sorted(os.environ), **extra}
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")


if args[:1] == ["--version"]:
    record()
    print("codex-cli 0.0.0-fake")
    sys.exit(0)

if args[:2] == ["login", "status"]:
    record()
    if mode == "old":
        print("error: unrecognized subcommand 'status'", file=sys.stderr)
        sys.exit(2)
    if mode == "signed-out":
        print("Not logged in", file=sys.stderr)
        sys.exit(1)
    if mode == "api-key":
        print("Logged in using an API key - sk-proj-***FAKE", file=sys.stderr)
        sys.exit(0)
    print("Logged in using ChatGPT", file=sys.stderr)
    sys.exit(0)

if args[:1] == ["exec"]:
    prompt = sys.stdin.read()
    schema_path = args[args.index("--output-schema") + 1]
    answer_path = args[args.index("--output-last-message") + 1]
    with open(schema_path, encoding="utf-8") as handle:
        schema = json.load(handle)
    record(prompt=prompt, schema=schema)
    if mode == "quota":
        print(
            "ERROR: You’ve hit your usage limit. Visit https://chatgpt.com/codex/settings/"
            "usage to purchase more credits or try again at Aug 29, 2026 4:15 PM.",
            file=sys.stderr,
        )
        sys.exit(1)
    if mode == "signed-out":
        print("Error: Not logged in. Run `codex login` first.", file=sys.stderr)
        sys.exit(1)
    props = schema.get("properties", {})
    if "summary_html" in props:
        # Strict structured output fills every optional key, with null where it has
        # nothing: exactly what the provider has to strip before validating.
        answer = {
            "summary_html": "<p>Summarized by the fake Codex.</p>",
            "title": "Fake Codex summary",
            "action_items": [
                {"who": "ME", "what": "send the deck", "due": None, "at_ms": None,
                 "detail": None, "due_at": None}
            ],
            "chapters": None,
        }
    else:
        answer = {name: True for name in schema.get("required", [])}
    with open(answer_path, "w", encoding="utf-8") as handle:
        json.dump(answer, handle)
    print("progress goes to stderr", file=sys.stderr)
    print(json.dumps(answer))
    sys.exit(0)

print(f"error: unexpected argument '{args[0] if args else ''}' found", file=sys.stderr)
sys.exit(2)
"""


def install_fake_codex(folder: Path) -> Path:
    """Write ``codex`` into ``folder`` and return its path. POSIX only (a shebang)."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "codex"
    path.write_text(f"#!{sys.executable}\n{SCRIPT}", encoding="utf-8")
    path.chmod(0o755)
    return path
