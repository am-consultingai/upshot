from __future__ import annotations

import random
from dataclasses import dataclass
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
    """Nine seconds above the threshold is not a meeting; ten is."""
    h = build(tmp_path)
    strong_evidence(h)
    h.seconds(9)
    assert h.detector.state is DetectorState.AWAKE, "9 s must not commit"
    h.vad.set(me=False, them=False)
    h.titles.window_titles = []
    h.seconds(1)
    assert h.detector.state is DetectorState.AWAKE
    assert h.dao.list_meetings() == []

    strong_evidence(h)
    h.seconds(11)
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
