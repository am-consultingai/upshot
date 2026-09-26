"""The unattended loop on each machine. Read RUNBOOK.md first.

    python3 agent.py a     # machine A: runs "shifts" of the driver session
    python3 agent.py b     # machine B: runs jobs that A posts to Drive

loop.sh runs this forever (and refreshes it from origin/main between runs),
so one call does one unit of work and returns: one shift on A, one job on B,
or a short idle wait when there is nothing to do.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

HOME = Path(os.environ.get("UPSHOT_AGENT_HOME", "~/upshot-agent")).expanduser()
sys.path.insert(0, str(HOME))
import drive  # noqa: E402  (copied next to this file by loop.sh)

LOGS = HOME / "logs"
JOBS = HOME / "jobs"
STOP_LOCAL = HOME / "STOP"
# Written by A's shift: {"job": "<id>", "until": "<iso>"}. It lives in scratch/, the one folder
# A's allowlist lets it write: the allowlist denies ~/upshot-agent/*.json and *.md, and a deny
# beats an allow, so ~/upshot-agent/wait.json can't be written. The old path is still read.
WAIT_FILES = (HOME / "scratch" / "wait.json", HOME / "wait.json")
DONE_FILE = HOME / "DONE"  # written by A's shift when the epic is finished
BACKOFF_FILE = HOME / "backoff.json"
JOB_TIMEOUT_S = 4 * 3600
SHIFT_TIMEOUT_S = 3 * 3600
IDLE_S = 60


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def log(msg: str) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    line = f"{now()} {msg}"
    print(line, flush=True)
    with open(LOGS / "agent.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def heartbeat(role: str, state: str, **extra: object) -> None:
    try:
        drive.write_json(
            f"status/{role.upper()}.json",
            {"machine": socket.gethostname(), "state": state, "at": now(), **extra},
        )
    except Exception as e:  # a missed heartbeat must never stop the loop
        log(f"heartbeat failed: {e}")


def stopped() -> str | None:
    if STOP_LOCAL.exists():
        return f"local {STOP_LOCAL}"
    if DONE_FILE.exists():
        return "the epic is finished (DONE)"
    try:
        if drive.resolve("STOP"):
            return "Drive projects/upshot/STOP"
    except Exception as e:
        log(f"cannot read Drive: {e}")
        return "Drive unreachable"
    return None


def backing_off() -> bool:
    try:
        until = json.loads(BACKOFF_FILE.read_text())["until"]
    except (OSError, ValueError, KeyError):
        return False
    return time.time() < until


def claude(prompt: str, cwd: Path, settings: Path, logfile: Path, timeout: int) -> tuple[int, str]:
    """Run one headless Claude session with the machine's allowlist; return (code, tail)."""
    exe = shutil.which("claude") or str(Path("~/.local/bin/claude").expanduser())
    cmd = [
        exe, "-p", prompt,
        "--settings", str(settings),
        "--permission-mode", "dontAsk",
        "--output-format", "stream-json", "--verbose",
    ]  # fmt: skip
    logfile.parent.mkdir(parents=True, exist_ok=True)
    with open(logfile, "ab") as out:
        try:
            code = subprocess.run(
                cmd, cwd=cwd, stdout=out, stderr=subprocess.STDOUT, timeout=timeout
            ).returncode
        except subprocess.TimeoutExpired:
            code = 124
    tail = logfile.read_bytes()[-4000:].decode("utf-8", "replace")
    low = tail.lower()
    if code != 0 and (
        "rate limit" in low or "usage limit" in low or "limit reached" in low or "429" in low
    ):
        BACKOFF_FILE.write_text(json.dumps({"until": time.time() + 45 * 60, "why": "usage limit"}))
        log("usage limit reached; backing off 45 minutes")
    if "not logged in" in low or "please run /login" in low or "invalid api key" in low:
        BACKOFF_FILE.write_text(
            json.dumps({"until": time.time() + 6 * 3600, "why": "claude signed out"})
        )
        log("claude is signed out on this machine; needs the user (see RUNBOOK 'Needs you')")
    return code, tail


# ---------------------------------------------------------------- machine B


