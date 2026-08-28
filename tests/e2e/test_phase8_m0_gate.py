"""M0 — the pipeline gate, run as a test so it cannot rot between releases."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app import selftest


def test_m0_pipeline_gate(tmp_path: Path, app_home: Path) -> None:
    report = tmp_path / "m0.json"
    code = selftest.main(["pipeline", "--report", str(report), "--quiet"])
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert code == 0, payload
    assert payload["ok"] is True
    names = {check["name"]: check for check in payload["checks"]}
    assert names["pipeline_artifacts"]["metrics"]["artifacts"] == {
        "transcript.json": True,
        "transcript.md": True,
        "notes.json": True,
        "summary.html": True,
        "summary.email.html": True,
        "meta.json": True,
    }
    assert names["pipeline_state"]["metrics"]["state"] in ("RENDERED", "DELIVERED")
    assert names["pipeline_log_clean"]["ok"] is True
    assert names["pipeline_within_budget"]["metrics"]["seconds"] < 120


def test_m0_accepts_a_wav_input(tmp_path: Path, app_home: Path) -> None:
    """The imported path: a WAV on disk becomes a summary."""
    import wave

    import numpy as np

    wav = tmp_path / "meeting_10min.wav"
    rng = np.random.default_rng(3)
    payload = (rng.normal(0, 0.12, 16000 * 90) * 32767).astype("<i2")
    with wave.open(str(wav), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(payload.tobytes())

    args = argparse.Namespace(input=wav, budget_s=180.0, seconds=60)
    report = selftest.run("pipeline", args)
    assert report["ok"] is True, report
    detail = next(c for c in report["checks"] if c["name"] == "pipeline_source")["detail"]
    assert "imported meeting_10min.wav" in detail
