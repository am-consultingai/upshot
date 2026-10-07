"""The four-tier state machine (DETECTION.md §3).

Tier 0 idle → Tier 1 wake (streams open, pre-roll filling, **nothing on disk**) →
Tier 2 confirm (score sustained) → Tier 3 committed. Everything it reacts to arrives
through injected sources and an injected clock, so the whole machine is testable with no
Windows APIs and no real time.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import SimpleNamespace
from typing import Any

from app.audio.recorder import Recorder
from app.clock import Clock, SystemClock
from app.config import Config
from app.db.dao import Dao
from app.detect import evidence as ev
from app.detect.assign import Assignment, assign, clues
from app.detect.evidence import Evidence
from app.detect.sources import MicHolder, Sources
from app.log import get
from app.meetings import MeetingService, event_key
from app.meetings import calendar_payload as stored_calendar
from app.pipeline.states import MeetingState

log = get(__name__)

#: ``LastUsedTimeStart`` counts from 1601, as every Windows FILETIME does.
FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=UTC)


class DetectorState(StrEnum):
    IDLE = "idle"
    AWAKE = "awake"  # tier 1 + 2
    RECORDING = "recording"  # tier 3
    GRACE = "grace"  # mic released, waiting to see if it comes back


#: How long before a calendar meeting its reminder comes (D76).
REMINDER_S = 300.0


def _exe_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path)) if path else ""


def own_executables() -> frozenset[str]:
    """The executables Upshot itself runs as, in ConsentStore spelling.

    The settings meters and an armed recorder hold the microphone from this very
    process, and Windows lists it like any other holder. Taken for a meeting app, it
    armed the recorder, which took the endpoints back from the meters, which reopened
    them, which woke the detector again: about twenty reopens a second, no level ever
    shown, and a manual recording padded with a minute of pre-roll (machine B, job 013).
    Both paths count: under a venv, Windows reports the base interpreter, not the
    launcher in ``sys.executable``. And Windows reports the path with links resolved:
    uv's ``cpython-3.14-…`` folder is a junction to ``cpython-3.14.7-…`` (job 014).
    """
    paths = {sys.executable, getattr(sys, "_base_executable", "")}
    paths |= {os.path.realpath(path) for path in paths if path}
    return frozenset(_exe_key(path) for path in paths if path)


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
        calendar: Any = None,
        prompts: Any = None,
    ) -> None:
        self.config = config
        self.dao = dao
        self.meetings = meetings
        self.recorder = recorder
        self.sources = sources
        self.clock = clock or SystemClock()
        self.notifier = notifier
        self.events = events
        #: ``app.gcal.source.CalendarNow``, or None. Read from the local cache only.
        self.calendar = calendar
        #: ``app.prompts.Prompts``: the offer to record the banner and the toasts share.
        self.prompts = prompts
        #: Calendar meetings already reminded of, and already announced as started: each
        #: is said once (D76).
        self.reminded: set[tuple[str, str, str]] = set()
        self.announced: set[tuple[str, str, str]] = set()
        #: The running recording's calendar meeting end, and whether its overrun was said.
        self.event_end: datetime | None = None
        self.event_title: str = ""
        self.overrun_said = False
        #: When a recording whose call just ended saves itself, if nothing changes (D77).
        self.ending_at: datetime | None = None
        #: "Keep recording": the user says the meeting goes on without the call's app.
        self.keep_going = False
        #: The detector's tick runs on its own thread; Stop now and Keep recording come
        #: from the page or a toast. One at a time, so a recording is never ended twice.
        self._lock = threading.RLock()
        #: Holders that are Upshot itself, never a meeting (see ``own_executables``).
        self.own = own_executables()
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
        #: Who held the microphone when the call's app let go: only an app that takes it
        #: after that can be the next call (D90).
        self.held_at_release: set[str] = set()
        #: Who held it at the look before this one.
        self.held_before: set[str] = set()
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
        event = self.calendar_event()
        found.extend(ev.calendar_evidence(event.title or "" if event else None))
        return found

    def calendar_event(self) -> Any:
        if self.calendar is None:
            return None
        try:
            return self.calendar.current(self.clock.now())
        except Exception:  # the calendar is advisory; detection never fails on it
            log.exception("calendar lookup failed")
            return None

    def assignment(self, process: str | None) -> Assignment:
        """Which calendar meeting a call by ``process`` is (D89): the meetings on now,
        told apart by the window titles and their links. Never certain by a guess."""
        if self.calendar is None:
            return Assignment(None, False, (), "no calendar")
        now = self.clock.now()
        try:
            live = getattr(self.calendar, "live", None)
            if live is not None:
                events = list(live(now))
            else:
                current = self.calendar.current(now)
                events = [current] if current is not None else []
            windows = list(self.sources.titles.titles())
        except Exception:  # the calendar is advisory; detection never fails on it
            log.exception("calendar lookup failed")
            return Assignment(None, False, (), "the calendar could not be read")
        return assign(events, process=process, windows=windows)

    def assignment_now(self) -> Assignment:
        """The calendar meeting of the call on now, from the call app holding the
        microphone: for a Start that names no meeting."""
        known = list(self.config.get("detection.known_apps", []))
        try:
            holders = [h.process for h in self.sources.mic.current_holders()]
        except Exception:
            holders = []
        apps = [p for p in holders if ev.matches_process(p, known)]
        return self.assignment(apps[0] if apps else None)

    def _calendar_connected(self) -> bool:
        if self.calendar is None:
            return False
        active = getattr(self.calendar, "active", None)
        try:
            return bool(active()) if active is not None else True
        except Exception:
            return False

    def calendar_payload(self, found: Assignment) -> dict[str, Any] | None:
        """What a recording keeps of ``found``: the meeting, matched by the detector, or the
        meetings it may be, proposed. None with no calendar meeting on."""
        from app.gcal.source import proposal, snapshot

        if found.event is not None and found.certain:
            make = getattr(self.calendar, "snapshot", None)
            if make is not None:
                return dict(make(found.event, source="detected"))
            return snapshot(found.event, state="matched", source="detected", confidence=1.0)
        if found.candidates:
            return proposal(found.candidates, source="detected", reason=found.reason)
        return None

    def remind(self) -> None:
        """Calendar meetings, by the clock alone (D76): a reminder five minutes before, and
        at the start time an offer to record, with a toast.

        A meeting first seen after it started (the sync ran late, or the app was just
        started) is still announced while it is on. Nothing here arms or commits: a
        blocked-out hour is not a call, and only a microphone or the user starts one.
        """
        if self.calendar is None:
            return
        now = self.clock.now()
        try:
            events = self.calendar.soon(now, ahead_s=REMINDER_S + 60)
        except Exception:  # the calendar is advisory; detection never fails on it
            log.exception("calendar lookup failed")
            return
        recording = self.recorder.committed
        started: list[Any] = []
        for event in events:
            to_start = (event.start - now).total_seconds()
            title = event.title or ""
            account_id, calendar_id, event_id = event.key
            if 0 < to_start <= REMINDER_S and event.key not in self.reminded:
                self.reminded.add(event.key)
                log.info("calendar: %s starts in %d s", title or "a meeting", int(to_start))
                if self.notifier is not None:
                    self.notifier.meeting_soon(
                        ":".join(event.key),
                        title,
                        minutes=max(1, round(to_start / 60)),
                        conference_url=event.conference_url,
                    )
            if to_start <= 0 and now < event.end and event.key not in self.announced:
                self.announced.add(event.key)
                self.reminded.add(event.key)
                if recording:
                    continue  # already recording: most likely this very meeting
                started.append(event)
        if not started:
            return
        offered = True
        if self.prompts is not None:
            from app.prompts import Prompt

            if len(started) == 1:
                only = started[0]
                prompt = Prompt(
                    kind="calendar",
                    title=only.title or "",
                    at=now,
                    calendar_id=only.calendar_id,
                    event_id=only.event_id,
                    conference_url=only.conference_url,
                    until=only.end,
                    account_id=only.account_id,
                )
            else:
                # Booked at the same time (D89): one offer, the user picks which.
                prompt = Prompt(
                    kind="calendar",
                    title=" / ".join(e.title or "" for e in started[:3]),
                    at=now,
                    until=max(e.end for e in started),
                    candidates=tuple(
                        (e.account_id, e.calendar_id, e.event_id, e.title or "")
                        for e in started[:3]
                    ),
                )
            offered = self.prompts.offer(prompt, recording=recording)
        for event in started:
            title = event.title or ""
            account_id, calendar_id, event_id = event.key
            log.info("calendar: %s has started and nothing is recording", title or "a meeting")
            self._publish("upcoming", event=title, starts_at=event.start.isoformat())
            if offered and self.notifier is not None:
                # One notification each: its Start names its own meeting.
                self.notifier.meeting_starting(
                    ":".join(event.key),
                    title,
                    calendar_id=calendar_id,
                    event_id=event_id,
                    minutes_ago=max(0, int(-(event.start - now).total_seconds() // 60)),
                    conference_url=event.conference_url,
                    account_id=account_id,
                )

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
        with self._lock:
            return self._tick()

    def _tick(self) -> DetectorState:
        if self.mode == "off":
            return self.state
        holders = [
            holder
            for holder in self.sources.mic.current_holders()
            if _exe_key(holder.process) not in self.own
        ]
        names = {holder.process for holder in holders}
        before = self.holding
        self.held_before = set(before or ())
        self._note_acquisitions(holders)
        if before is not None and self.state in (DetectorState.RECORDING, DetectorState.GRACE):
            # Said in the log while recording, when nothing else would say it: the
            # microphone was the whole story of a missed hang-up on machine B (D78).
            for process in sorted(names - before):
                log.info("recording %s: %s took the microphone", self.meeting_id, process)
            for process in sorted(before - names):
                log.info("recording %s: %s let go of the microphone", self.meeting_id, process)
        self.remind()
        if self.prompts is not None:
            self.prompts.tick(self.clock.now(), recording=self.recorder.committed, holders=names)
        if self.recorder.committed and self.state in (DetectorState.IDLE, DetectorState.AWAKE):
            # A recording this detector did not start: the user pressed Start, most likely
            # on the very call that woke it. A wake is dropped (every way out of a wake
            # discards the recorder, which would close the streams that recording uses),
            # and the recording is adopted, so the same end signals end it (D76): before,
            # one started by hand never stopped by itself.
            if self.state is DetectorState.AWAKE:
                log.info("dropping the wake: a recording is already running")
                self.wake = None
            self._adopt(holders)
            return self.state
        if (
            self.state in (DetectorState.RECORDING, DetectorState.GRACE)
            and not self.recorder.committed
        ):
            # Stopped from elsewhere: the page, the tray or a notification's Stop.
            log.info("the recording was stopped elsewhere")
            self._forget_recording()
            return self.state
        if self.state is DetectorState.IDLE:
            self._tick_idle(self.candidate(holders))
        elif self.state is DetectorState.AWAKE:
            # Only the process that woke us counts now. Taking any holder would mean a
            # permanent one keeps the wake alive for ever, long after the app it was
            # about has gone.
            wake = self.wake
            self._tick_awake(wake.process if wake and wake.process in names else "")
        elif self.state in (DetectorState.RECORDING, DetectorState.GRACE):
            if self.recording_process is None and not self.keep_going:
                self._join_late(holders)
            if self.state is DetectorState.GRACE and self._taken_again(holders):
                return self.state
            owner = self.recording_process
            self._tick_recording(owner if owner and owner in names else "")
        return self.state

    def _join_late(self, holders: list[MicHolder]) -> None:
        """A recording with no call app yet takes the first one that joins (D78).

        "Join and record" starts the recording and opens the meeting at once, so the call
        app takes the microphone a moment *after* the recording began: on machine B two
        seconds after, and the hang-up then went unnoticed. Only a known call app that
        newly takes the microphone counts, so a recording of an in-person meeting is not
        claimed by something that has held it all along.
        """
        known = list(self.config.get("detection.known_apps", []))
        ignore = list(self.config.get("detection.ignore", []))
        for holder in holders:
            process = holder.process
            if (
                process in self.fresh
                and ev.matches_process(process, known)
                and not ev.matches_process(process, ignore)
            ):
                self.recording_process = process
                self.fresh.discard(process)
                log.info(
                    "recording %s: %s joined; it ends when that app lets go of the microphone",
                    self.meeting_id,
                    process,
                )
                return

    def _adopt(self, holders: list[MicHolder]) -> None:
        """Take on a recording started by the user, to end it like one of our own.

        The app it belongs to is the known meeting app holding the microphone now, if one
        is: when that app lets go, the call is over. With none (an in-person meeting, or a
        call app not on the list) only silence, the calendar and the cap can end it.
        """
        known = list(self.config.get("detection.known_apps", []))
        ignore = list(self.config.get("detection.ignore", []))
        apps = [
            holder.process
            for holder in holders
            if ev.matches_process(holder.process, known)
            and not ev.matches_process(holder.process, ignore)
        ]
        self.meeting_id = self.recorder.meeting_id
        self.recording_process = apps[0] if apps else None
        # Whoever holds the microphone now is part of this recording's call, not news:
        # stopping it while still on the call must not wake on that same call again.
        self.fresh -= {holder.process for holder in holders}
        self.state = DetectorState.RECORDING
        self.started_mono = self.clock.monotonic()
        self.released_at = None
        self.silent_since = None
        self._note_event()
        log.info(
            "adopted recording %s (%s)",
            self.meeting_id,
            f"ends when {self.recording_process} lets go of the microphone"
            if self.recording_process
            else "no call app holds the microphone",
        )

    def _note_event(self) -> None:
        """The calendar meeting a recording belongs to, for its scheduled end.

        The recording's own meeting first: one started for a named calendar meeting is
        matched to it at once. Only without one is it whatever is on now, and "whatever
        is on now" was wrong on machine B, where a real 10:00 meeting overlapped the one
        being recorded and its end was waited for instead (D76).
        """
        self.overrun_said = False
        self.event_end, self.event_title = None, ""
        meeting = self.dao.get_meeting(self.meeting_id) if self.meeting_id else None
        if meeting is not None and meeting.calendar_json:
            try:
                snap = json.loads(meeting.calendar_json)
                end = (snap.get("event") or {}).get("end")
                if end:
                    self.event_end = datetime.fromisoformat(str(end))
                    self.event_title = str(snap.get("title") or "")
                    return
            except (ValueError, TypeError, AttributeError):
                pass  # an unreadable snapshot: fall back to the calendar
        found = self.assignment(self.recording_process)
        if found.event is not None:
            self.event_end, self.event_title = found.event.end, found.event.title or ""

    def keep(self, meeting_id: str | None) -> bool:
        """ "Keep recording": the meeting goes on though the call's app let go (D77).

        What was heard since is written, and the microphone no longer ends this
        recording; silence, the calendar and the cap still can.
        """
        with self._lock:
            if meeting_id is not None and meeting_id != self.meeting_id:
                return False
            if self.state is DetectorState.GRACE:
                self.recorder.release_hold(keep=True)
                self.state = DetectorState.RECORDING
                self.released_at = None
            self.ending_at = None
            self.keep_going = True
            self.recording_process = None
            log.info("recording %s: kept going by the user", self.meeting_id)
            self._publish("recording", meeting_id=self.meeting_id)
            return True

    def end_now(self, meeting_id: str | None, why: str) -> str | None:
        """Stop now, from the page or a toast, for a recording this detector follows."""
        with self._lock:
            if self.meeting_id is None or (
                meeting_id is not None and meeting_id != self.meeting_id
            ):
                return None
            if self.state not in (DetectorState.RECORDING, DetectorState.GRACE):
                return None
            return self.end(why)

    def _forget_recording(self) -> None:
        self.held_at_release = set()
        self.ending_at = None
        self.keep_going = False
        self.meeting_id = None
        self.recording_process = None
        self.state = DetectorState.IDLE
        self.released_at = None
        self.silent_since = None
        self.started_mono = None
        self.event_end = None
        self.overrun_said = False

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
            found = self.assignment(wake.process)
            event = found.event
            choices = () if event is not None else found.candidates[:3]
            log.info(
                "the call is %s (%s)",
                repr(event.title) if event is not None else "no one calendar meeting",
                found.reason,
            )
            title = (
                event.title
                if event is not None
                else " / ".join(e.title or "" for e in choices)
                if choices
                else wake.title
            )
            self._publish(
                "shadow",
                process=wake.process,
                score=wake.peak_score,
                event=title if (event is not None or choices) else None,
            )
            # Detect and notify (D64): the offer reaches an open window as the banner, and
            # everyone else as the toast. Both come from one offer (D76), so neither shows
            # for a meeting already being recorded or already turned down.
            offered = True
            if self.prompts is not None:
                from app.prompts import Prompt

                offered = self.prompts.offer(
                    Prompt(
                        kind="detected",
                        title=title or "",
                        at=self.clock.now(),
                        calendar_id=event.calendar_id if event else None,
                        event_id=event.event_id if event else None,
                        conference_url=event.conference_url if event else None,
                        until=event.end if event else None,
                        process=wake.process,
                        account_id=event.account_id if event else None,
                        candidates=tuple(
                            (e.account_id, e.calendar_id, e.event_id, e.title or "")
                            for e in choices
                        ),
                    ),
                    recording=self.recorder.committed,
                )
            for announced in (event,) if event is not None else choices:
                self.announced.add(announced.key)  # the call is the announcement
            if offered and self.notifier is not None:
                self.notifier.call_detected(
                    wake.process,
                    title,
                    calendar_id=event.calendar_id if event else None,
                    event_id=event.event_id if event else None,
                    account_id=event.account_id if event else None,
                    candidates=tuple(
                        (e.account_id, e.calendar_id, e.event_id, e.title or "") for e in choices
                    ),
                )
            return
        found = self.assignment(wake.process)
        payload = self.calendar_payload(found)
        # The same calendar meeting recorded moments ago (a rejoin, a restart): carry that
        # recording on rather than start another (D88). Only a meeting the clues settle.
        meeting = self._begin(
            payload,
            payload,
            title=wake.title or None,
            evidence=[item.as_dict() for item in wake.evidence],
        )
        self.meeting_id = meeting.id
        self._note_event()
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
        if self.recording_process is None:
            pass  # no call app to watch (adopted with none, or kept going): other signals
        elif process:
            if self.state is DetectorState.GRACE:
                self._rejoin()
        else:
            if self.released_at is None:
                # The call's app let go: most likely the user hung up. Say so at once and
                # stop writing, so the file ends here; wait out the grace before saving,
                # in case the call comes back (D77).
                self.released_at = now
                # At the look before the hang-up: an app taking the microphone in the same
                # second as the call's app lets go is the next call, not furniture.
                self.held_at_release = self.held_before - {self.recording_process or ""}
                self.state = DetectorState.GRACE
                self.recorder.hold()
                self.ending_at = self.clock.now() + timedelta(seconds=self.grace_s)
                log.info("the call's app let go of the microphone; saving in %.0f s", self.grace_s)
                self._publish(
                    "ending", meeting_id=self.meeting_id, ends_at=self.ending_at.isoformat()
                )
                if self.notifier is not None and self.meeting_id:
                    self.notifier.call_ended(
                        self.meeting_id, self.event_title, seconds=int(self.grace_s)
                    )
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
            return
        wall = self.clock.now()
        if self.event_end is not None and wall >= self.event_end and not self.overrun_said:
            # The meeting's scheduled end has passed and the call is still on (had it
            # ended, the microphone rule would have stopped it). Overruns are normal:
            # ask, do not cut (D76).
            self.overrun_said = True
            log.info("recording %s runs past its scheduled end", self.meeting_id)
            if self.notifier is not None and self.meeting_id:
                self.notifier.still_recording(self.meeting_id, self.event_title)

    def _rejoin(self) -> None:
        """Back in the call (a rejoin, a dropped connection): the same meeting goes on.
        The dead air between is not written; the timeline keeps its length."""
        log.info("microphone re-acquired inside the grace window; same meeting")
        self.recorder.release_hold(keep=False)
        self.state = DetectorState.RECORDING
        self.released_at = None
        self.ending_at = None
        self._publish("recording", meeting_id=self.meeting_id)

    # -- the next meeting (D90)

    def _taken_again(self, holders: list[MicHolder]) -> bool:
        """The microphone, let go by the call's app, taken again within the grace: the
        same meeting coming back, or the next one. Whether this handled it.

        The call's app taking it again is a rejoin, unless the calendar says the next
        meeting is on. Another call app taking it is the next call, unless the calendar
        says it is the same meeting. Back-to-back meetings always let go of the microphone
        between them, if only for a moment; before this, the grace read the next meeting
        as the last one coming back and recorded both as one.
        """
        owner = self.recording_process
        names = {holder.process for holder in holders}
        if owner and owner in names:
            process, same_app = owner, True
        else:
            known = list(self.config.get("detection.known_apps", []))
            ignore = list(self.config.get("detection.ignore", []))
            newcomers = [
                holder.process
                for holder in holders
                if holder.process in self.fresh
                and holder.process not in self.held_at_release
                and ev.matches_process(holder.process, known)
                and not ev.matches_process(holder.process, ignore)
            ]
            if not newcomers:
                return False
            process, same_app = newcomers[0], False
        verdict, payload, follow = self._which_meeting(process)
        if verdict == "unknown" and not same_app:
            # Nothing says which meeting it is: the grace runs out, and the new app is
            # judged like any call, by its evidence (D77).
            return False
        if verdict in ("same", "unknown"):
            if same_app:
                return False  # the rejoin, as ever
            log.info("recording %s: %s took over the same meeting", self.meeting_id, process)
            self.recording_process = process
            self.fresh.discard(process)
            self._rejoin()
            return True
        self._hand_over(process, payload, follow)
        return True

    def _which_meeting(
        self, process: str
    ) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
        """Whether a call by ``process`` now is the recording's own calendar meeting
        ("same"), another ("next"), or cannot be told ("unknown"). With what the next
        recording keeps of the calendar, and the meeting it may continue.

        Another meeting named by a clue the last one lacks (the window, the app) is that
        meeting. Told by the clock alone (the last one's time is over, or the next began
        later), it may be the last one running over: then it is proposed, both meetings
        offered, and the user asked at its end.
        """
        meeting = self.dao.get_meeting(self.meeting_id) if self.meeting_id else None
        current = stored_calendar(meeting) if meeting is not None else {}
        key = event_key(current)
        found = self.assignment(process)
        payload = self.calendar_payload(found)
        if key is None or found.event is None or not found.certain or payload is None:
            return "unknown", payload, payload
        if event_key(payload) == key:
            return "same", None, None
        try:
            windows = list(self.sources.titles.titles())
        except Exception:
            windows = []
        last = SimpleNamespace(
            title=current.get("title"), conference_url=current.get("conference_url")
        )
        if clues(found.event, process, windows) > clues(last, process, windows):
            return "next", payload, payload
        from app.gcal.match import PROPOSED

        guess = {
            "match": {
                "state": PROPOSED,
                "source": "detected",
                "confidence": None,
                "reason": "the next calendar meeting began, or the last one came back",
            },
            "candidates": [
                {**(payload.get("event") or {}), "title": payload.get("title"), "overlap": None},
                {**(current.get("event") or {}), "title": current.get("title"), "overlap": None},
            ],
        }
        return "next", guess, payload

    def _hand_over(
        self, process: str, payload: dict[str, Any] | None, follow: dict[str, Any] | None
    ) -> None:
        """The last recording ends where its call did, and the next one starts with what
        was heard since, as its pre-roll. Detect-only mode starts nothing by itself: the
        last one ends, and the next call is offered like any other."""
        why = "the next meeting took the microphone"
        if self.mode != "on":
            self.fresh.add(process)
            self.end(why)
            return
        result = self.recorder.split()
        if result is None:
            self.fresh.add(process)
            self.end(why)
            return
        last = self.meeting_id
        self._finished(last, result, why)
        titles = self._titles()
        meeting = self._begin(
            payload,
            follow,
            title=titles[0] if titles else None,
            evidence=[],
        )
        self.meeting_id = meeting.id
        self._note_event()
        self.recording_process = process
        self.fresh.discard(process)
        self.state = DetectorState.RECORDING
        self.started_mono = self.clock.monotonic()
        self.released_at = None
        self.ending_at = None
        self.silent_since = None
        self.keep_going = False
        self.commits += 1
        log.info("recording %s ended; the next meeting is recording %s", last, meeting.id)
        if self.notifier is not None:
            self.notifier.recording_started(meeting.id, meeting.title or "")
        self._publish("recording", meeting_id=meeting.id)

    def _titles(self) -> list[str]:
        try:
            return list(self.sources.titles.titles())
        except Exception:
            return []

    def _begin(
        self,
        payload: dict[str, Any] | None,
        follow: dict[str, Any] | None,
        *,
        title: str | None,
        evidence: list[dict[str, Any]],
    ) -> Any:
        """A recording for a call the detector settled on: the same calendar meeting
        recorded moments ago carried on (D88), else a new one. ``follow``: the meeting
        it may carry on."""
        earlier = self.meetings.to_continue(follow)
        reopened = self.meetings.reopen(earlier.id) if earlier is not None else None
        if reopened is not None:
            meeting, after_ms = reopened
            self.recorder.commit(meeting.path, meeting.id, after_ms=after_ms)
        else:
            meeting = self.meetings.create(
                source="detected",
                title=title or None,
                title_source="window" if title else None,
                evidence=evidence,
            )
            if payload is not None:
                meeting = self.meetings.choose_event(meeting.id, payload)
            self.recorder.commit(meeting.path, meeting.id)
        self.meetings.committed(meeting, meeting.path)
        return meeting

    def end(self, why: str = "") -> str | None:
        meeting_id = self.meeting_id
        result = self.recorder.stop()
        self._finished(meeting_id, result, why)
        if self.recording_process:
            # The meeting ended but the app still holds the microphone — the duration cap
            # fired, or both sides went quiet. If it looks like a meeting again it is a
            # new one (DETECTION.md §7.4), so this counts as a fresh acquisition. A
            # process that has genuinely let go is pruned on the next look anyway.
            self.fresh.add(self.recording_process)
        self._forget_recording()
        self._publish("idle", reason=why)
        return meeting_id

    def _finished(self, meeting_id: str | None, result: Any, why: str) -> None:
        """A recording's file is closed: the meeting is saved, said, and asked about."""
        duration_s = round(result.total_duration_ms / 1000)
        if meeting_id:
            meeting = self.meetings.finish(meeting_id, duration_s=duration_s)
            log.info("meeting %s ended (%s), state %s", meeting_id, why, meeting.state)
            if meeting.id != meeting_id:
                log.info("recording %s joined the earlier recording %s", meeting_id, meeting.id)
            if self.notifier is not None and meeting.state == MeetingState.RECORDED:
                self.notifier.recording_ended(meeting.id, duration_s // 60, reason=why)
            self.meetings.ask_if_unsettled(
                meeting.id, self.notifier, calendar_connected=self._calendar_connected()
            )

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
        """Record a verdict, in the log file as well as the database.

        Despite the name this only ever wrote a row to `detector_events`. That
        table was read by exactly one thing — the Detector screen — so when the
        screen was removed on 2026-09-22 the whole audit trail would have become
        invisible: the question "why did it record that?" had no answer outside a
        SQL client. One line per verdict, at INFO, restores it.

        The evidence is summarised rather than dumped. The full items stay in the
        row; what belongs on one line is which signals fired and how hard.
        """
        row_id = self.dao.add_detector_event(
            peak_score=peak,
            evidence=[item.as_dict() for item in evidence],
            outcome=str(outcome),
            process=process,
            window_title=title or None,
            meeting_id=meeting_id,
        )
        why = ", ".join(f"{item.code}{item.weight:+d}" for item in evidence) or "no evidence"
        log.info(
            "detector %s: %s scored %d (%s)%s%s",
            outcome,
            process or "unknown process",
            peak,
            why,
            f" — {title}" if title else "",
            f" — meeting {meeting_id}" if meeting_id else "",
        )
        return row_id

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
