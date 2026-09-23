from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest

from app.audio.fake import SyntheticCapture
from app.audio.recorder import Recorder
from app.clock import FakeClock
from app.config import default_config
from app.db.dao import Dao, connect
from app.detect.detector import Detector, DetectorState, Outcome
from app.detect.sources import (
    FakeCameraSource,
    FakeMicSource,
    FakeSessionSource,
    FakeTitleSource,
    FakeVadSource,
    Sources,
)
from app.meetings import MeetingService
from app.notify import FakeNotifier
from app.pipeline.queue import JobQueue
from app.pipeline.states import MeetingState


@dataclass
class Harness:
    detector: Detector
    dao: Dao
    clock: FakeClock
    mic: FakeMicSource
    vad: FakeVadSource
    titles: FakeTitleSource
    sessions: FakeSessionSource
    camera: FakeCameraSource
    recorder: Recorder
    notifier: FakeNotifier
    root: Path
    captures: dict[str, SyntheticCapture]

    def seconds(self, count: float, *, audio: bool = True) -> None:
        """Advance the world one second at a time: audio, then a detector tick."""
        for _ in range(int(count)):
            if audio:
                for capture in self.captures.values():
                    for _block in range(10):  # 10 × 0.1 s blocks == 1 s
                        capture.emit()
                self.recorder.drain()
            self.detector.tick()
            self.clock.advance(1)


def build(tmp_path: Path, **overrides: object) -> Harness:
    defaults: dict[str, object] = {
        "audio__capture": "synthetic",
        "audio__vad": "energy",
        "detection__mode": "on",
        "delivery__notifier": "fake",
        "audio__min_meeting_s": 5,
    }
    defaults.update(overrides)
    config = default_config(**defaults)  # type: ignore[arg-type]
    config.set("data_root", str(tmp_path / "meetings"))
    conn = connect(tmp_path / "index.db")
    clock = FakeClock()
    dao = Dao(conn, clock)
    dao.seed_ids(21)
    queue = JobQueue(conn, clock, random.Random(9))
    captures: dict[str, SyntheticCapture] = {}

    def make(track: str) -> SyntheticCapture:
        capture = SyntheticCapture(track, "tone", queue_seconds=200.0, generate_on_read=False)
        captures[track] = capture
        return capture

    recorder = Recorder(config, make, clock=clock)
    sources = Sources(
        mic=FakeMicSource(),
        sessions=FakeSessionSource(),
        titles=FakeTitleSource(),
        camera=FakeCameraSource(),
        vad=FakeVadSource(),
    )
    notifier = FakeNotifier(clock=clock)
    detector = Detector(
        config,
        dao,
        MeetingService(config, dao, queue, clock=clock),
        recorder,
        sources,
        clock=clock,
        notifier=notifier,
    )
    return Harness(
        detector=detector,
        dao=dao,
        clock=clock,
        mic=sources.mic,  # type: ignore[arg-type]
        vad=sources.vad,  # type: ignore[arg-type]
        titles=sources.titles,  # type: ignore[arg-type]
        sessions=sources.sessions,  # type: ignore[arg-type]
        camera=sources.camera,  # type: ignore[arg-type]
        recorder=recorder,
        notifier=notifier,
        root=tmp_path,
        captures=captures,
    )


def strong_evidence(h: Harness) -> None:
    h.mic.hold("Zoom.exe")
    h.titles.window_titles = ["Zoom Meeting"]
    h.vad.set(me=True, them=True)


def data_root_files(h: Harness) -> list[Path]:
    root = h.root / "meetings"
    return [p for p in root.rglob("*") if p.is_file()] if root.exists() else []


# ------------------------------------------------------------------ tier 1


def test_tier1_opens_streams_no_disk(tmp_path: Path) -> None:
    h = build(tmp_path)
    strong_evidence(h)
    h.detector.tick()
    assert h.detector.state is DetectorState.AWAKE
    assert h.recorder.armed is True and h.recorder.committed is False
    h.seconds(5)
    assert len(h.recorder.runtime["me"].ring) > 0, "the pre-roll is filling"
    assert data_root_files(h) == [], "zero files on disk during tier 1"


def test_ignored_process_never_wakes(tmp_path: Path) -> None:
    h = build(tmp_path)
    h.mic.hold("VoiceAccess.exe")
    h.detector.tick()
    assert h.detector.state is DetectorState.IDLE
    assert h.recorder.armed is False
    events = h.dao.detector_events()
    assert events and events[0].outcome == Outcome.IGNORED


