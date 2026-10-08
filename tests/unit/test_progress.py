"""Percent done and time left: the shared ETA, the meeting's phases, the tracker."""

from __future__ import annotations

from datetime import timedelta
from itertools import pairwise

import pytest

from app.clock import FakeClock, iso
from app.events import EventBus
from app.pipeline.progress import (
    MEETING_PHASES,
    MEETING_WEIGHTS,
    MIN_ETA_ELAPSED_S,
    MIN_ETA_PROGRESS,
    MeetingProgress,
    eta_seconds,
    eta_since,
    meeting_progress,
)
from app.tray_state import AppState, icon_for

# ------------------------------------------------------------------ the ETA


def test_time_left_is_this_runs_pace() -> None:
    # A quarter done in a minute: three quarters left at the same pace is three minutes.
    assert eta_seconds(0.25, 60.0) == 180
    assert eta_seconds(0.5, 600.0) == 600
    assert eta_seconds(0.99, 990.0) == 10


def test_no_time_left_until_it_can_be_measured() -> None:
    assert eta_seconds(None, 600.0) is None
    assert eta_seconds(MIN_ETA_PROGRESS - 0.001, 600.0) is None, "too little done"
    assert eta_seconds(0.5, MIN_ETA_ELAPSED_S - 1) is None, "too soon"
    assert eta_seconds(MIN_ETA_PROGRESS, MIN_ETA_ELAPSED_S) is not None
    assert eta_seconds(1.0, 600.0) is None, "finished: nothing left to estimate"


def test_time_left_since_a_stamp() -> None:
    clock = FakeClock()
    started = iso(clock.now() - timedelta(seconds=120))
    assert eta_since(0.4, started, clock.now()) == 180
    assert eta_since(0.4, None, clock.now()) is None


# ------------------------------------------------------------------ the phases


def test_the_meeting_phases_add_up_to_the_whole_stage() -> None:
    assert sum(MEETING_WEIGHTS.values()) == pytest.approx(1.0)
    assert set(MEETING_WEIGHTS) == set(MEETING_PHASES)
    assert meeting_progress(MEETING_PHASES[0], 0.0) == 0.0
    assert meeting_progress(MEETING_PHASES[-1], 1.0) == 1.0


def test_each_phase_starts_where_the_one_before_it_ends() -> None:
    for before, after in pairwise(MEETING_PHASES):
        assert meeting_progress(before, 1.0) == pytest.approx(meeting_progress(after, 0.0))
    assert meeting_progress("transcribe", 2.0) == meeting_progress("transcribe", 1.0)
    with pytest.raises(ValueError):
        meeting_progress("summarize", 0.5)


# ------------------------------------------------------------------ the tracker


def progress_events(events: EventBus) -> list[dict[str, object]]:
    return [
        e.payload
        for e in events.history
        if e.type == "job" and e.payload.get("action") == "progress"
    ]


def test_the_tracker_never_goes_backwards_and_forgets_a_finished_run() -> None:
    clock = FakeClock()
    tracker = MeetingProgress(clock, EventBus())
    tracker.report("m1", "language", 0.5)
    assert tracker.get("m1") is None, "nothing is reported for a stage not started"

    tracker.start("m1", "transcribe")
    tracker.report("m1", "transcribe", 0.5)
    high = tracker.get("m1")
    assert high is not None and high["phase"] == "transcribe"
    tracker.report("m1", "transcribe", 0.2)  # a late, lower figure
    after = tracker.get("m1")
    assert after is not None and after["progress"] == high["progress"]

    tracker.finish("m1")
    assert tracker.get("m1") is None


def test_the_tracker_says_time_left_once_measured() -> None:
    clock = FakeClock()
    tracker = MeetingProgress(clock, EventBus())
    tracker.start("m1", "transcribe")
    tracker.report("m1", "transcribe", 0.4)
    state = tracker.get("m1")
    assert state is not None and state["eta_s"] is None, "too soon"
    clock.advance(120)
    state = tracker.get("m1")
    assert state is not None
    progress = float(state["progress"])
    assert state["eta_s"] == round(120 * (1 - progress) / progress)


def test_events_are_at_most_one_a_second_but_a_new_phase_is_said_at_once() -> None:
    clock = FakeClock()
    events = EventBus()
    tracker = MeetingProgress(clock, events)
    tracker.start("m1", "transcribe")
    tracker.report("m1", "transcribe", 0.1)
    tracker.report("m1", "transcribe", 0.2)  # the same second: held
    tracker.report("m1", "diarize", 0.0)  # a new phase: said
    clock.advance(1.0)
    tracker.report("m1", "diarize", 0.5)
    sent = progress_events(events)
    assert [e["phase"] for e in sent] == ["transcribe", "diarize", "diarize"]
    assert all(e["meeting_id"] == "m1" and e["state"] == "running" for e in sent)
    assert [e["progress"] for e in sent] == sorted(e["progress"] for e in sent)  # type: ignore[type-var]


# ------------------------------------------------------------------ the tray


@pytest.mark.parametrize(
    ("state", "tooltip"),
    [
        (AppState(processing=True, queue_depth=1, transcribing=0.42), "Transcribing 42 %"),
        (
            AppState(processing=True, queue_depth=3, transcribing=0.05),
            "Transcribing 5 %, 2 more waiting",
        ),
        # A summary has no percentage: the count, as before.
        (AppState(processing=True, queue_depth=2, transcribing=None), "Processing 2 jobs"),
    ],
)
def test_the_tray_says_how_far_the_transcription_has_got(state: AppState, tooltip: str) -> None:
    assert icon_for(state).tooltip == f"Upshot — {tooltip}"
