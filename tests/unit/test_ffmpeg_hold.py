"""ffmpeg that keeps failing to start: Upshot stops starting it for a while and says so
once (z8tj1hwjvv)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.audio import ingest
from app.audio.ingest import FFMPEG_HOLD_AFTER, FFMPEG_HOLD_S, FfmpegHealth, FfmpegHold
from app.notify import FakeNotifier


class Ran:
    def __init__(self, returncode: int, stderr: bytes) -> None:
        self.returncode, self.stderr, self.stdout = returncode, stderr, b""


NO_MEMORY = Ran(0xC0000017, b"")
REFUSED = Ran(1, b"ffmpeg version 7\nclip.m4a: Invalid data found when processing input")
READ = Ran(1, b"ffmpeg version 7\nInput #0, mov, from 'clip.m4a':\n  Duration: 00:01:30.00,\n"
              b"  Stream #0:0: Audio: aac\nAt least one output file must be specified")  # fmt: skip


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def health(monkeypatch: pytest.MonkeyPatch) -> tuple[FfmpegHealth, Clock, list[FfmpegHold]]:
    clock = Clock()
    record = FfmpegHealth(clock=clock)
    holds: list[FfmpegHold] = []
    record.on_hold = holds.append
    monkeypatch.setattr(ingest, "HEALTH", record)
    monkeypatch.setattr(ingest, "ffmpeg_path", lambda config=None: "ffmpeg")
    monkeypatch.setattr(ingest, "commit_memory", lambda: (512 * 2**20, 64 * 2**30))
    return record, clock, holds


def answers(monkeypatch: pytest.MonkeyPatch, *results: Ran) -> list[list[str]]:
    """ffmpeg's runs, in order; the commands it was started with."""
    queue = list(results)
    started: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> Ran:
        started.append(argv)
        return queue.pop(0)

    monkeypatch.setattr(ingest.subprocess, "run", run)
    return started


def test_three_failures_in_a_row_stop_ffmpeg_being_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, health: Any
) -> None:
    record, _clock, holds = health
    started = answers(monkeypatch, *[NO_MEMORY] * FFMPEG_HOLD_AFTER)
    for _ in range(FFMPEG_HOLD_AFTER):
        with pytest.raises(ingest.FfmpegFailed):
            ingest.probe(tmp_path / "clip.m4a")
    assert record.held()
    with pytest.raises(ingest.FfmpegFailed, match="failed 3 times in a row"):
        ingest.probe(tmp_path / "next.m4a")
    with pytest.raises(ingest.FfmpegFailed, match="waits 60 s"):
        ingest.to_wav(tmp_path / "next.m4a", tmp_path / "out.wav")
    assert len(started) == FFMPEG_HOLD_AFTER, "no ffmpeg started while held"
    assert [(hold.failures, hold.memory) for hold in holds] == [(3, (512 * 2**20, 64 * 2**30))]


def test_two_failures_are_not_a_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, health: Any
) -> None:
    record, _clock, holds = health
    answers(monkeypatch, NO_MEMORY, NO_MEMORY, READ)
    for _ in range(2):
        with pytest.raises(ingest.FfmpegFailed):
            ingest.probe(tmp_path / "clip.m4a")
    assert not record.held() and holds == []
    assert ingest.probe(tmp_path / "clip.m4a").has_audio
    assert record.failures == 0, "a run that worked starts the count again"


def test_after_the_hold_one_ffmpeg_is_tried_and_a_failure_holds_again_quietly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, health: Any
) -> None:
    record, clock, holds = health
    started = answers(monkeypatch, *[NO_MEMORY] * (FFMPEG_HOLD_AFTER + 1))
    for _ in range(FFMPEG_HOLD_AFTER):
        with pytest.raises(ingest.FfmpegFailed):
            ingest.probe(tmp_path / "clip.m4a")
    clock.now += FFMPEG_HOLD_S
    with pytest.raises(ingest.FfmpegFailed):
        ingest.probe(tmp_path / "clip.m4a")
    assert len(started) == FFMPEG_HOLD_AFTER + 1, "tried once when the hold ended"
    assert record.held()
    assert len(holds) == 1, "said once, not at every hold"