# ------------------------------------------------------------------ tier 2


def test_sustain_required(tmp_path: Path) -> None:
    """Evidence has to hold for `detection.sustain_s` before it counts as a meeting.

    Written against the configured window rather than a number, because the window is a
    tuning decision: it was halved to 5 s so a detection reaches the screen while the
    meeting is still starting, and a test that hardcodes 10 turns that into a failure
    instead of a choice.
    """
    h = build(tmp_path)
    window = int(h.detector.sustain_s)
    strong_evidence(h)
    h.seconds(window - 1)
    assert h.detector.state is DetectorState.AWAKE, "a second short must not commit"
    h.vad.set(me=False, them=False)
    h.titles.window_titles = []
    h.seconds(1)
    assert h.detector.state is DetectorState.AWAKE
    assert h.dao.list_meetings() == []

    strong_evidence(h)
    h.seconds(window + 1)
    assert h.detector.state is DetectorState.RECORDING
    assert len(h.dao.list_meetings()) == 1


def test_discard_leaves_nothing(tmp_path: Path) -> None:
    h = build(tmp_path)
    strong_evidence(h)
    h.seconds(5)
    h.mic.release()
    h.vad.set(me=False, them=False)
    h.detector.tick()
    assert h.detector.state is DetectorState.IDLE
    assert h.dao.list_meetings() == []
    assert data_root_files(h) == []
    events = h.dao.detector_events()
    assert events and events[0].outcome == Outcome.NEAR_MISS
    assert h.detector.near_misses == 1


def test_90s_timeout_gives_up(tmp_path: Path) -> None:
    """A held microphone with no other evidence returns to idle at 90 s."""
    h = build(tmp_path)
    h.mic.hold("SomeApp.exe")  # unknown app: score 1, below threshold
    h.seconds(89)
    assert h.detector.state is DetectorState.AWAKE
    h.seconds(2)
    assert h.detector.state is DetectorState.IDLE
    assert h.dao.list_meetings() == []
    assert data_root_files(h) == []


def test_commit_flushes_preroll(tmp_path: Path) -> None:
    """Commit after 18 s: chunk 0001 is the pre-roll and starts at t0_ms == 0."""
    h = build(tmp_path, detection__sustain_s=18)
    strong_evidence(h)
    h.seconds(19)
    assert h.detector.state is DetectorState.RECORDING
    from app.audio.writer import read_manifest

    meeting = h.dao.list_meetings()[0]
    records, torn = read_manifest(meeting.path)
    assert torn == 0
    first = next(r for r in records if r.track == "me" and r.seq == 1)
    assert first.t0_ms == 0
    assert 17_000 <= first.dur_ms <= 20_000, first.dur_ms


# ------------------------------------------------------------------ tier 3


def test_end_on_mic_release_with_grace(tmp_path: Path) -> None:
    """Re-acquire at 45 s → one meeting; at 75 s → two."""
    h = build(tmp_path)
    strong_evidence(h)
    h.seconds(11)
    assert h.detector.state is DetectorState.RECORDING
    first_id = h.detector.meeting_id

    h.mic.release()
    h.seconds(45)
    assert h.detector.state is DetectorState.GRACE
    h.mic.hold("Zoom.exe")
    h.seconds(2)
    assert h.detector.state is DetectorState.RECORDING
    assert h.detector.meeting_id == first_id
    assert len(h.dao.list_meetings()) == 1

    h.mic.release()
    h.seconds(75)
    assert h.detector.state is DetectorState.IDLE
    strong_evidence(h)
    h.seconds(11)
    assert h.detector.state is DetectorState.RECORDING
    assert h.detector.meeting_id != first_id
    assert len(h.dao.list_meetings()) == 2


def test_end_on_dual_silence(tmp_path: Path) -> None:
    h = build(tmp_path, detection__dual_silence_s=60)
    strong_evidence(h)
    h.seconds(11)
    meeting_id = h.detector.meeting_id
    assert meeting_id is not None
    h.vad.set(me=False, them=False)  # the mic is still held, nobody is talking
    h.seconds(61)
    assert h.detector.state is DetectorState.IDLE
    meeting = h.dao.require_meeting(meeting_id)
    assert meeting.state in (MeetingState.RECORDED, MeetingState.DISCARDED)
    assert meeting.duration_s and meeting.duration_s > 0


