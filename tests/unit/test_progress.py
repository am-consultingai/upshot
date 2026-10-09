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
    TimeLeft,
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


# ------------------------------------------------------------------ held while stuck (D94)


def readings(
    estimate: TimeLeft, clock: FakeClock, started: str, progress: float, seconds: int, every: int
) -> list[int | None]:
    """Poll ``estimate`` every ``every`` seconds for ``seconds``, progress standing still."""
    out = []
    for _ in range(seconds // every):
        clock.advance(every)
        out.append(estimate.at(progress, started, clock.now()))
    return out


def test_time_left_holds_while_a_long_chunk_is_decoded() -> None:
    # As seen in testing: 10% reached, then a long chunk with no callback for 50 s,
    # polled every 2 s. The plain average climbed 195, 213, 250 ... 666; it must not.
    clock = FakeClock()
    started = iso(clock.now())
    estimate = TimeLeft()
    clock.advance(21.7)
    first = estimate.at(0.10, started, clock.now())
    assert first == 195

    stuck = readings(estimate, clock, started, 0.10, seconds=50, every=2)
    assert stuck == [195] * 25, "the estimate holds while progress stands still"
    assert eta_since(0.10, started, clock.now()) == 645, "what the bare average said"

    # The chunk lands: 38% at 71.7 s. The new figure is the whole run's average pace.
    resumed = estimate.at(0.38, started, clock.now())
    assert resumed == round(71.7 * 0.62 / 0.38) == 117


def test_a_wait_longer_than_the_estimate_grows_it_no_faster_than_real_time() -> None:
    clock = FakeClock()
    started = iso(clock.now())
    estimate = TimeLeft()
    clock.advance(20)
    assert estimate.at(0.5, started, clock.now()) == 20

    stuck = readings(estimate, clock, started, 0.5, seconds=40, every=1)
    assert stuck[:20] == [20] * 20, "held until the wait outlasts it"
    steps = [after - before for before, after in pairwise([20, *stuck])]  # type: ignore[operator]
    assert all(0 <= step <= 1 for step in steps), "never a jump, never faster than the clock"
    assert stuck[-1] == 40


def test_a_held_estimate_never_exceeds_the_plain_average() -> None:
    # Far along, the average itself grows slower than real time: it caps the hold.
    clock = FakeClock()
    started = iso(clock.now())
    estimate = TimeLeft()
    clock.advance(90)
    assert estimate.at(0.9, started, clock.now()) == 10
    for reading in readings(estimate, clock, started, 0.9, seconds=60, every=5):
        assert reading is not None
        assert reading <= eta_since(0.9, started, clock.now())  # type: ignore[operator]


def test_a_held_estimate_still_waits_for_enough_of_the_run() -> None:
    clock = FakeClock()
    started = iso(clock.now())
    estimate = TimeLeft()
    clock.advance(5)
    assert estimate.at(0.5, started, clock.now()) is None, "too soon"
    clock.advance(5)
    assert estimate.at(MIN_ETA_PROGRESS - 0.01, started, clock.now()) is None, "too little"
    clock.advance(MIN_ETA_ELAPSED_S)
    assert estimate.at(MIN_ETA_PROGRESS - 0.01, started, clock.now()) is None, "still too little"
    assert estimate.at(1.0, started, clock.now()) is None, "finished"


def test_the_language_phase_does_not_climb_either() -> None:
    # 5% with 297 -> 335 -> 374 in 4 s, as seen: now held at the first figure.
    clock = FakeClock()
    started = iso(clock.now())
    estimate = TimeLeft()
    clock.advance(15.6)
    first = estimate.at(0.05, started, clock.now())
    assert first == 296
    assert readings(estimate, clock, started, 0.05, seconds=4, every=2) == [296, 296]


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


def test_the_tracker_holds_time_left_while_progress_stands_still() -> None:
    clock = FakeClock()
    tracker = MeetingProgress(clock, EventBus())
    tracker.start("m1", "transcribe")
    tracker.report("m1", "transcribe", 0.2)
    clock.advance(60)
    first = tracker.get("m1")
    assert first is not None and first["eta_s"] is not None
    held = first["eta_s"]
    for _ in range(10):
        clock.advance(2)
        state = tracker.get("m1")
        assert state is not None and state["eta_s"] == held
    tracker.report("m1", "transcribe", 0.8)
    state = tracker.get("m1")
    assert state is not None and state["eta_s"] is not None and state["eta_s"] < held


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
