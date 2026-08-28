"""M2 — the detection gate, run as a test."""

from __future__ import annotations

import json
from pathlib import Path

from app import selftest


def test_m2_detect_e2e_gate(tmp_path: Path, app_home: Path) -> None:
    report = tmp_path / "m2.json"
    code = selftest.main(["detect-e2e", "--report", str(report), "--quiet"])
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert code == 0, payload
    names = {check["name"]: check for check in payload["checks"]}
    assert set(names) == {
        "detect_commits_a_meeting",
        "video_never_wakes_the_detector",
        "media_app_is_a_near_miss",
        "shadow_commits_nothing",
    }
    assert all(check["ok"] for check in payload["checks"])
    evidence = names["detect_commits_a_meeting"]["metrics"]["evidence"]
    assert "mic.known_app" in evidence and "window.title" in evidence


def test_detect_shadow_reports_or_skips(tmp_path: Path, app_home: Path) -> None:
    """On Linux there is no ConsentStore, so this reports a skip rather than a pass."""
    report = tmp_path / "shadow.json"
    code = selftest.main(["detect-shadow", "--seconds", "2", "--report", str(report), "--quiet"])
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert code == 0
    check = payload["checks"][0]
    assert check["name"] == "detect_shadow"
    assert check["ok"] is True
    assert check["detail"].startswith("skipped:") or "samples" in check["detail"]