def next_job() -> str | None:
    jobs = sorted(c["name"] for c in drive.ls("jobs") if c["mimeType"] == drive.FOLDER)
    for job in jobs:
        # A job folder without job.md is still being uploaded (job 015 was claimed half-way).
        if drive.resolve(f"jobs/{job}/job.md") and not drive.resolve(f"results/{job}/result.json"):
            return job
    return None


def run_job(job: str) -> None:
    work = JOBS / job
    out = work / "out"
    work.mkdir(parents=True, exist_ok=True)
    drive.pull(f"jobs/{job}", work / "job")
    out.mkdir(exist_ok=True)
    resumed = drive.resolve(f"results/{job}/claimed.json") is not None
    drive.write_json(
        f"results/{job}/claimed.json",
        {"at": now(), "machine": socket.gethostname(), "resumed": resumed},
    )
    heartbeat("b", "working", job=job)
    log(f"job {job}: start{' (resumed after a restart)' if resumed else ''}")
    prompt = (HOME / "prompt-b.md").read_text().replace("{JOB}", job).replace("{WORK}", str(work))
    if resumed:
        prompt += (
            "\n\nNote: a previous attempt at this job was interrupted (restart or crash)."
            " Check `out/` for what it left and continue.\n"
        )
    code, tail = claude(
        prompt, work, HOME / "allowlist-b.json", LOGS / f"{job}.jsonl", JOB_TIMEOUT_S
    )
    if not (out / "result.json").exists():
        (out / "result.json").write_text(json.dumps({
            "status": "error",
            "summary": f"the job session ended (exit {code}) without writing out/result.json",
            "tail": tail[-1500:],
        }, indent=2))  # fmt: skip
    shutil.copy(LOGS / f"{job}.jsonl", out / "session.jsonl")
    # result.json goes last: its presence is what tells A the job is finished.
    result = (out / "result.json").read_bytes()
    (out / "result.json").rename(work / "result.json.local")
    drive.push(out, f"results/{job}")
    drive.write_bytes(f"results/{job}/result.json", result, "application/json")
    log(f"job {job}: finished, exit {code}")


def role_b() -> None:
    if (why := stopped()) or backing_off():
        heartbeat("b", "paused", why=why or "backing off")
        time.sleep(5 * IDLE_S)
        return
    job = next_job()
    if job is None:
        heartbeat("b", "idle")
        time.sleep(IDLE_S)
        return
    run_job(job)
    heartbeat("b", "idle", last_job=job)


# ---------------------------------------------------------------- machine A


def waiting() -> str | None:
    """A skips its shift while it waits on a job whose result has not arrived."""
    for wait_file in WAIT_FILES:
        try:
            w = json.loads(wait_file.read_text())
        except (OSError, ValueError):
            continue
        if datetime.fromisoformat(w["until"]) < datetime.now(UTC):
            continue  # the wait timed out; the shift will look into it
        if w["job"] == "user" or not drive.resolve(f"results/{w['job']}/result.json"):
            return w["job"]
    return None


def role_a() -> None:
    if (why := stopped()) or backing_off():
        heartbeat("a", "paused", why=why or "backing off")
        time.sleep(5 * IDLE_S)
        return
    if job := waiting():
        heartbeat("a", "waiting", job=job)
        time.sleep(2 * IDLE_S)
        return
    repo = Path(os.environ["UPSHOT_A_REPO"])
    shift = datetime.now().strftime("%Y%m%d-%H%M%S")
    heartbeat("a", "working", shift=shift)
    log(f"shift {shift}: start")
    prompt = (HOME / "prompt-a.md").read_text()
    code, _ = claude(
        prompt, repo, HOME / "allowlist-a.json", LOGS / f"shift-{shift}.jsonl", SHIFT_TIMEOUT_S
    )
    log(f"shift {shift}: finished, exit {code}")
    heartbeat("a", "between shifts", last_shift=shift, exit=code)
    time.sleep(2 * IDLE_S)


if __name__ == "__main__":
    role = sys.argv[1] if len(sys.argv) > 1 else ""
    if role not in ("a", "b"):
        raise SystemExit("usage: agent.py a|b")
    try:
        role_a() if role == "a" else role_b()
    except Exception as e:
        log(f"cycle failed: {type(e).__name__}: {e}")
        time.sleep(5 * IDLE_S)
