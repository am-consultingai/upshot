"""The DETECTION.md §5 scoring table, as a table."""

from __future__ import annotations

import pytest

from app.config import default_config
from app.detect import evidence as ev
from app.detect.evidence import Evidence, score

WEIGHTS = default_config().detection_weights
KNOWN = list(default_config().get("detection.known_apps", []))
IGNORE = ["VoiceAccess.exe", "NVIDIA Broadcast.exe"]
PATTERNS = list(default_config().get("detection.title_patterns", []))


def build(
    process: str,
    *,
    mic: bool = False,
    loopback: bool = False,
    titles: tuple[str, ...] = (),
    render: tuple[str, ...] = (),
    camera: bool = False,
) -> list[Evidence]:
    found = ev.mic_evidence(process, known_apps=KNOWN, ignore=IGNORE)
    found += ev.speech_evidence(mic=mic, loopback=loopback)
    found += ev.title_evidence(list(titles), PATTERNS)
    found += ev.session_evidence(process, list(render))
    found += ev.camera_evidence(camera)
    return found


@pytest.mark.parametrize(
    ("name", "evidence", "expected", "records"),
    [
        (
            "zoom call, you talking",
            build("Zoom.exe", mic=True, loopback=True, titles=("Zoom Meeting",)),
            9,
            True,
        ),
        (
            "all-hands, you never unmute",
            build("Zoom.exe", loopback=True, titles=("Zoom Meeting",)),
            7,
            True,
        ),
        ("youtube video", build("", loopback=True), 2, False),
        ("dictation into word", build("WINWORD.EXE", mic=True), 3, False),
        ("music while working", build("", loopback=True), 2, False),
        ("whatsapp voice message", build("WhatsApp.exe", mic=True), 3, False),
        (
            "discord voice channel",
            build("Discord.exe", mic=True, loopback=True),
            7,
            True,
        ),
        ("zoom open, nobody talking yet", build("Zoom.exe", titles=("Zoom Meeting",)), 5, True),
    ],
)
def test_scoring_table(name: str, evidence: list[Evidence], expected: int, records: bool) -> None:
    total = score(evidence, WEIGHTS)
    assert total == expected, f"{name}: {[e.code for e in evidence]}"
    assert (total >= 5) is records, name


def test_ignore_list_dominates() -> None:
    """An ignored process with otherwise-strong evidence still scores below threshold."""
    evidence = build(
        "VoiceAccess.exe", mic=True, loopback=True, titles=("Zoom Meeting",), camera=True
    )
    total = score(evidence, WEIGHTS)
    assert ev.is_ignored(evidence)
    assert total < 5, total
    assert total == -5 + 2 + 2 + 2 + 1


def test_each_code_counts_once() -> None:
    doubled = [Evidence(2, ev.WINDOW_TITLE, "Zoom Meeting"), Evidence(2, ev.WINDOW_TITLE, "Meet")]
    assert score(doubled, WEIGHTS) == 2


def test_weights_come_from_config() -> None:
    evidence = [Evidence(3, ev.MIC_KNOWN, "Zoom.exe")]
    assert score(evidence, {ev.MIC_KNOWN: 99}) == 99
    assert score(evidence, {}) == 3  # falls back to the evidence's own weight


def test_calendar_weight_exists_but_never_contributes() -> None:
    assert ev.CALENDAR in WEIGHTS
    from pathlib import Path

    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("app").rglob("*.py")
        if path.name not in ("evidence.py",)
    )
    assert "CALENDAR" not in sources, "nothing in V1 may contribute the calendar weight"


def test_process_matching_ignores_paths_and_case() -> None:
    assert ev.matches_process(r"C:\Program Files\Zoom\Zoom.exe", ["zoom.exe"])
    assert ev.matches_process("/usr/bin/Teams.exe", ["Teams.exe"])
    assert not ev.matches_process("Zoomba.exe", ["Zoom.exe"])


def test_title_patterns() -> None:
    assert ev.title_evidence(["Weekly Sync | Microsoft Teams"], PATTERNS)
    assert ev.title_evidence(["Zoom Meeting"], PATTERNS)
    assert not ev.title_evidence(["Inbox — Outlook"], PATTERNS)


def test_explain_renders_the_recorded_because_string() -> None:
    evidence = build("Zoom.exe", mic=True, loopback=True, titles=("Zoom Meeting",))
    text = ev.explain(evidence)
    assert "Zoom.exe" in text and "Zoom Meeting" in text and "speaking" in text
