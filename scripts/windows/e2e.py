"""Run the browser specs against the app **on Windows**, driven from WSL.

    python3 scripts/windows/e2e.py [--port 8130] [--grep pattern]

Why this exists: Chromium inside WSL produces zero animation frames, so Playwright's
actionability checks never settle and every click times out — the browser tier cannot run
there at all. A browser on Windows paints normally (61 fps measured), and because WSL is
in mirrored networking mode, both sides share `127.0.0.1`: the specs reach the app, the
CDP endpoint reaches the browser, and nothing is exposed beyond loopback.

What it owns, and tears down again: a scratch app instance with its own data folder and
fixed auth secrets, a headless Chrome with its own profile, and the artifacts both leave
behind. It never touches the app the user is running — different port, different
`MA_HOME`, and deliberately not `run-app.ps1`, which stops every `app.main` it finds.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SESSION = "harness-session"
CSRF = "harness-csrf"
#: Where `run-app.ps1` keeps the Windows-side interpreter, relative to %LOCALAPPDATA%.
VENV_SUFFIX = Path("meeting-agent-win/venv/Scripts/python.exe")


def powershell(script: str, timeout: float = 60) -> str:
    done = subprocess.run(
        ["/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe", "-NoProfile",
         "-Command", script],
        capture_output=True, text=True, timeout=timeout, check=False,
    )
    return done.stdout.strip()


def windows_paths() -> dict[str, str]:
    """Ask Windows where things are, rather than assuming this machine's layout.

    Everything here differs between installs: the account name, the WSL distribution, the
    drive Chrome landed on. Hardcoding any of them makes a harness that runs here and
    nowhere else, which is worse than one that says plainly what it could not find.
    """
    found = powershell(
        "@{ local = $env:LOCALAPPDATA; temp = $env:TEMP; "
        "chrome = @(\"$env:ProgramFiles\\Google\\Chrome\\Application\\chrome.exe\", "
        "\"${env:ProgramFiles(x86)}\\Google\\Chrome\\Application\\chrome.exe\", "
        "\"$env:LOCALAPPDATA\\Google\\Chrome\\Application\\chrome.exe\", "
        "\"$env:ProgramFiles\\Microsoft\\Edge\\Application\\msedge.exe\", "
        "\"${env:ProgramFiles(x86)}\\Microsoft\\Edge\\Application\\msedge.exe\") "
        "| Where-Object { Test-Path $_ } | Select-Object -First 1 "
        "} | ConvertTo-Json -Compress"
    )
    try:
        return dict(json.loads(found))
    except ValueError:
        return {}


def to_wsl(windows_path: str) -> Path:
    return Path(subprocess.run(
        ["wslpath", "-u", windows_path], capture_output=True, text=True, check=True
    ).stdout.strip())


def to_windows(path: Path) -> str:
    """The UNC form Windows needs for a file that lives in this distribution."""
    return subprocess.run(
        ["wslpath", "-w", str(path)], capture_output=True, text=True, check=True
    ).stdout.strip()


def free_port(start: int) -> int:
    for candidate in range(start, start + 40):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", candidate)) != 0:
                return candidate
    raise RuntimeError("no free port in range")


def wait_for(url: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    return False


def kill_windows(match: str) -> int:
    """Stop a Windows process tree by a distinctive fragment of its command line.

    `Popen.terminate` is not enough from here: a Windows program launched through WSL
    interop gets a shim process on this side, and killing the shim leaves the real process
    running. Seven orphaned browsers accumulated before this was noticed.
    """
    script = (
        "$p = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "
        f"'{match}'" + " }; $p | ForEach-Object { & taskkill.exe /PID $_.ProcessId /T /F "
        "> $null 2>&1 }; @($p).Count"
    )
    try:
        done = subprocess.run(
            ["/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe", "-NoProfile",
             "-Command", script],
            capture_output=True, text=True, timeout=120, check=False,
        )
        return int((done.stdout.strip() or "0").splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0


def windows_events(since: float) -> list[str]:
    """Faults Windows recorded while the run was going on.

    A native crash kills the process before Python can log a word — that is how a toast
    bug took the whole application down with nothing in `app.log`. Windows records it
    anyway, so a run that dies silently still has something to show.
    """
    when = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(since))
    script = (
        "Get-WinEvent -FilterHashtable @{LogName='Application'; "
        "ProviderName='Application Error','Windows Error Reporting'; "
        f"StartTime=(Get-Date '{when}')}} -ErrorAction SilentlyContinue | "
        "ForEach-Object { $_.TimeCreated.ToString('HH:mm:ss') + ' ' + "
        "($_.Message -split \"`n\")[0] }"
    )
    try:
        found = subprocess.run(
            ["/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe", "-NoProfile",
             "-Command", script],
            capture_output=True, text=True, timeout=120, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in found.stdout.splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8130)
    parser.add_argument("--cdp-port", type=int, default=9222, dest="cdp_port")
    parser.add_argument("--grep", default=None, help="run only specs matching this")
    parser.add_argument("--keep", action="store_true", help="leave the app and browser up")
    args = parser.parse_args(argv)

    where = windows_paths()
    if not where.get("local") or not where.get("chrome"):
        print("could not find %LOCALAPPDATA% or a Chromium browser on the Windows side")
        return 2
    venv_python = to_wsl(where["local"]) / VENV_SUFFIX
    browser_exe = to_wsl(where["chrome"])
    if not venv_python.exists():
        print(f"no Windows venv at {venv_python} — run scripts/windows/run-app.cmd once")
        return 2

    port = free_port(args.port)
    cdp = free_port(args.cdp_port)
    home = to_wsl(where["local"]) / f"ma-e2e-{port}"
    profile = f"{where['temp']}\\ma-e2e-chrome-{port}"
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    started = time.time()

    environment = dict(os.environ)
    environment.update(
        MA_E2E_PORT=str(port),
        MA_E2E_SESSION=SESSION,
        MA_E2E_CSRF=CSRF,
        MA_TEST_MODE="1",
        MA_HOME=to_windows(home),
        # Variables do not cross into a Windows process unless they are named here.
        WSLENV="MA_E2E_PORT:MA_E2E_SESSION:MA_E2E_CSRF:MA_TEST_MODE:MA_HOME",
    )

    app_log = (artifacts / "windows-app.log").open("w")
    app = subprocess.Popen(
        [str(venv_python), "-u", to_windows(ROOT / "scripts" / "e2e_server.py")],
        stdout=app_log, stderr=subprocess.STDOUT, env=environment,
    )
    browser = subprocess.Popen(
        [str(browser_exe), "--headless=new", f"--remote-debugging-port={cdp}",
         f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    code = 1
    try:
        if not wait_for(f"http://127.0.0.1:{port}/api/health", 90):
            print(f"the app never came up on {port}; see artifacts/windows-app.log")
            return 1
        if not wait_for(f"http://127.0.0.1:{cdp}/json/version", 60):
            print(f"no CDP endpoint on {cdp}")
            return 1
        print(f"app on 127.0.0.1:{port}, browser on 127.0.0.1:{cdp}")

        command = ["npx", "playwright", "test", "--config", "playwright.windows.config.ts"]
        if args.grep:
            command += ["--grep", args.grep]
        run = subprocess.run(
            command,
            cwd=ROOT / "frontend",
            env={**environment, "MA_E2E_CDP": f"http://127.0.0.1:{cdp}"},
            check=False,
        )
        code = run.returncode
    finally:
        if not args.keep:
            # By the profile directory and the port: both are unique to this run, so a
            # browser or app instance the user started is never touched.
            kill_windows(f"ma-e2e-chrome-{port}")
            kill_windows(f"MA_E2E_PORT|e2e_server.*{port}")
            for process in (app, browser):
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
            app_log.close()
            shutil.rmtree(home, ignore_errors=True)
            shutil.rmtree(to_wsl(profile), ignore_errors=True)

    faults = windows_events(started)
    if faults:
        # A fault during the run is a failure even if every spec passed: something died.
        print("\nWindows recorded a fault during this run:")
        for line in faults:
            print("  " + line)
        code = code or 1
    summary = {
        "port": port,
        "ok": code == 0,
        "faults": faults,
        "seconds": round(time.time() - started),
    }
    (ROOT / "artifacts" / "e2e-windows-summary.json").write_text(json.dumps(summary, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