def test_max_duration_ends_the_meeting(tmp_path: Path) -> None:
    """The cap ends the meeting and a new one starts if activity continues (§7.4)."""
    h = build(tmp_path)
    h.detector.config.set("audio.max_meeting_h", 30 / 3600)  # 30 seconds
    strong_evidence(h)
    h.seconds(11)
    assert h.detector.state is DetectorState.RECORDING
    first_id = h.detector.meeting_id
    h.seconds(31)
    assert h.dao.require_meeting(str(first_id)).state in (
        MeetingState.RECORDED,
        MeetingState.DISCARDED,
    )
    h.seconds(11)
    assert h.detector.state is DetectorState.RECORDING
    assert h.detector.meeting_id != first_id
    assert len(h.dao.list_meetings()) == 2


def test_evidence_recorded_on_meeting(tmp_path: Path) -> None:
    h = build(tmp_path)
    strong_evidence(h)
    h.camera.on = True
    h.sessions.processes = ["Zoom.exe"]
    h.seconds(11)
    meeting = h.dao.list_meetings()[0]
    codes = {item["code"] for item in meeting.evidence}
    assert codes == {
        "mic.known_app",
        "vad.mic",
        "vad.loopback",
        "window.title",
        "session.render",
        "camera",
    }
    from app.detect.evidence import Evidence, explain

    rendered = explain([Evidence(**item) for item in meeting.evidence])
    assert "Zoom.exe" in rendered and "Zoom Meeting" in rendered
    events = h.dao.detector_events()
    assert events[0].outcome == Outcome.COMMITTED
    assert events[0].meeting_id == meeting.id
    assert events[0].peak_score >= 9
    assert h.notifier.titles() and "Recording" in h.notifier.titles()[0]


# ------------------------------------------------------------------ shadow


def test_shadow_mode_commits_nothing(tmp_path: Path) -> None:
    h = build(tmp_path, detection__mode="shadow")
    strong_evidence(h)
    h.seconds(12)
    assert h.dao.list_meetings() == []
    assert data_root_files(h) == []
    events = h.dao.detector_events()
    assert events and events[0].outcome == Outcome.SHADOW
    assert events[0].peak_score >= 9
    assert h.detector.state is DetectorState.IDLE
    assert h.recorder.armed is False
    # and it does not re-open the streams every few seconds while the mic stays held
    h.seconds(30)
    assert h.detector.state is DetectorState.IDLE
    assert len(h.dao.detector_events()) == 1
    assert data_root_files(h) == []


def test_off_mode_does_nothing(tmp_path: Path) -> None:
    h = build(tmp_path, detection__mode="off")
    strong_evidence(h)
    h.seconds(20)
    assert h.detector.state is DetectorState.IDLE
    assert h.dao.detector_events() == []


def test_mute_stops_waking(tmp_path: Path) -> None:
    h = build(tmp_path)
    h.detector.mute_for(3600)
    strong_evidence(h)
    h.seconds(20)
    assert h.detector.state is DetectorState.IDLE
    assert h.recorder.armed is False
    h.detector.muted_until = None
    h.seconds(11)
    assert h.detector.state is DetectorState.RECORDING


@pytest.mark.parametrize("mode", ["shadow", "on"])
def test_near_miss_below_watermark_is_not_logged(tmp_path: Path, mode: str) -> None:
    h = build(tmp_path / mode, detection__mode=mode)
    h.mic.hold("SomeApp.exe")  # score 1, below the watermark of 3
    h.seconds(91)
    assert h.detector.state is DetectorState.IDLE
    assert h.dao.detector_events() == []


def test_switching_the_mode_takes_effect_without_a_restart(tmp_path: Path) -> None:
    """Settings writes the mode into the live config, so the loop has to read it every
    tick. A detector that only learned its mode at startup would make the switch a lie."""
    h = build(tmp_path, detection__mode="off")
    strong_evidence(h)
    h.seconds(20)
    assert h.dao.detector_events() == [], "off means off"

    h.detector.config.set("detection.mode", "shadow")
    h.seconds(20)
    events = h.dao.detector_events()
    assert events and events[0].outcome == Outcome.SHADOW
    assert h.dao.list_meetings() == [], "watching still records nothing"

    # A wake that has been decided is not reconsidered while the microphone stays held,
    # so the next meeting starts the way a real one does: the app lets go, and takes it
    # again.
    h.mic.release()
    h.seconds(5)
    h.detector.config.set("detection.mode", "on")
    strong_evidence(h)
    h.seconds(20)
    assert h.dao.list_meetings(), "and now it records"


