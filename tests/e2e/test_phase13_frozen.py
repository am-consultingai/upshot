"""The frozen-build gates. They run only where a build exists (Windows).

`docs/windows-run.md` §5 carries the command that produces it.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.windows

DIST = Path("dist/upshot")
EXE = DIST / "upshot.exe"
BASELINE = Path("packaging/installer-size.json")


@pytest.fixture(autouse=True)
def _requires_freeze() -> None:
    if not EXE.exists():
        pytest.skip(f"no frozen build at {EXE} — run packaging/build.ps1 first")


def _run(*args: str, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(EXE), *args], capture_output=True, text=True, timeout=timeout, check=False
    )


def test_frozen_imports_all(tmp_path: Path) -> None:
    """The classic PyInstaller failure, caught by the build rather than by a user."""
    report = tmp_path / "imports.json"
    result = _run("--selftest", "imports", "--report", str(report))
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(report.read_text(encoding="utf-8"))
    check = payload["checks"][0]
    assert check["ok"] is True, check["detail"]
    assert check["metrics"]["modules"] > 40


def test_frozen_selftest_pipeline(tmp_path: Path) -> None:
    report = tmp_path / "m0.json"
    result = _run("--selftest", "pipeline", "--report", str(report))
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    artifacts = next(check for check in payload["checks"] if check["name"] == "pipeline_artifacts")
    assert artifacts["metrics"]["artifacts"]["summary.html"] is True


def test_frozen_resources_present() -> None:
    internal = DIST / "_internal"
    root = internal if internal.exists() else DIST
    assert (root / "app" / "llm" / "prompts" / "system.md").exists()
    assert (root / "app" / "db" / "migrations" / "0001_initial.sql").exists()
    assert (DIST / "ffmpeg.exe").exists() or (root / "ffmpeg.exe").exists()
    assert (root / "frontend" / "dist" / "index.html").exists()


def test_installer_builds() -> None:
    """A sudden size jump means something large was bundled by accident."""
    setup = Path("dist/Setup.exe")
    if not setup.exists():
        pytest.skip("no Setup.exe — Inno Setup was not run")
    size = setup.stat().st_size
    if not BASELINE.exists():
        BASELINE.write_text(json.dumps({"bytes": size}, indent=2), encoding="utf-8")
        pytest.skip(f"recorded a new installer size baseline: {size} bytes")
    baseline = int(json.loads(BASELINE.read_text(encoding="utf-8"))["bytes"])
    assert abs(size - baseline) / baseline <= 0.25, (
        f"installer is {size} bytes against a {baseline} baseline"
    )


def test_task_scheduler_registration(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("Task Scheduler is Windows-only")
    from app.bootstrap import TASK_NAME, register_logon_task, task_exists, unregister_logon_task

    task = f"{TASK_NAME}Test"
    step = register_logon_task(EXE.resolve(), task_name=task)
    try:
        assert step.ok, step.detail
        assert task_exists(task)
    finally:
        unregister_logon_task(task)
    assert not task_exists(task)
