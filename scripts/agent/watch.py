"""Follow this machine's loop in a terminal: what the agent is doing, as it does it.

    python3 ~/upshot-agent/watch.py          (Ctrl+C to stop watching; the loop keeps running)

It only reads: ~/upshot-agent/logs/agent.log, the newest session log next to it
(shift-*.jsonl on A, <job>.jsonl on B), and the other machine's heartbeat on
Drive once a minute. Closing it never affects the loop. See RUNBOOK.md.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

HOME = Path(os.environ.get("UPSHOT_AGENT_HOME", "~/upshot-agent")).expanduser()
LOGS = HOME / "logs"
sys.path.insert(0, str(HOME))

COLOR = sys.stdout.isatty()


def c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if COLOR else text


def stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def say(tag: str, text: str, code: str = "0") -> None:
    text = " ".join(text.split())
    width = 220
    more = "…" if len(text) > width else ""
    print(f"{c('2', stamp())} {c(code, tag.ljust(6))} {text[:width]}{more}")
    sys.stdout.flush()


def role() -> str:
    try:
        for line in (HOME / "env").read_text().splitlines():
            if line.startswith("ROLE="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return "?"


def event(line: str) -> None:
    try:
        m = json.loads(line)
    except ValueError:
        if line.strip():
            say("note", line, "33")
        return
    kind = m.get("type")
    if kind == "assistant":
        for part in m.get("message", {}).get("content", []):
            if part.get("type") == "text" and part["text"].strip():
                say("says", part["text"], "36")
            elif part.get("type") == "tool_use":
                inp = part.get("input", {})
                what = (
                    inp.get("description")
                    or inp.get("command")
                    or inp.get("file_path")
                    or inp.get("skill")
                    or inp.get("prompt")
                    or json.dumps(inp)
                )
                say("does", f"{part['name']}: {what}", "34")
    elif kind == "user":
        content = m.get("message", {}).get("content")
        for part in content if isinstance(content, list) else []:
            if part.get("type") == "tool_result" and part.get("is_error"):
                body = part.get("content")
                text = body if isinstance(body, str) else json.dumps(body)
                say("error", text, "31")
    elif kind == "result":
        secs = (m.get("duration_ms") or 0) / 1000
        say(
            "done",
            f"session ended ({m.get('subtype')}), {secs / 60:.0f} min, {m.get('num_turns')} turns",
            "32;1",
        )
    elif kind == "system" and m.get("subtype") == "init":
        say("start", f"new session in {m.get('cwd')} ({m.get('permissionMode')})", "32;1")


def newest_session() -> Path | None:
    logs = list(LOGS.glob("*.jsonl"))
    return max(logs, key=lambda p: p.stat().st_mtime) if logs else None


def main() -> None:
    me = role()
    other = "B" if me == "a" else "A"
    print(
        c(
            "1",
            f"Watching machine {me.upper()}'s loop (Ctrl+C stops watching only)."
            f" Machine {other} is shown from Drive once a minute.",
        )
    )
    agent_log = LOGS / "agent.log"
    agent_pos = max(0, agent_log.stat().st_size - 600) if agent_log.exists() else 0
    session: Path | None = None
    session_pos = 0
    last_other = ""
    next_other = 0.0
    first = True
    while True:
        if agent_log.exists():
            with open(agent_log, encoding="utf-8", errors="replace") as f:
                f.seek(agent_pos)
                for line in f.read().splitlines():
                    if line.strip():
                        say("loop", line.split(" ", 1)[-1], "35;1")
                agent_pos = f.tell()
        latest = newest_session()
        if latest and latest != session:
            session = latest
            say("log", f"following {latest.name}", "2")
            lines = latest.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in lines[-25:] if first else lines:
                event(line)
            session_pos = latest.stat().st_size
            first = False
        elif session and session.exists() and session.stat().st_size > session_pos:
            with open(session, "rb") as f:
                f.seek(session_pos)
                chunk = f.read()
            cut = chunk.rfind(b"\n") + 1  # only whole lines
            for line in chunk[:cut].decode("utf-8", "replace").splitlines():
                event(line)
            session_pos += cut
        if time.time() >= next_other:
            next_other = time.time() + 60
            try:
                import drive

                raw = drive.read_text(f"status/{other}.json") or "{}"
                st = json.loads(raw)
                line = (
                    f"machine {other}: {st.get('state')}"
                    + (f" · {st['job']}" if st.get("job") else "")
                    + f" · heartbeat {st.get('at', '?')}"
                )
                if line != last_other:
                    say("peer", line, "33")
                    last_other = line
            except Exception as e:
                say("peer", f"cannot read Drive: {e}", "31")
        time.sleep(2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