def test_a_manual_recording_survives_the_detector_waking_on_it(tmp_path: Path) -> None:
    """The likeliest thing to happen in shadow mode: the call that woke the detector is
    the call the user then presses Start on. Every way out of a wake discards the
    recorder, so detecting through that recording would have closed its streams."""
    h = build(tmp_path, detection__mode="shadow")
    strong_evidence(h)
    h.seconds(2)
    assert h.detector.state is DetectorState.AWAKE

    folder = h.root / "meetings" / "manual"
    h.recorder.start(folder, "manual")
    h.seconds(30)

    assert h.detector.state is DetectorState.IDLE, "the wake was dropped, not decided"
    assert h.recorder.committed, "the recording is still running"
    assert h.dao.detector_events() == [], "nothing was scored through someone else's recording"
    result = h.recorder.stop()
    assert result.total_duration_ms > 20_000, "the audio kept being written throughout"


def test_waking_takes_the_endpoints_back_from_the_settings_meters(tmp_path: Path) -> None:
    """One capture stream per endpoint: a Settings meter left open would make every wake
    fail to arm, silently, for as long as the microphone was held."""
    from app.audio import monitor as meter

    h = build(tmp_path, detection__mode="shadow")
    meter.acquire(h.detector.config, None, "me")
    assert meter.active("me") is not None
    try:
        strong_evidence(h)
        h.seconds(2)
        assert h.detector.state is DetectorState.AWAKE, "the wake armed the recorder"
        assert meter.active("me") is None, "the preview stream was not released"
    finally:
        meter.release()


# ------------------------------------------- furniture vs. an app joining a call

VOICEMEETER = r"C:\Program Files (x86)\VB\Voicemeeter\voicemeeter.exe"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def held_for(h: Harness, seconds: float) -> int:
    """`LastUsedTimeStart` as Windows reports it: a FILETIME, in ms since 1601."""
    from app.detect.detector import FILETIME_EPOCH

    taken = h.clock.now() - timedelta(seconds=seconds)
    return int((taken - FILETIME_EPOCH).total_seconds() * 1000)


def test_furniture_holding_the_microphone_is_not_a_meeting(tmp_path: Path) -> None:
    """Voicemeeter routes all audio on the author's machine and holds the microphone from
    boot to shutdown — 234 minutes when this was measured. Windows lists it first, so the
    detector woke on it, scored it, gave up, and was then deaf to the Meet call that
    started minutes later. Nothing here names it: it is old, and meetings are new."""
    from app.detect.registry import MicHolder

    h = build(tmp_path, detection__mode="shadow")
    h.mic.holders = [MicHolder(process=VOICEMEETER, since_ms=held_for(h, 4 * 3600))]
    h.titles.window_titles = ["clickup"]
    h.vad.set(me=True, them=True)
    h.seconds(100)
    assert h.detector.state is DetectorState.IDLE
    assert h.dao.detector_events() == [], "scenery is not an event"

    # The call starts. The browser takes the microphone; Voicemeeter never let go.
    h.mic.holders = [
        MicHolder(process=VOICEMEETER, since_ms=held_for(h, 4 * 3600)),
        MicHolder(process=CHROME, since_ms=held_for(h, 1)),
    ]
    h.titles.window_titles = ["Meet – rbn-nmqs-jbm - Google Chrome"]
    h.seconds(15)

    scored = [event for event in h.dao.detector_events() if event.outcome == Outcome.SHADOW]
    assert scored, "the browser joining the call was never scored"
    assert "chrome.exe" in scored[0].process
    assert scored[0].peak_score >= h.detector.threshold


def test_a_hold_that_began_just_before_startup_still_counts(tmp_path: Path) -> None:
    """The first look has no previous one to compare against, so it asks Windows how old
    each hold is. A call joined seconds before the app started is still a meeting."""
    from app.detect.registry import MicHolder

    h = build(tmp_path, detection__mode="shadow")
    h.mic.holders = [MicHolder(process=CHROME, since_ms=held_for(h, 5))]
    h.titles.window_titles = ["Meet – rbn-nmqs-jbm - Google Chrome"]
    h.vad.set(me=True, them=True)
    h.seconds(15)
    assert [e.outcome for e in h.dao.detector_events()] == [Outcome.SHADOW]


