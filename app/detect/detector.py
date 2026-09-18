"""The four-tier state machine (DETECTION.md §3).

Tier 0 idle → Tier 1 wake (streams open, pre-roll filling, **nothing on disk**) →
Tier 2 confirm (score sustained) → Tier 3 committed. Everything it reacts to arrives
through injected sources and an injected clock, so the whole machine is testable with no
Windows APIs and no real time.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from app.audio.recorder import Recorder
from app.clock import Clock, SystemClock
from app.config import Config
from app.db.dao import Dao
from app.detect import evidence as ev
from app.detect.evidence import Evidence
from app.detect.sources import MicHolder, Sources
from app.log import get
from app.meetings import MeetingService
from app.pipeline.states import MeetingState

log = get(__name__)

#: ``LastUsedTimeStart`` counts from 1601, as every Windows FILETIME does.
FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=UTC)


class DetectorState(StrEnum):
    IDLE = "idle"
    AWAKE = "awake"  # tier 1 + 2
    RECORDING = "recording"  # tier 3
    GRACE = "grace"  # mic released, waiting to see if it comes back


class Outcome(StrEnum):
    COMMITTED = "committed"
    NEAR_MISS = "near_miss"
    IGNORED = "ignored"
    SHADOW = "shadow"


@dataclass
class Wake:
    process: str
    started_mono: float
    peak_score: int = 0
    sustained_since: float | None = None
    evidence: list[Evidence] = field(default_factory=list)
    title: str = ""


class Detector:
    def __init__(
        self,
        config: Config,
        dao: Dao,
        meetings: MeetingService,
        recorder: Recorder,
        sources: Sources,
        *,
        clock: Clock | None = None,
        notifier: Any = None,
        events: Any = None,
    ) -> None:
        self.config = config
        self.dao = dao
        self.meetings = meetings
        self.recorder = recorder
        self.sources = sources
        self.clock = clock or SystemClock()
        self.notifier = notifier
        self.events = events
        self.state = DetectorState.IDLE
        self.wake: Wake | None = None
        self.meeting_id: str | None = None
        self.muted_until: float | None = None
        self.released_at: float | None = None
        self.silent_since: float | None = None
        self.started_mono: float | None = None
        self.near_misses = 0
        self.commits = 0
        #: Who held the microphone at the previous look. ``None`` before the first one.
        self.holding: set[str] | None = None
        #: Acquisitions this detector has seen begin and not yet judged. A meeting starts;
        #: it is the *taking* of the microphone that says so, never the holding. A process
        #: leaves this set when its verdict is in, or when it lets go — and only a fresh
        #: acquisition can put it back.
        self.fresh: set[str] = set()
        #: The process whose recording is running, so that "the microphone was released"
        #: means released by *that* app rather than by whoever happened to hold it.
        self.recording_process: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- config ------------------------------------------------------------

    @property
    def mode(self) -> str:
        return str(self.config.get("detection.mode", "shadow"))

    @property
    def threshold(self) -> int:
        return int(self.config.get("detection.threshold", 5))

    @property
    def sustain_s(self) -> float:
        return float(self.config.get("detection.sustain_s", 10))

    @property
    def give_up_s(self) -> float:
        return float(self.config.get("detection.give_up_s", 90))

    @property
    def grace_s(self) -> float:
        return float(self.config.get("detection.release_grace_s", 60))

    @property
    def dual_silence_s(self) -> float:
        return float(self.config.get("detection.dual_silence_s", 300))

    @property
    def max_meeting_s(self) -> float:
        return float(self.config.get("audio.max_meeting_h", 4)) * 3600

    @property
    def watermark(self) -> int:
        return int(self.config.get("detection.near_miss_watermark", 3))

    # -- evidence ----------------------------------------------------------

    def collect(self, process: str) -> list[Evidence]:
        config = self.config
        found: list[Evidence] = []
        found.extend(
            ev.mic_evidence(
                process,
                known_apps=list(config.get("detection.known_apps", [])),
                ignore=list(config.get("detection.ignore", [])),
            )
        )
        found.extend(
            ev.speech_evidence(
                mic=self.sources.vad.sustained("me"),
                loopback=self.sources.vad.sustained("them"),
            )
        )
        titles = self.sources.titles.titles()
        found.extend(ev.title_evidence(titles, list(config.get("detection.title_patterns", []))))
        found.extend(ev.session_evidence(process, self.sources.sessions.render_processes()))
        found.extend(ev.camera_evidence(self.sources.camera.in_use()))
        return found

    def score(self, evidence: list[Evidence]) -> int:
        return ev.score(evidence, self.config.detection_weights)

    # -- the loop ----------------------------------------------------------

    def mute_for(self, seconds: float) -> None:
        """The tray's "don't detect for 1 hour" — a mute button for a private call."""
        self.muted_until = self.clock.monotonic() + seconds

    @property
    def muted(self) -> bool:
        return self.muted_until is not None and self.clock.monotonic() < self.muted_until

    def tick(self) -> DetectorState:
        """One second of the detector's life."""
        if self.mode == "off":
            return self.state
        if self.recorder.committed and self.state in (DetectorState.IDLE, DetectorState.AWAKE):
            # A recording this detector did not start: the user pressed Start, most
            # likely on the very call that woke it. Waking arms the recorder and every
            # way out of a wake discards it, so detecting through someone else's
            # recording would close the streams that recording is using.
            if self.state is DetectorState.AWAKE:
                log.info("dropping the wake: a recording is already running")
                self.wake = None
                self.state = DetectorState.IDLE
            return self.state
        holders = self.sources.mic.current_holders()
        names = {holder.process for holder in holders}
        self._note_acquisitions(holders)
        if self.state is DetectorState.IDLE:
            self._tick_idle(self.candidate(holders))
        elif self.state is DetectorState.AWAKE:
            # Only the process that woke us counts now. Taking any holder would mean a
            # permanent one keeps the wake alive for ever, long after the app it was
            # about has gone.
            wake = self.wake
            self._tick_awake(wake.process if wake and wake.process in names else "")
        elif self.state in (DetectorState.RECORDING, DetectorState.GRACE):
            owner = self.recording_process
            self._tick_recording(owner if owner and owner in names else "")
        return self.state

    def _note_acquisitions(self, holders: list[MicHolder]) -> None:
        """Track who *began* holding the microphone since the previous look.

        The first look has no previous one to compare against, so it falls back to what
        Windows itself recorded: ``LastUsedTimeStart``. That is how a machine's furniture
        — a virtual audio device that holds the microphone from boot to shutdown, a noise
        suppressor, a voice assistant — is told apart from an app that just joined a call,
        without naming a single one of them. Nothing hardcoded, nothing per-vendor.
        """
        names = {holder.process for holder in holders}
        if self.holding is None:
            self.fresh = {holder.process for holder in holders if self._is_recent(holder)}
        else:
            self.fresh |= names - self.holding
        self.fresh &= names  # letting go clears the slate; taking it again is news
        self.holding = names

    def _is_recent(self, holder: MicHolder) -> bool:
        """Only for the first look. An unreadable timestamp counts as recent: this is an
        undocumented registry artifact, and failing closed would mean a machine that does
        not report it detects nothing at all, silently."""
        since_ms = int(holder.since_ms or 0)
        if since_ms <= 0:
            return True
        age = self.clock.now() - (FILETIME_EPOCH + timedelta(milliseconds=since_ms))
        return age.total_seconds() <= float(self.config.get("detection.fresh_hold_s", 120))

    def candidate(self, holders: list[MicHolder]) -> str:
        """Which new acquisition to reason about: a known conferencing app first.

        Not simply the first holder. On a machine with a virtual audio device — Voicemeeter,
        in the case this was written for — that device is listed first every time, so the
        browser that actually joined the call was never looked at.
        """
        ignore = list(self.config.get("detection.ignore", []))
        waiting = [holder.process for holder in holders if holder.process in self.fresh]
        for process in waiting:
            if ev.matches_process(process, ignore):
                continue
            if ev.matches_process(process, list(self.config.get("detection.known_apps", []))):
                return process
        # Whatever is left, including something ignored: `_tick_idle` records that it was
        # ignored, once, and then holds its peace.
        return waiting[0] if waiting else ""

    # -- tier 0 → 1

    def _tick_idle(self, process: str) -> None:
        if not process or self.muted:
            return
        ignore = list(self.config.get("detection.ignore", []))
        if ev.matches_process(process, ignore):
            self._log_event(Outcome.IGNORED, 0, [Evidence(-5, ev.IGNORED, process)], process)
            # Said once. An ignored app that holds the microphone all day would otherwise
            # write a row a second for as long as it ran.
            self.fresh.discard(process)
            return
        # Tier 1: open both streams and start the pre-roll. Nothing is written to disk.
        # The Settings meters may be holding those endpoints, and WASAPI gives one capture
        # stream per endpoint: without this the arm raises, the tick is logged as failed
        # and the wake is lost, once a second, for as long as the microphone is held.
        # Pressing Start does the same thing for the same reason.
        from app.audio import monitor as meter

        if any(meter.active(track) is not None for track in ("me", "them")):
            log.info("taking the endpoints back from the settings meters")
            meter.release()
        self.recorder.arm()
        self.wake = Wake(process=process, started_mono=self.clock.monotonic())
        self.state = DetectorState.AWAKE
        log.info("tier 1: %s took the microphone", process)
        self._publish("awake", process=process)
        # "Within ~200 ms … start collecting evidence" (DETECTION.md §3): the wake tick
        # scores too, so the sustain window starts now rather than a second from now.
        self._tick_awake(process)

    # -- tier 2

    def _tick_awake(self, process: str) -> None:
        wake = self.wake
        assert wake is not None
        now = self.clock.monotonic()
        if not process:
            self._discard(wake, "the microphone was released")
            return
        evidence = self.collect(process)
        total = self.score(evidence)
        wake.peak_score = max(wake.peak_score, total)
        wake.evidence = evidence
        titles = self.sources.titles.titles()
        wake.title = titles[0] if titles else ""
        if ev.is_ignored(evidence) or total < self.threshold:
            wake.sustained_since = None
        elif wake.sustained_since is None:
            wake.sustained_since = now
        if wake.sustained_since is not None and now - wake.sustained_since >= self.sustain_s:
            self._commit(wake)
            return
        if now - wake.started_mono >= self.give_up_s:
            self._discard(wake, "90 s with no verdict")

    def _commit(self, wake: Wake) -> None:
        if self.mode == "shadow":
            # Everything including the pre-roll ran; nothing is committed.
            self._log_event(
                Outcome.SHADOW, wake.peak_score, wake.evidence, wake.process, title=wake.title
            )
            log.info(
                "shadow mode: would have recorded %s (score %d)", wake.process, wake.peak_score
            )
            self.recorder.discard()
            self.state = DetectorState.IDLE
            self.wake = None
            self.fresh.discard(wake.process)
            self._publish("shadow", process=wake.process, score=wake.peak_score)
            return
        meeting = self.meetings.create(
            source="detected",
            title=wake.title or None,
            title_source="window" if wake.title else None,
            evidence=[item.as_dict() for item in wake.evidence],
        )
        self.recorder.commit(meeting.path, meeting.id)
        self.meetings.committed(meeting, meeting.path)
        self.meeting_id = meeting.id
        self.recording_process = wake.process
        self.fresh.discard(wake.process)
        self.state = DetectorState.RECORDING
        self.started_mono = self.clock.monotonic()
        self.released_at = None
        self.silent_since = None
        self.commits += 1
        self._log_event(
            Outcome.COMMITTED,
            wake.peak_score,
            wake.evidence,
            wake.process,
            title=wake.title,
            meeting_id=meeting.id,
        )
        log.info("tier 3: recording %s (score %d)", meeting.id, wake.peak_score)
        if self.notifier is not None:
            self.notifier.recording_started(meeting.id, meeting.title or "")
        self._publish("recording", meeting_id=meeting.id)
        self.wake = None

    def _discard(self, wake: Wake, why: str) -> None:
        self.recorder.discard()
        self.state = DetectorState.IDLE
        self.wake = None
        self.fresh.discard(wake.process)
        if wake.peak_score >= self.watermark:
            self.near_misses += 1
            self._log_event(
                Outcome.NEAR_MISS, wake.peak_score, wake.evidence, wake.process, title=wake.title
            )
            log.info("near miss: %s peaked at %d (%s)", wake.process, wake.peak_score, why)
        self._publish("idle", reason=why)

    # -- tier 3

    def _tick_recording(self, process: str) -> None:
        now = self.clock.monotonic()
        if process:
            if self.state is DetectorState.GRACE:
                log.info("microphone re-acquired inside the grace window; same meeting")
                self.state = DetectorState.RECORDING
                self.released_at = None
        else:
            if self.released_at is None:
                self.released_at = now
                self.state = DetectorState.GRACE
            elif now - self.released_at >= self.grace_s:
                self.end("the microphone was released")
                return
        speaking = self.sources.vad.sustained("me") or self.sources.vad.sustained("them")
        if speaking:
            self.silent_since = None
        elif self.silent_since is None:
            self.silent_since = now
        elif now - self.silent_since >= self.dual_silence_s:
            self.end("both tracks were silent")
            return
        if self.started_mono is not None and now - self.started_mono >= self.max_meeting_s:
            self.end("the maximum meeting duration was reached")

    def end(self, why: str = "") -> str | None:
        meeting_id = self.meeting_id
        result = self.recorder.stop()
        duration_s = round(result.total_duration_ms / 1000)
        if meeting_id:
            meeting = self.meetings.finish(meeting_id, duration_s=duration_s)
            log.info("meeting %s ended (%s), state %s", meeting_id, why, meeting.state)
            if self.notifier is not None and meeting.state == MeetingState.RECORDED:
                self.notifier.recording_ended(meeting_id, duration_s // 60)
        if self.recording_process:
            # The meeting ended but the app still holds the microphone — the duration cap
            # fired, or both sides went quiet. If it looks like a meeting again it is a
            # new one (DETECTION.md §7.4), so this counts as a fresh acquisition. A
            # process that has genuinely let go is pruned on the next look anyway.
            self.fresh.add(self.recording_process)
        self.meeting_id = None
        self.recording_process = None
        self.state = DetectorState.IDLE
        self.released_at = None
        self.silent_since = None
        self.started_mono = None
        self._publish("idle", reason=why)
        return meeting_id

    # -- plumbing ----------------------------------------------------------

    def _log_event(
        self,
        outcome: Outcome,
        peak: int,
        evidence: list[Evidence],
        process: str,
        *,
        title: str = "",
        meeting_id: str | None = None,
    ) -> int:
        return self.dao.add_detector_event(
            peak_score=peak,
            evidence=[item.as_dict() for item in evidence],
            outcome=str(outcome),
            process=process,
            window_title=title or None,
            meeting_id=meeting_id,
        )

    def _publish(self, state: str, **payload: Any) -> None:
        if self.events is not None:
            self.events.publish("detector", state=state, **payload)

    def evidence_json(self) -> str:
        wake = self.wake
        return json.dumps([item.as_dict() for item in (wake.evidence if wake else [])])

    # -- threading ---------------------------------------------------------

    def run_forever(self, interval_s: float = 1.0) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("detector tick failed")
            self.clock.sleep(interval_s)

    def start(self) -> threading.Thread:
        self._stop.clear()  # so a detector that was stopped can be started again
        thread = threading.Thread(target=self.run_forever, name="detector", daemon=True)
        self._thread = thread
        thread.start()
        return thread

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
