from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app import selftest
from app.clock import FakeClock, SystemClock
from app.introspect import import_all_modules, module_names


def test_imports_all_modules() -> None:
    names, failures = import_all_modules()
    assert not failures, failures
    assert "app.selftest" in names
    assert len(names) == len(module_names())


def test_selftest_reports_json(tmp_path: Path, app_home: Path) -> None:
    report = tmp_path / "r.json"
    code = selftest.main(["all", "--report", str(report), "--quiet"])
    assert code == 0
    data = json.loads(report.read_text(encoding="utf-8"))
    assert set(data) == {"suite", "ok", "checks"}
    assert data["suite"] == "all"
    assert isinstance(data["ok"], bool) and data["ok"] is True
    assert data["checks"]
    for check in data["checks"]:
        assert set(check) == {"name", "ok", "detail", "metrics"}
        assert isinstance(check["name"], str)
        assert isinstance(check["ok"], bool)
        assert isinstance(check["detail"], str)
        assert isinstance(check["metrics"], dict)


def test_selftest_runs_as_a_module(tmp_path: Path) -> None:
    env = {**dict(**__import__("os").environ), "UP_HOME": str(tmp_path / "home")}
    proc = subprocess.run(
        [sys.executable, "-m", "app.selftest", "imports", "--quiet"],
        capture_output=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr.decode()


def test_selftest_unknown_suite_fails() -> None:
    report = selftest.run("nope", selftest.argparse.Namespace())
    assert report["ok"] is False


def test_fake_clock_monotonic() -> None:
    clock = FakeClock()
    before, mono = clock.now(), clock.monotonic()
    clock.advance(5)
    assert (clock.now() - before).total_seconds() == 5.0
    assert clock.monotonic() - mono == 5.0
    # no wall-clock leakage: a second instance is identical
    assert FakeClock().now() == FakeClock().now()
    assert FakeClock().now() != SystemClock().now()


def test_fake_clock_sleep_advances() -> None:
    clock = FakeClock()
    clock.sleep(2.5)
    assert clock.slept == [2.5]
    assert clock.monotonic() == 2.5


def test_fake_clock_rejects_naive_datetimes() -> None:
    from datetime import datetime

    with pytest.raises(ValueError):
        FakeClock(datetime(2026, 1, 1))