def test_an_unreadable_timestamp_is_treated_as_recent(tmp_path: Path) -> None:
    """The ConsentStore is an undocumented artifact. A machine that does not report when
    a hold began must still detect meetings — failing closed would mean detecting nothing
    at all, and saying nothing about it."""
    from app.detect.registry import MicHolder

    h = build(tmp_path, detection__mode="shadow")
    h.mic.holders = [MicHolder(process=CHROME, since_ms=0)]
    h.titles.window_titles = ["Meet – rbn-nmqs-jbm - Google Chrome"]
    h.vad.set(me=True, them=True)
    h.seconds(15)
    assert [e.outcome for e in h.dao.detector_events()] == [Outcome.SHADOW]


def test_taking_the_microphone_again_is_news(tmp_path: Path) -> None:
    """A verdict is about one acquisition. Let go and take it again — a second call on
    the same machine — and the detector has to look afresh."""
    from app.detect.registry import MicHolder

    h = build(tmp_path, detection__mode="shadow")
    h.titles.window_titles = ["Meet – rbn-nmqs-jbm - Google Chrome"]
    h.vad.set(me=True, them=True)
    h.mic.holders = [MicHolder(process=CHROME, since_ms=held_for(h, 1))]
    h.seconds(15)
    assert len(h.dao.detector_events()) == 1

    h.seconds(20)
    assert len(h.dao.detector_events()) == 1, "one acquisition, one verdict"

    h.mic.holders = []  # the call ends
    h.seconds(2)
    h.mic.holders = [MicHolder(process=CHROME, since_ms=held_for(h, 1))]  # and another starts
    h.seconds(15)
    assert len(h.dao.detector_events()) == 2


def test_a_recording_ends_when_its_own_app_lets_go(tmp_path: Path) -> None:
    """The grace window asks whether *the meeting's* app released the microphone. Any
    holder would do before, so a permanent one kept every detected meeting running."""
    from app.detect.registry import MicHolder

    h = build(tmp_path, detection__mode="on")
    h.mic.holders = [
        MicHolder(process=VOICEMEETER, since_ms=held_for(h, 4 * 3600)),
        MicHolder(process=CHROME, since_ms=held_for(h, 1)),
    ]
    h.titles.window_titles = ["Meet – rbn-nmqs-jbm - Google Chrome"]
    h.vad.set(me=True, them=True)
    h.seconds(15)
    assert h.detector.state is DetectorState.RECORDING

    # The call ends: the browser lets go, Voicemeeter does not.
    h.mic.holders = [MicHolder(process=VOICEMEETER, since_ms=held_for(h, 4 * 3600))]
    h.seconds(h.detector.grace_s + 5)
    assert h.detector.meeting_id is None, "the meeting was never ended"
    assert h.detector.state not in (DetectorState.RECORDING, DetectorState.GRACE)
    assert h.dao.list_meetings()[0].state == MeetingState.RECORDED


def test_every_verdict_reaches_the_log_file(tmp_path: Path, caplog) -> None:  # type: ignore[no-untyped-def]
    """The detector's audit trail must not live only in a table nobody reads.

    `detector_events` had exactly one consumer — the Detector screen — and the
    screen was removed on 2026-09-22. `_log_event` despite its name only ever
    wrote the row, so "why did it record that?" would have had no answer short of
    opening the database by hand. Each verdict now also goes to the log at INFO,
    carrying the score and which signals fired.
    """
    h = build(tmp_path, detection__mode="shadow")
    strong_evidence(h)
    with caplog.at_level("INFO", logger="app.detect.detector"):
        h.seconds(12)

    events = h.dao.detector_events()
    assert events, "the row is still written"

    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("detector ")]
    assert lines, f"no verdict was logged; saw {[r.getMessage() for r in caplog.records]}"
    verdict = lines[-1]
    assert str(Outcome.SHADOW) in verdict
    assert str(events[0].peak_score) in verdict
    # The evidence is summarised on the line, not just stored in the row.
    assert any(item["code"] in verdict for item in events[0].evidence_list)
