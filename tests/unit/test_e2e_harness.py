"""scripts/windows/e2e.py under WSL's default NAT networking (found on machine B).

Only the pure parts run here; the Windows half is exercised by the harness itself.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "windows" / "e2e.py"


@pytest.fixture(scope="module")
def e2e() -> ModuleType:
    spec = importlib.util.spec_from_file_location("e2e_harness", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_playwright_runs_on_windows_node_under_nat(e2e: ModuleType) -> None:
    windows = e2e.playwright_command("smoke", windows_node=True)
    assert windows[:2] == ["/mnt/c/Windows/System32/cmd.exe", "/c"]
    assert windows[2:] == e2e.playwright_command("smoke", windows_node=False)
    assert windows[-2:] == ["--grep", "smoke"]


def test_windows_ports_skip_what_is_listening(e2e: ModuleType) -> None:
    assert e2e.free_windows_port(8130, {8130, 8131}) == 8132
    with pytest.raises(RuntimeError):
        e2e.free_windows_port(8130, set(range(8130, 8170)))


def test_an_unknown_networking_mode_is_treated_as_nat(
    e2e: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("wslinfo")

    monkeypatch.setattr(subprocess, "run", missing)
    assert e2e.networking_mode() == "nat"


def test_only_the_runs_own_faults_fail_it(e2e: ModuleType) -> None:
    driver = (
        "00:11:16 Faulting application name: dptf_helper.exe, version: 9.0.11905.54015, "
        "time stamp: 0x6655d1a8"
    )
    app = "00:12:01 Faulting application name: python.exe, version: 3.13.5150.1013"
    browser = "00:12:30 Faulting application name: chrome.exe, version: 140.0.7339.80"
    assert e2e.our_faults([driver]) == []
    assert e2e.our_faults([driver, app, browser]) == [app, browser]
