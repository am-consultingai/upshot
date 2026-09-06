"""The worker loop (TECHNICAL-DESIGN.md §6.2).

One job at a time, preemptible by a starting recording, restartable after a crash. The
stage registry is injected, which is what lets the queue be proven with no real stage.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.clock import Clock, SystemClock
from app.config import Config
from app.db.dao import Dao
from app.errors import PermanentError, Preempted
from app.log import get, meeting_context, meeting_log_handler
from app.pipeline.activity import FakeRecorderState, RecorderState, SystemActivity
from app.pipeline.context import StageContext
from app.pipeline.queue import Job, JobQueue
from app.pipeline.states import (
    STAGE_DONE_STATE,
    STAGE_RUNNING_STATE,
    JobStage,
    MeetingState,
)

log = get(__name__)

StageFn = Callable[[StageContext], None]

#: Policies under which a running meeting does *not* stop the worker.
ALWAYS_RUN_POLICIES = frozenset({"asap"})


@dataclass
class WorkerStats:
    claimed: int = 0
    completed: int = 0
    failed: int = 0
    preempted: int = 0
    skipped_by_policy: int = 0


class Worker:
    def __init__(
        self,
        *,
        dao: Dao,
        queue: JobQueue,
        config: Config,
        stages: Mapping[str, StageFn],
        clock: Clock | None = None,
        recorder: RecorderState | None = None,
        activity: SystemActivity | None = None,
        services: Any = None,
    ) -> None:
        self.dao = dao
        self.queue = queue
        self.config = config
        self.stages = dict(stages)
        self.clock = clock or SystemClock()
        self.recorder: RecorderState = recorder or FakeRecorderState(False)
        self.activity = activity
        self.services = services
        self.stats = WorkerStats()
        self.last_metrics: dict[str, dict[str, Any]] = {}
        self.stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_sweep: float | None = None
        self.queue.reset_running()

    # -- policy ------------------------------------------------------------

    @property
    def policy(self) -> str:
        policy = self.config.job_policy
        if policy != "auto":
            return policy
        profile = self.config.profile
        return "after_meeting" if profile == "cpu-deferred" else "asap"

    def should_yield(self) -> bool:
        """A starting recording always wins, unless the policy is explicitly ``asap``."""
        return self.recorder.is_active() and self.policy not in ALWAYS_RUN_POLICIES

    def may_run(self) -> bool:
        if self.should_yield():
            return False
        if self.policy == "when_idle" and self.activity is not None and self.activity.is_busy():
            return False
        return not (self.policy == "scheduled" and not self.in_schedule())

    def in_schedule(self) -> bool:
        """`scheduled` runs inside a nightly window — never silently as `asap`."""
        start = int(self.config.get("schedule.hour", 2))
        length = int(self.config.get("schedule.hours", 4))
        hour = self.clock.now().hour
        return any(hour == (start + offset) % 24 for offset in range(max(1, length)))

    # -- one job -----------------------------------------------------------

    def run_once(self) -> bool:
        """Claim and execute at most one job. Returns True when work was done."""
        if not self.may_run():
            self.stats.skipped_by_policy += 1
            return False
        job = self.queue.claim_next()
        if job is None:
            return False
        self.stats.claimed += 1
        self._execute(job)
        return True

    def execute(self, job: Job) -> None:
        """Run one already-claimed job. The loop's body, exposed for tests and retries."""
        self._execute(job)

    def _execute(self, job: Job) -> None:
        stage_fn = self.stages.get(job.stage)
        meeting = self.dao.get_meeting(job.meeting_id)
        if stage_fn is None or meeting is None:
            self.queue.fail(
                job, f"no stage {job.stage!r} or meeting {job.meeting_id!r}", permanent=True
            )
            self.stats.failed += 1
            return
        context = StageContext(
            meeting=meeting,
            dao=self.dao,
            queue=self.queue,
            config=self.config,
            clock=self.clock,
            job=job,
            should_yield=self.should_yield,
            services=self.services,
            force=self.queue.take_rerun(job.meeting_id, job.stage),
        )
        handler = None
        folder = Path(meeting.folder)
        if folder.exists():
            handler = meeting_log_handler(folder)
            logging.getLogger().addHandler(handler)
        with meeting_context(meeting.id):
            self._mark_running(job)
            try:
                stage_fn(context)
            except Preempted:
                self.queue.release(job)
                self.stats.preempted += 1
                log.info("stage %s preempted by the recorder", job.stage)
                return
            except PermanentError as exc:
                self._on_failure(job, exc, permanent=True)
                return
            except Exception as exc:
                self._on_failure(job, exc, permanent=False)
                return
            finally:
                if handler is not None:
                    logging.getLogger().removeHandler(handler)
                    handler.close()
            self._on_success(job, context)

    def _stage(self, job: Job) -> JobStage | None:
        try:
            return JobStage(job.stage)
        except ValueError:
            return None

    def _mark_running(self, job: Job) -> None:
        stage = self._stage(job)
        if stage is None:
            return
        target = STAGE_RUNNING_STATE[stage]
        meeting = self.dao.require_meeting(job.meeting_id)
        if meeting.state != str(target):
            from app.pipeline.states import is_legal

            if is_legal(MeetingState(meeting.state), target):
                self.dao.set_state(job.meeting_id, target)

    def _on_success(self, job: Job, context: StageContext | None = None) -> None:
        self.queue.complete(job)
        self.stats.completed += 1
        self.last_metrics[job.stage] = dict(context.metrics) if context else {}
        stage = self._stage(job)
        if stage is not None and not (context is not None and context.hold_state):
            from app.pipeline.states import is_legal

            target = STAGE_DONE_STATE[stage]
            meeting = self.dao.require_meeting(job.meeting_id)
            current = MeetingState(meeting.state)
            if current != target and is_legal(current, target):
                self.dao.set_state(job.meeting_id, target)
        self.queue.enqueue_next_stage(job)

    def _on_failure(self, job: Job, exc: BaseException, *, permanent: bool) -> None:
        updated = self.queue.fail(job, exc, permanent=permanent)
        self.stats.failed += 1
        log.warning("stage %s failed (attempt %s): %s", job.stage, updated.attempts, exc)
        if updated.state != "failed":
            return
        # A delivery failure must never regress a rendered meeting (DESIGN.md §13).
        if job.stage == JobStage.DELIVER:
            self.dao.update_meeting(job.meeting_id, error=updated.last_error)
            return
        meeting = self.dao.require_meeting(job.meeting_id)
        from app.pipeline.states import is_legal

        if is_legal(MeetingState(meeting.state), MeetingState.FAILED):
            self.dao.set_state(job.meeting_id, MeetingState.FAILED)
        self.dao.update_meeting(job.meeting_id, error=updated.last_error)

    # -- the loop ----------------------------------------------------------

    def maybe_sweep(self) -> Any:
        """Run the retention policy, at most once every ``retention.sweep_hours``.

        From the worker loop rather than a scheduler thread: the sweep deletes the same
        folders and rows the pipeline reads, and running it on the thread that already
        serialises that work removes a whole class of race for the cost of a timestamp.
        The first call always runs, so a sweep happens shortly after every startup.
        """
        hours = float(self.config.get("retention.sweep_hours", 6))
        if hours <= 0 or self.services is None:
            return None
        now = self.clock.monotonic()
        if self._last_sweep is not None and now - self._last_sweep < hours * 3600:
            return None
        self._last_sweep = now
        try:
            from app.retention import sweep_services

            return sweep_services(self.services)
        except Exception as exc:  # a housekeeping task must never take the worker down
            log.warning("retention sweep failed: %s", exc)
            return None

    def run_forever(self, *, idle_sleep: float = 2.0, busy_sleep: float = 5.0) -> None:
        while not self.stop_event.is_set():
            if not self.may_run():
                self.clock.sleep(busy_sleep)
                continue
            if not self.run_once():
                # Only with nothing else to do: housekeeping never competes with a job.
                self.maybe_sweep()
                self.clock.sleep(idle_sleep)

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self.run_forever, name="worker", daemon=True)
        self._thread = thread
        thread.start()
        return thread

    def is_alive(self) -> bool:
        """False once the worker thread has died — the tray shows that, and the
        recorder is unaffected."""
        thread = self._thread
        return thread is None or thread.is_alive()

    def stop(self, timeout: float = 10.0) -> None:
        self.stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def drain(self, limit: int = 1000) -> int:
        """Run until the queue has nothing runnable. Returns the number of jobs executed."""
        done = 0
        while done < limit and self.run_once():
            done += 1
        return done
