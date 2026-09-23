"""Phase 14 — the three milestone gates, as tests, so they cannot rot."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import selftest


def run_gate(name: str, tmp_path: Path, *extra: str) -> dict[str, object]:
    report = tmp_path / f"{name}.json"
    code = selftest.main([name, "--report", str(report), "--quiet", *extra])
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert code == 0, payload
    return payload


def test_m0_pipeline(tmp_path: Path, app_home: Path) -> None:
    payload = run_gate("pipeline", tmp_path)
    assert payload["ok"] is True


@pytest.mark.slow
def test_m1_capture_e2e(tmp_path: Path, app_home: Path) -> None:
    """The whole product with no human in the loop — 20 s here, 120 s in the gate."""
    payload = run_gate("capture-e2e", tmp_path, "--seconds", "20")
    checks = {check["name"]: check for check in payload["checks"]}  # type: ignore[union-attr]
    assert payload["ok"] is True
    assert checks["capture_duration"]["metrics"]["durations_ms"] == {
        "me": 20_000,
        "them": 20_000,
    }
    assert checks["capture_them_correlates"]["ok"] is True, checks["capture_them_correlates"]
    assert checks["capture_language_pinned"]["metrics"]["language"] == "en"
    assert checks["capture_tray_sequence"]["metrics"]["states"] == [
        "idle",
        "recording",
        "processing",
        "idle",
    ]
    assert checks["capture_pipeline_rendered"]["metrics"]["state"] in ("RENDERED", "DELIVERED")


def test_m2_detect_e2e(tmp_path: Path, app_home: Path) -> None:
    payload = run_gate("detect-e2e", tmp_path)
    assert payload["ok"] is True