def test_a_run_that_works_ends_the_hold_and_a_new_streak_is_said_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, health: Any
) -> None:
    record, clock, holds = health
    answers(monkeypatch, *[NO_MEMORY] * 3, READ, *[NO_MEMORY] * 3)
    for _ in range(3):
        with pytest.raises(ingest.FfmpegFailed):
            ingest.probe(tmp_path / "clip.m4a")
    clock.now += FFMPEG_HOLD_S
    ingest.probe(tmp_path / "clip.m4a")
    assert not record.held() and record.state()["ok"] is True
    for _ in range(3):
        with pytest.raises(ingest.FfmpegFailed):
            ingest.probe(tmp_path / "clip.m4a")
    assert len(holds) == 2


def test_a_file_ffmpeg_refuses_is_ffmpeg_working(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, health: Any
) -> None:
    record, _clock, _holds = health
    answers(monkeypatch, NO_MEMORY, NO_MEMORY, REFUSED, NO_MEMORY, NO_MEMORY)
    for result in ("failed", "failed", "refused", "failed", "failed"):
        error = ingest.UnsupportedAudio if result == "refused" else ingest.FfmpegFailed
        with pytest.raises(error):
            ingest.probe(tmp_path / "clip.m4a")
    assert not record.held() and record.failures == 2


def test_an_ffmpeg_that_can_never_run_is_not_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, health: Any
) -> None:
    """Broken is said by each job's own error, which tells what to do."""
    record, _clock, holds = health
    answers(monkeypatch, *[Ran(0xC0000135, b"")] * 4)
    for _ in range(4):
        with pytest.raises(ingest.FfmpegBroken):
            ingest.probe(tmp_path / "clip.m4a")
    assert record.failures == 0 and holds == []


def test_the_state_for_info(health: Any) -> None:
    record, _clock, _holds = health
    assert record.state() == {"ok": True, "held": False, "failures_in_a_row": 0, "error": None}
    for _ in range(3):
        record.failed("exit code 0xc0000017")
    assert record.state() == {
        "ok": False,
        "held": True,
        "failures_in_a_row": 3,
        "error": "exit code 0xc0000017",
    }


def test_a_notice_that_fails_is_not_the_callers_error(health: Any) -> None:
    record, _clock, _holds = health

    def broken(hold: FfmpegHold) -> None:
        raise RuntimeError("no toast")

    record.on_hold = broken
    for _ in range(3):
        record.failed("exit code 0xc0000017")
    assert record.held()


# -- the notice --------------------------------------------------------------


def test_the_notice_names_low_memory() -> None:
    notifier = FakeNotifier()
    notifier.ffmpeg_failing(3, (int(0.4 * 2**30), 64 * 2**30))
    toast = notifier.shown[-1]
    assert toast.title == "Upshot can't read audio files right now"
    assert "3 times in a row" in toast.body
    assert "0.4 GB of 64 GB left" in toast.body


def test_the_notice_leaves_out_memory_that_is_not_low() -> None:
    notifier = FakeNotifier()
    notifier.ffmpeg_failing(3, (30 * 2**30, 64 * 2**30))
    assert "GB" not in notifier.shown[-1].body
    notifier = FakeNotifier()
    notifier.ffmpeg_failing(3, None)
    assert "GB" not in notifier.shown[-1].body


def test_the_notice_is_in_every_language() -> None:
    from app.i18n import tr

    for language in ("en", "he", "de", "es", "fr"):
        body = tr("toast.ffmpegFailing.body", language, count=3)
        memory = tr("toast.ffmpegFailing.memory", language, free="0.4", limit="64")
        assert "3" in body and "0.4" in memory and "64" in memory


def test_the_app_wires_the_notice(tmp_path: Path) -> None:
    from tests.fixtures.api import build_harness

    harness = build_harness(tmp_path)
    notifier = harness.services.notifier
    for _ in range(3):
        ingest.HEALTH.failed("exit code 0xc0000017")
    assert any(toast.key == "ffmpeg_failing" for toast in notifier.shown)


def test_commit_memory_is_none_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ingest.sys, "platform", "linux")
    assert ingest.commit_memory() is None
