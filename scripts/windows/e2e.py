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
`UP_HOME`, and deliberately not `run-app.ps1`, which stops every `app.main` it finds.

WSL's default NAT networking has no shared loopback: this side cannot reach the app at
all (machine B). There the harness stays on the Windows side for everything that talks
to the app — the probes go through PowerShell and Playwright runs on Windows' own Node —
which needs the checkout on a Windows drive (`/mnt/c/...`) with `npm ci` run there.
"""

from __future__ import annotations

import argparse
import json
import os
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
VENV_SUFFIX = Path("upshot-win/venv/Scripts/python.exe")


def powershell(script: str, timeout: float = 60) -> str:
    done = subprocess.run(
        [
            "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
            "-NoProfile",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
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
        'chrome = @("$env:ProgramFiles\\Google\\Chrome\\Application\\chrome.exe", '
        '"${env:ProgramFiles(x86)}\\Google\\Chrome\\Application\\chrome.exe", '
        '"$env:LOCALAPPDATA\\Google\\Chrome\\Application\\chrome.exe", '
        '"$env:ProgramFiles\\Microsoft\\Edge\\Application\\msedge.exe", '
        '"${env:ProgramFiles(x86)}\\Microsoft\\Edge\\Application\\msedge.exe") '
        "| Where-Object { Test-Path $_ } | Select-Object -First 1 "
        "} | ConvertTo-Json -Compress"
    )
    try:
        return dict(json.loads(found))
    except ValueError:
        return {}


def to_wsl(windows_path: str) -> Path:
    return Path(
        subprocess.run(
            ["wslpath", "-u", windows_path], capture_output=True, text=True, check=True
        ).stdout.strip()
    )


def to_windows(path: Path) -> str:
    """The UNC form Windows needs for a file that lives in this distribution."""
    return subprocess.run(
        ["wslpath", "-w", str(path)], capture_output=True, text=True, check=True
    ).stdout.strip()


def networking_mode() -> str:
    """``mirrored`` or ``nat`` (WSL's default), from ``wslinfo``; ``nat`` when unsure."""
    try:
        done = subprocess.run(
            ["wslinfo", "--networking-mode"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return "nat"
    return done.stdout.strip().lower() or "nat"


def windows_listening_ports() -> set[int]:
    found = powershell(
        "(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue).LocalPort "
        "| Sort-Object -Unique"
    )
    return {int(line) for line in found.split() if line.strip().isdigit()}


def free_windows_port(start: int, busy: set[int]) -> int:
    for candidate in range(start, start + 40):
        if candidate not in busy:
            return candidate
    raise RuntimeError("no free port in range")


def wait_for_windows(url: str, timeout: float) -> bool:
    """`wait_for`, asked from the Windows side: under NAT only Windows sees its loopback."""
    script = (
        f"$deadline = (Get-Date).AddSeconds({int(timeout)}); "
        "while ((Get-Date) -lt $deadline) { "
        f"try {{ Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 '{url}' > $null; 'up'; exit }} "
        "catch { Start-Sleep -Milliseconds 500 } }; 'down'"
    )
    return powershell(script, timeout=timeout + 30).endswith("up")


def playwright_command(grep: str | None, *, windows_node: bool) -> list[str]:
    command = ["npx", "playwright", "test", "--config", "playwright.windows.config.ts"]
    if grep:
        command += ["--grep", grep]
    if windows_node:
        return ["/mnt/c/Windows/System32/cmd.exe", "/c", *command]
    return command


def free_port(start: int) -> int:
    """The first port in range with nothing listening on it.

    The timeout is the whole point. A closed loopback port normally refuses at
    once, but under WSL's mirrored networking a connection to the Windows side can
    be dropped instead of refused — and a blocking `connect_ex` then waits out the
    kernel's SYN retries, about two minutes. Called twice, that added roughly four
    and a half minutes to a run whose tests take one, and it looked for all the
    world like the suite hanging.

    A probe that times out means nothing answered, which is exactly the port we
    want, so the timeout is not an error here.
    """
    for candidate in range(start, start + 40):
        with socket.socket() as probe:
            probe.settimeout(0.25)
            try:
                if probe.connect_ex(("127.0.0.1", candidate)) != 0:
                    return candidate
            except OSError:
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
            [
                "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
                "-NoProfile",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        return int((done.stdout.strip() or "0").splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0


def remove_on_windows(paths: list[str]) -> None:
    """Delete run directories from the Windows side, in one call.

    `shutil.rmtree` on these is correct and unusably slow: both live on the Windows
    filesystem, and every unlink crosses the 9p boundary. A Chrome profile is tens
    of thousands of small files, so teardown took around four minutes against
    roughly one minute of actual tests — which read, from outside, exactly like the
    suite hanging. It was what sent me looking for a deadlock that did not exist.

    Windows deletes its own files at native speed, so it is asked to.
    """
    quoted = ",".join(f"'{path}'" for path in paths)
    powershell(
        f"foreach ($p in @({quoted})) {{ "
        "if (Test-Path -LiteralPath $p) { "
        "Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction SilentlyContinue } }",
        timeout=180,
    )


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
        '($_.Message -split "`n")[0] }'
    )
    try:
        found = subprocess.run(
            [
                "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
                "-NoProfile",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in found.stdout.splitlines() if line.strip()]


class Phases:
    """Wall-clock per phase, printed at the end.

    A run that spends one minute on tests and five somewhere else is not a slow
    suite, it is a suite with a problem elsewhere — and from outside the two are
    indistinguishable. Teardown was four of those minutes before it was measured.
    """

    def __init__(self) -> None:
        self.marks: list[tuple[str, float]] = []
        self.last = time.monotonic()

    def mark(self, name: str) -> None:
        now = time.monotonic()
        self.marks.append((name, now - self.last))
        self.last = now

    def report(self) -> str:
        return "  ".join(f"{name} {seconds:.0f}s" for name, seconds in self.marks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8130)
    parser.add_argument("--cdp-port", type=int, default=9222, dest="cdp_port")
    parser.add_argument("--grep", default=None, help="run only specs matching this")
    parser.add_argument("--keep", action="store_true", help="leave the app and browser up")
    parser.add_argument(
        "--python",
        default=None,
        help="the Windows venv's python.exe (WSL path), when it is not where run-app.ps1 "
        "puts it by default: a launcher run with -WorkDir, or a test machine's job folder",
    )
    args = parser.parse_args(argv)

    where = windows_paths()
    if not where.get("local") or not where.get("chrome"):
        print("could not find %LOCALAPPDATA% or a Chromium browser on the Windows side")
        return 2
    venv_python = Path(args.python) if args.python else to_wsl(where["local"]) / VENV_SUFFIX
    browser_exe = to_wsl(where["chrome"])
    if not venv_python.exists():
        print(f"no Windows venv at {venv_python} — run scripts/windows/run-app.cmd once")
        return 2

    windows_node = networking_mode() != "mirrored"
    if windows_node and not str(ROOT).startswith("/mnt/"):
        print(
            "WSL is in NAT mode, so the specs must run on Windows' Node, which cannot "
            f"work in {ROOT}: use a checkout on a Windows drive, or mirrored networking"
        )
        return 2

    phases = Phases()
    if windows_node:
        busy = windows_listening_ports()
        port = free_windows_port(args.port, busy)
        cdp = free_windows_port(args.cdp_port, busy)
        reachable = wait_for_windows
    else:
        port = free_port(args.port)
        cdp = free_port(args.cdp_port)
        reachable = wait_for
    print(f"networking: {'NAT, specs on Windows Node' if windows_node else 'mirrored'}")
    home = to_wsl(where["local"]) / f"ma-e2e-{port}"
    profile = f"{where['temp']}\\ma-e2e-chrome-{port}"
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    started = time.time()

    environment = dict(os.environ)
    environment.update(
        UP_E2E_PORT=str(port),
        UP_E2E_SESSION=SESSION,
        UP_E2E_CSRF=CSRF,
        UP_TEST_MODE="1",
        UP_HOME=to_windows(home),
        UP_E2E_CDP=f"http://127.0.0.1:{cdp}",
        # Variables do not cross into a Windows process unless they are named here.
        WSLENV="UP_E2E_PORT:UP_E2E_SESSION:UP_E2E_CSRF:UP_TEST_MODE:UP_HOME:UP_E2E_CDP",
    )

    phases.mark("discover")
    app_log = (artifacts / "windows-app.log").open("w")
    # The port is passed on argv as well as in the environment. It is redundant for
    # configuration and essential for teardown: a Windows process started through
    # WSL interop can only be found again by its command line, and an environment
    # variable is not part of that.
    app = subprocess.Popen(
        [str(venv_python), "-u", to_windows(ROOT / "scripts" / "e2e_server.py"), str(port)],
        stdout=app_log,
        stderr=subprocess.STDOUT,
        env=environment,
    )
    browser = subprocess.Popen(
        [
            str(browser_exe),
            "--headless=new",
            f"--remote-debugging-port={cdp}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    code = 1
    try:
        # A failed start falls through to teardown and the summary, so even that run
        # leaves e2e-windows-summary.json behind.
        if not reachable(f"http://127.0.0.1:{port}/api/health", 90):
            print(f"the app never came up on {port}; see artifacts/windows-app.log")
        elif not reachable(f"http://127.0.0.1:{cdp}/json/version", 60):
            print(f"no CDP endpoint on {cdp}")
        else:
            phases.mark("startup")
            print(f"app on 127.0.0.1:{port}, browser on 127.0.0.1:{cdp}")
            run = subprocess.run(
                playwright_command(args.grep, windows_node=windows_node),
                cwd=ROOT / "frontend",
                env=environment,
                check=False,
            )
            code = run.returncode
            phases.mark("tests")
    finally:
        if not args.keep:
            # By the profile directory and the port: both are unique to this run, so a
            # browser or app instance the user started is never touched.
            # Both matches must appear on a *command line*, which is the only thing
            # visible to Win32_Process from here. The previous app matcher looked for
            # `UP_E2E_PORT`, an environment variable that appears on no command line,
            # so the app was never killed at all: three consecutive runs left three
            # live instances and a hundred browser processes behind, and the third run
            # slowed to a crawl against its own litter. Hence the port on argv.
            killed_browsers = kill_windows(f"ma-e2e-chrome-{port}")
            killed_apps = kill_windows(f"e2e_server\\.py.*{port}(\\s|$)")
            for process in (app, browser):
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
            # Teardown that silently does nothing is how the litter accumulated
            # unnoticed, so it says what it stopped.
            print(f"stopped {killed_apps} app and {killed_browsers} browser processes")
            phases.mark("kill")
            app_log.close()
            remove_on_windows([to_windows(home), profile])
            phases.mark("clean")

    faults = windows_events(started)
    phases.mark("events")
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
    print("phases: " + phases.report())
    summary["phases"] = {name: round(seconds) for name, seconds in phases.marks}
    (ROOT / "artifacts" / "e2e-windows-summary.json").write_text(json.dumps(summary, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
