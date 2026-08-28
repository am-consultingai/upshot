"""The evidence model (DETECTION.md §5). Pure — no Windows imports, ever.

That is what makes the ruleset testable as a table, against the worked examples in the
design rather than against a mock of the operating system.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# Codes are the vocabulary of the scoring table; the weights live in config.
MIC_KNOWN = "mic.known_app"
MIC_UNKNOWN = "mic.unknown_app"
VAD_LOOPBACK = "vad.loopback"
VAD_MIC = "vad.mic"
WINDOW_TITLE = "window.title"
SESSION_RENDER = "session.render"
CAMERA = "camera"
CALENDAR = "calendar"  # post-V1: the weight exists, nothing ever contributes it
IGNORED = "ignored"


@dataclass(frozen=True)
class Evidence:
    weight: int
    code: str  # 'mic.known_app'
    detail: str  # 'Zoom.exe'

    def as_dict(self) -> dict[str, Any]:
        return {"weight": self.weight, "code": self.code, "detail": self.detail}


def score(evidence: Sequence[Evidence], weights: Mapping[str, int]) -> int:
    """Sum the configured weight of each *distinct* code.

    Distinct: two window titles matching is not twice the evidence that one is.
    """
    seen: dict[str, int] = {}
    for item in evidence:
        if item.code in seen:
            continue
        seen[item.code] = int(weights.get(item.code, item.weight))
    return sum(seen.values())


def explain(evidence: Sequence[Evidence]) -> str:
    """The "Recorded because: …" string the UI shows."""
    return ", ".join(f"{item.detail}" for item in evidence if item.detail)


def is_ignored(evidence: Sequence[Evidence]) -> bool:
    return any(item.code == IGNORED for item in evidence)


def matches_process(process: str, candidates: Iterable[str]) -> bool:
    name = process.rsplit("\\", 1)[-1].rsplit("/", 1)[-1].lower()
    return any(name == str(candidate).lower() for candidate in candidates)


def title_matches(title: str, patterns: Iterable[str]) -> bool:
    lowered = title.lower()
    return any(str(pattern).lower() in lowered for pattern in patterns)


# --------------------------------------------------------------------------- builders


def mic_evidence(
    process: str, *, known_apps: Sequence[str], ignore: Sequence[str]
) -> list[Evidence]:
    if not process:
        return []
    if matches_process(process, ignore):
        return [Evidence(-5, IGNORED, process)]
    if matches_process(process, known_apps):
        return [Evidence(3, MIC_KNOWN, process)]
    return [Evidence(1, MIC_UNKNOWN, process)]


def speech_evidence(*, mic: bool, loopback: bool) -> list[Evidence]:
    out: list[Evidence] = []
    if loopback:
        out.append(Evidence(2, VAD_LOOPBACK, "someone else is speaking"))
    if mic:
        out.append(Evidence(2, VAD_MIC, "you are speaking"))
    return out


def title_evidence(titles: Sequence[str], patterns: Sequence[str]) -> list[Evidence]:
    for title in titles:
        if title and title_matches(title, patterns):
            return [Evidence(2, WINDOW_TITLE, title)]
    return []


def session_evidence(process: str, render_processes: Sequence[str]) -> list[Evidence]:
    if process and matches_process(process, render_processes):
        return [Evidence(1, SESSION_RENDER, f"{process} is playing audio")]
    return []


def camera_evidence(in_use: bool, detail: str = "the camera is on") -> list[Evidence]:
    return [Evidence(1, CAMERA, detail)] if in_use else []
