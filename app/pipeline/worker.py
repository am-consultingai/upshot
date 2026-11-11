"""The worker loop (TECHNICAL-DESIGN.md §6.2).

One job at a time, preemptible by a starting recording, restartable after a crash. The
stage registry is injected, which is what lets the queue be proven with no real stage.
"""

from __future__ import annotations

import functools
import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from app import meta
from app.clock import Clock, SystemClock
from app.config import Config
from app.db.dao import Dao
from app.errors import (
    Cancelled,
    Deferred,
    PermanentError,
    Preempted,
    QuotaExhausted,
    SignInRequired,
    describe,
)
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


def _minutes_bucket(duration_s: int | None) -> str | None:
    """A meeting's length for a crash report, coarsely: enough to see that long meetings
    fail, not enough to tell one meeting from another."""
    if not duration_s:
        return None
    minutes = duration_s / 60
    for edge in (15, 30, 60, 120):
        if minutes < edge:
            return f"<{edge}"
    return "120+"


#: A provider still signed out after this long is mentioned again (a day of waiting
#: summaries is worth a second word; every fifteen-minute retry is not).
SIGNED_OUT_TOLD_S = 6 * 3600.0

StageFn = Callable[[StageContext], None]

#: Policies under which a running meeting does *not* stop the worker.
ALWAYS_RUN_POLICIES = frozenset({"asap"})
#: A file job whose ffmpeg could not start waits this long before it tries again.
FFMPEG_DEFER_S = 300


@dataclass
class WorkerStats:
    claimed: int = 0
    completed: int = 0
    failed: int = 0
    preempted: int = 0
    skipped_by_policy: int = 0


def settle_stopped(dao: Dao, meeting_id: str, before: str) -> None:
    """A meeting whose transcription the user stopped goes back to where it was: a
    re-transcription to its transcript and summary, which nothing has replaced (the
    stage writes its output only once it has finished), a first one to "recorded".

    From a state that says nothing of what is on disk (still transcribing after a
    restart, failed, interrupted), the transcript decides which.
    """
    from app.pipeline.stages.assemble import transcript_paths
    from app.pipeline.states import is_legal

    meeting = dao.get_meeting(meeting_id)
    if meeting is None:
        return
    target = MeetingState(before)
    if target in (MeetingState.TRANSCRIBING, MeetingState.FAILED, MeetingState.INTERRUPTED):
        transcribed = transcript_paths(Path(meeting.folder))[1].exists()
        target = MeetingState.TRANSCRIBED if transcribed else MeetingState.RECORDED
    current = MeetingState(meeting.state)
    if current != target and is_legal(current, target):
        dao.set_state(meeting_id, target)
    log.info("meeting %s: transcription stopped by the user; back to %s", meeting_id, target)


def stop_transcription(svc: Any, meeting_id: str) -> str | None:
    """Stop a meeting's transcription, for every place that offers it (the page, the
    tray, the notification): ``"waiting"``, ``"running"`` or ``None`` (nothing to stop)."""
    stopped = svc.queue.request_stop(meeting_id)
    if stopped is None:
        return None
    if stopped == "waiting":
        # It never started; only a merge (D89) moves a meeting to TRANSCRIBING ahead of it.
        meeting = svc.dao.get_meeting(meeting_id)
        if meeting is not None:
            settle_stopped(svc.dao, meeting_id, meeting.state)
        svc.events.publish("job", meeting_id=meeting_id, stage="transcribe", state="cancelled")
    if svc.notifier is not None:
        # "Meeting ended. Transcribing…" is no longer true.
        svc.notifier.withdraw(meeting_id)
    log.info("meeting %s: transcription stop requested (%s)", meeting_id, stopped)
    return str(stopped)


#: How often the worker tries again to remove deleted transcriptions whose files were in use.
FINISH_DELETES_S = 60.0


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
        transcriptions: Any = None,
        scheduler: Any = None,
    ) -> None:
        self.dao = dao
        self.queue = queue
        self.config = config
        self.stages = dict(stages)
        self.clock = clock or SystemClock()
        self.recorder: RecorderState = recorder or FakeRecorderState(False)
        self.activity = activity
        self.services = services
        #: When each provider's sign-out was last told (monotonic): one toast per sign-out.
        self._told_signed_out: dict[str, float] = {}
        #: When each provider's sign-out was last answered by the fallback, told the same way.
        self._told_fallback: dict[str, float] = {}
        #: File transcription (D86): the store of file jobs, and the scheduler that picks
        #: between them and meeting jobs, first in, first out. Without them the worker
        #: runs meeting jobs only, as it always has.
        self.transcriptions = transcriptions
        self.scheduler = scheduler
        self.stats = WorkerStats()
        #: True while a job runs: the computer is kept awake for it (app/power.py).
        self.busy = False
        self.last_metrics: dict[str, dict[str, Any]] = {}
        self.stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_sweep: float | None = None
        self._last_deletes: float | None = None
        self._auto_profile: tuple[tuple[Any, Any], str] | None = None
        self.queue.reset_running()
        if self.transcriptions is not None:
            self.transcriptions.reset_running()

    # -- policy ------------------------------------------------------------

    @property
    def policy(self) -> str:
        policy = self.config.job_policy
        if policy != "auto":
            return policy
        return "after_meeting" if self.profile() == "cpu-deferred" else "asap"

    def profile(self) -> str:
        """``profile = "auto"`` resolved as at start-up (``bootstrap.choose_profile``): a GPU
        that will run the model transcribes during a meeting, a CPU after it (D60). Taken
        as is, a saved ``auto`` read as live, so a CPU-only machine transcribed under a
        recording (the test machine, 2026-10-05). Cached per device setting: the plan searches
        the disk for CUDA and asks the GPU for its memory."""
        configured = self.config.profile
        if configured != "auto":
            return configured
        key = (self.config.get("asr.device", "auto"), self.config.get("asr.cuda_dir"))
        if self._auto_profile is None or self._auto_profile[0] != key:
            from app.asr.local import planned_device
            from app.asr.models import HEBREW

            # The Hebrew model's device, the default language's (D99).
            device = planned_device(self.config, role=HEBREW)
            self._auto_profile = (key, "gpu-live" if device == "cuda" else "cpu-deferred")
        return self._auto_profile[1]

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

    def waiting_reason(self) -> str:
        """Why waiting work is not running now: ``recording`` (a meeting is being recorded
        and the policy yields to it, which is also what holds ``after_meeting``),
        ``policy:when_idle``, ``policy:scheduled``, or just ``queue``."""
        if self.should_yield():
            return "recording"
        if self.policy == "when_idle" and self.activity is not None and self.activity.is_busy():
            return "policy:when_idle"
        if self.policy == "scheduled" and not self.in_schedule():
            return "policy:scheduled"
        return "queue"

    # -- one job -----------------------------------------------------------

    def run_once(self) -> bool:
        """Claim and execute at most one job. Returns True when work was done."""
        if not self.may_run():
            self.stats.skipped_by_policy += 1
            return False
        claimed = self.scheduler.claim_next_any() if self.scheduler else self.queue.claim_next()
        if claimed is None:
            return False
        self.stats.claimed += 1
        self.busy = True
        try:
            if isinstance(claimed, Job):
                self._execute(claimed)
            else:
                self._execute_file(claimed)
        finally:
            self.busy = False
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
        # Where a transcription the user stops takes the meeting back to.
        before = meeting.state
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
        # The transcribe stage says how far it has got (app/pipeline/progress.py); the
        # others have no honest percentage, and the page labels them instead.
        tracker = getattr(self.services, "progress", None) if self.services else None
        if tracker is not None and job.stage == JobStage.TRANSCRIBE:
            context.report = functools.partial(tracker.report, job.meeting_id)
        else:
            tracker = None
        handler: logging.Handler | None = None

        def release() -> None:
            """The progress entry and the meeting's log handler end with the run. Both
            steps are idempotent, so it can run from either ``finally`` below."""
            if tracker is not None:
                tracker.finish(job.meeting_id)
            if handler is not None:
                logging.getLogger().removeHandler(handler)
                handler.close()

        # Everything from the progress entry's start is inside a ``try``: a log handler,
        # ``_mark_running`` or ``_announce`` that raises must not leave the entry behind,
        # showing a run that is no longer happening.
        try:
            if tracker is not None:
                tracker.start(job.meeting_id, job.stage)
            folder = Path(meeting.folder)
            # The provider picked on the meeting page that this run starts with, if any: a
            # final failure ends that pick, and only that one.
            ran_with = (
                meta.read(folder).get("summary_provider")
                if job.stage == JobStage.SUMMARIZE and folder.exists()
                else None
            )
            if folder.exists():
                handler = meeting_log_handler(folder)
                logging.getLogger().addHandler(handler)
            with meeting_context(meeting.id):
                self._mark_running(job)
                self._announce(job)
                try:
                    stage_fn(context)
                    if self.queue.deleting(job.meeting_id):
                        raise Cancelled(f"{job.stage} finished, but the meeting is being deleted")
                except Cancelled as exc:
                    log.info("%s", exc)  # the deletion itself is finished below
                    if not self.queue.deleting(job.meeting_id) and self.queue.stopping(
                        job.meeting_id
                    ):
                        self.queue.stopped(job)
                        settle_stopped(self.dao, job.meeting_id, before)
                    return
                except Preempted:
                    self._keep_force(job, context)
                    self.queue.release(job)
                    self.stats.preempted += 1
                    log.info("stage %s preempted by the recorder", job.stage)
                    return
                except Deferred as exc:
                    self._keep_force(job, context)
                    self._on_deferred(job, exc)
                    return
                except PermanentError as exc:
                    describe(exc, summary_windows=context.metrics.get("summary_windows"))
                    self._on_failure(job, exc, permanent=True, ran_with=ran_with)
                    return
                except Exception as exc:
                    describe(exc, summary_windows=context.metrics.get("summary_windows"))
                    self._keep_force(job, context)
                    self._on_failure(job, exc, permanent=False, ran_with=ran_with)
                    return
                finally:
                    release()
                    self.queue.clear_stop(job.meeting_id)
                    # However the stage ended: a meeting deleted meanwhile goes now.
                    self.finish_delete(job.meeting_id)
                    # Failed, deferred, preempted, deleted; a success is said below, once done.
                    self._announce(job, ended_only=True)
                self._on_success(job, context)
                self._announce(job)
        except BaseException:
            release()  # raised before the stage's own ``try`` was reached
            raise

    def _keep_force(self, job: Job, context: StageContext) -> None:
        """A run asked for by hand that did not finish (a recording took over, the provider
        said not now, an error to retry) is still asked for: the next attempt redoes the
        work too, instead of finding the old output current and skipping. A final failure
        ends the request (``_on_failure`` drops it again)."""
        if context.force and not self.queue.deleting(job.meeting_id):
            self.queue.request_rerun(job.meeting_id, job.stage)

    def _announce(self, job: Job, *, ended_only: bool = False) -> None:
        """A ``job`` event as a stage starts and ends: the lists of what is running
        (the meeting's card, Settings) change at once rather than at their next poll."""
        events = getattr(self.services, "events", None) if self.services is not None else None
        if events is None:
            return
        current = self.queue.get(job.id)
        state = current.state if current is not None else "deleted"
        if ended_only and state == "running":
            return
        events.publish("job", meeting_id=job.meeting_id, stage=job.stage, state=state)

    def _execute_file(self, job: Any) -> None:
        """One file transcription (D86): the engine, not the stage registry. A recording
        stops it only between phases; a cancel or a delete at the next segment."""
        import json

        from app.asr.models import ModelNotInstalled
        from app.audio.ingest import FfmpegBroken, FfmpegFailed, UnsupportedAudio
        from app.transcription.engine import transcribe_file
        from app.transcription.store import RESULT_NAME
        from app.transcription.types import overall_progress

        store = self.transcriptions
        folder = store.folder(job.id)

        def stop_check() -> None:
            store.stop_check(job.id)

        def checkpoint() -> None:
            store.stop_check(job.id)
            if self.should_yield():
                raise Preempted(f"transcription {job.id} yielded to the recorder")

        def on_progress(phase: str, fraction: float) -> None:
            store.progress(job.id, phase, overall_progress(phase, fraction))

        try:
            # Inside the try: an error here escaped to run_forever's catch-all and left
            # the job running until the next restart (the PR #1 review).
            folder.mkdir(parents=True, exist_ok=True)
            result = transcribe_file(
                Path(job.source_path),
                folder,
                config=self.config,
                options=job.parsed_options,
                services=self.services,
                stop_check=stop_check,
                on_progress=on_progress,
                checkpoint=checkpoint,
            )
            payload = result.as_result(id=job.id, source_name=job.source_name)
            (folder / RESULT_NAME).write_text(
                json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except Cancelled as exc:
            log.info("%s", exc)
            store.end_cancelled(job.id)
            return
        except Preempted:
            if store.release(job).state == "cancelled":
                log.info("transcription %s cancelled as a recording preempted it", job.id)
                return
            self.stats.preempted += 1
            log.info("transcription %s preempted by the recorder", job.id)
            return
        except FfmpegFailed as exc:
            # ffmpeg could not start (no memory, on the machine that reported it, for
            # days): nothing to do with this file, and five quick retries would not
            # outlast it. Wait, counting no attempt; the job shows why while it waits, and
            # a cancel asked for meanwhile is honoured by defer.
            why = f"{type(exc).__name__}: {exc}".replace(
                Path(job.source_path).name, job.source_name
            )
            until = self.clock.now() + timedelta(seconds=FFMPEG_DEFER_S)
            if store.defer(job, why, until=until).state == "pending":
                log.warning("transcription %s waits: %s", job.id, exc)
            return
        except FfmpegBroken as exc:
            # ffmpeg can never run here (missing, blocked, the wrong program): trying again
            # changes nothing until Upshot is reinstalled or ffmpeg_path corrected. Not a
            # crash report either: it is this computer's setup, and the message says what to do.
            self._failed_for_good(store, job, exc)
            return
        except (PermanentError, UnsupportedAudio, ModelNotInstalled, FileNotFoundError) as exc:
            # Trying again changes nothing: an undecodable file, a missing model, a path
            # that is not there. UnsupportedAudio is a ValueError, so it is named here.
            self._failed_for_good(store, job, exc)
            return
        except Exception as exc:
            updated = store.fail(job, exc)
            if updated.state == "cancelled":
                log.info("transcription %s cancelled as it failed: %s", job.id, exc)
                return
            self.stats.failed += 1
            log.warning("transcription %s failed (attempt %s): %s", job.id, updated.attempts, exc)
            if updated.state == "failed":
                # Failed for good from something unexpected, as a meeting stage reports
                # (D87). A file that can't be decoded or a missing model is not a bug.
                from app.diagnostics.reporter import report

                report(exc, where="transcription")
            return
        finally:
            # However it ended: a job deleted meanwhile goes now its files are let go.
            store.finish_delete(job.id)
        if store.visible(job.id) is None:
            # Deleted as it finished: a delete pressed after the finally above is owed
            # here, or the row would stay running (its files may still be going).
            store.finish_delete(job.id)
            return
        store.complete(
            job.id,
            duration_s=result.duration_s,
            language=result.language,
            language_conf=result.language_conf,
            model=result.model,
        )
        # A delete asked for after the finally above, as the job was being marked done:
        # it was handed to the worker, so the worker does it now.
        if store.finish_delete(job.id):
            return
        self.stats.completed += 1
        if job.source_kind == "upload" and not bool(
            self.config.get("transcription.keep_input", False)
        ):
            # Through the store, under its lock: a Delete pressed now removes the whole
            # folder, and the two removals must not meet half-way.
            store.remove_input(job.id)

    def _failed_for_good(self, store: Any, job: Any, exc: BaseException) -> None:
        """A file job that trying again would not fix; cancelled instead when a cancel
        was asked for while it ran."""
        if store.fail(job, exc, permanent=True).state == "cancelled":
            log.info("transcription %s cancelled as it failed: %s", job.id, exc)
            return
        self.stats.failed += 1
        log.warning("transcription %s failed: %s", job.id, exc)

    def finish_delete(self, meeting_id: str) -> None:
        """The deletion asked for while a stage ran (``queue.request_delete``), now that
        the stage has let go of the meeting's files. Nothing to do otherwise."""
        if not self.queue.deleting(meeting_id):
            return
        meeting = self.dao.get_meeting(meeting_id)
        meetings = getattr(self.services, "meetings", None) if self.services else None
        try:
            if meeting is not None and meetings is not None:
                meetings.purge(meeting)
            elif meeting is not None:
                self.dao.delete_meeting(meeting_id)
        except (OSError, ValueError) as exc:
            # A file still held (Windows): the marker stays, and the next start finishes it.
            log.warning("meeting %s: could not finish deleting it: %s", meeting_id, exc)
            return
        finally:
            self.queue.deleted(meeting_id)
        if self.services is not None and getattr(self.services, "events", None) is not None:
            self.services.events.publish("meeting", meeting_id=meeting_id, action="deleted")

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
        if self.dao.get_meeting(job.meeting_id) is None:
            return  # deleted as the stage finished: nothing to move on
        self.queue.complete(job)
        self.stats.completed += 1
        # The stage that failed went through after all (a retry, a sign-in): an error
        # window still open for it has nothing left to say.
        from app.diagnostics import notices

        notices.forget_meeting(job.meeting_id, stage=str(job.stage))
        self.last_metrics[job.stage] = dict(context.metrics) if context else {}
        if job.stage == JobStage.SUMMARIZE:
            # A summary written by a provider (in this run: not one that found the notes
            # current and skipped) means it is signed in and has allowance: its next
            # sign-out is news again, and it may be offered on the meeting page.
            metrics = context.metrics if context else {}
            name = str(metrics.get("written_by") or "")
            self._told_signed_out.pop(name, None)
            self._told_fallback.pop(name, None)  # it runs again: its next sign-out is news
            instead_of = str(metrics.get("signed_out_instead_of") or "")
            if instead_of and name:
                self._fallback_took_over(job, instead_of, name)
            status = getattr(self.services, "cli_status", None) if self.services else None
            if status is not None and name:
                status.unspent(name)
        stage = self._stage(job)
        if stage is not None and not (context is not None and context.hold_state):
            from app.pipeline.states import is_legal

            target = STAGE_DONE_STATE[stage]
            meeting = self.dao.require_meeting(job.meeting_id)
            current = MeetingState(meeting.state)
            if current != target and is_legal(current, target):
                self.dao.set_state(job.meeting_id, target)
        if self._transcripts_only(job):
            log.info("meeting %s: transcribed; no AI provider, so no summary", job.meeting_id)
        else:
            self.queue.enqueue_next_stage(job)
        self._merge_pending(job.meeting_id)

    def _merge_pending(self, meeting_id: str) -> None:
        """A later recording of a meeting, waiting for its own transcript to be joined to
        the earlier one's (D89): it may have it now."""
        meetings = getattr(self.services, "meetings", None)
        if meetings is None:
            return
        try:
            meetings.merge_pending(meeting_id)
        except Exception as exc:  # the merge is retried at the next stage; never fatal
            log.warning("meeting %s: merge failed: %s", meeting_id, exc)

    def _transcripts_only(self, job: Job) -> bool:
        """With no AI provider a meeting ends at its transcript (D63), not at a failure.

        A sensitive meeting is the exception: it was always summarized by the local model,
        whatever the provider says, so it still is.
        """
        if job.stage != JobStage.ASSEMBLE or str(self.config.get("llm.provider")) != "none":
            return False
        return not bool(self.dao.require_meeting(job.meeting_id).sensitive)

    def _on_deferred(self, job: Job, exc: Deferred) -> None:
        """Park the job until the provider says it can try again. The meeting stays put.

        A stated reset time is trusted up to a week — the longest cap a plan announces —
        and one that has already passed, or none at all, means the class's default wait.
        """
        now = self.clock.now()
        until = exc.retry_at
        if until is not None and until.tzinfo is None:
            until = until.astimezone()
        if until is None or until <= now:
            until = now + timedelta(seconds=exc.default_wait_s)
        # In the clock's own offset: `not_before` is compared as a string, and a
        # timestamp written in another offset sorts wrongly against every other one.
        until = min(until, now + timedelta(days=7)).astimezone(now.tzinfo)
        if isinstance(exc, QuotaExhausted) and exc.provider:
            status = getattr(self.services, "cli_status", None) if self.services else None
            if status is not None:
                status.spent(exc.provider, until)  # not offered on the page until then
        signed_out = isinstance(exc, SignInRequired) and bool(exc.provider)
        self.queue.defer(
            job, str(exc), until=until, waiting_for=exc.provider if signed_out else None
        )
        log.warning("stage %s deferred until %s: %s", job.stage, until.isoformat(), exc)
        if signed_out:
            self._signed_out(job, exc.provider)

    def _fallback_took_over(self, job: Job, signed_out: str, wrote: str) -> None:
        """The fallback wrote a summary because the usual provider is signed out: say so
        (owner, 2026-11-11), once per sign-out like the sign-out notice itself, so a
        backlog of meetings is one message and not one each."""
        notifier = getattr(self.services, "notifier", None) if self.services else None
        if notifier is None or not hasattr(notifier, "fallback_took_over"):
            return
        now = self.clock.monotonic()
        told = self._told_fallback.get(signed_out)
        if told is not None and now - told < SIGNED_OUT_TOLD_S:
            return
        self._told_fallback[signed_out] = now
        from app.llm.model_choice import PROVIDER_NAMES

        notifier.fallback_took_over(
            job.meeting_id,
            PROVIDER_NAMES.get(signed_out, signed_out),
            PROVIDER_NAMES.get(wrote, wrote),
        )

    def _signed_out(self, job: Job, provider: str) -> None:
        """A CLI turned out signed out: the page must see it now, not at the status
        cache's next refresh, and the user hears it once per sign-out — not once per
        waiting meeting, and not again every fifteen minutes while it waits."""
        services = self.services
        status = getattr(services, "cli_status", None) if services is not None else None
        if status is not None:
            status.forget(provider)
        notifier = getattr(services, "notifier", None) if services is not None else None
        if notifier is None or not hasattr(notifier, "signed_out"):
            return
        now = self.clock.monotonic()
        told = self._told_signed_out.get(provider)
        if told is not None and now - told < SIGNED_OUT_TOLD_S:
            return
        self._told_signed_out[provider] = now
        from app.llm.model_choice import CLI_NAMES

        notifier.signed_out(job.meeting_id, CLI_NAMES.get(provider, provider))

    def _on_failure(
        self, job: Job, exc: BaseException, *, permanent: bool, ran_with: str | None = None
    ) -> None:
        updated = self.queue.fail(job, exc, permanent=permanent)
        self.stats.failed += 1
        log.warning("stage %s failed (attempt %s): %s", job.stage, updated.attempts, exc)
        if updated.state != "failed":
            return
        self.queue.take_rerun(job.meeting_id, job.stage)  # the asked-for run has ended
        if job.stage == JobStage.SUMMARIZE and isinstance(ran_with, str):
            # A provider picked on the meeting page was for this run, and it has ended.
            from app.pipeline.stages.summarize import drop_chosen_provider

            folder = Path(self.dao.require_meeting(job.meeting_id).folder)
            if folder.exists():
                drop_chosen_provider(folder, only=ran_with)
        # Failed for good, not a retry: worth a crash report, if the user agreed (D87).
        if isinstance(exc, Exception):
            from app.diagnostics import notices
            from app.diagnostics.reporter import report

            failed_meeting = self.dao.get_meeting(job.meeting_id)
            describe(
                exc,
                stage=str(job.stage),
                attempt=updated.attempts,
                language=(failed_meeting.language if failed_meeting else None),
                meeting_minutes=_minutes_bucket(
                    failed_meeting.duration_s if failed_meeting else None
                ),
            )

            sent = report(exc, where=f"stage:{job.stage}")
            # And a window of its own for the user, unless the page already explains it.
            notices.record(
                meeting_id=job.meeting_id,
                stage=str(job.stage),
                message=updated.last_error or str(exc),
                at=self.clock.now().isoformat(),
                exc=exc,
                sent=sent,
            )
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
        self.maybe_backup()
        try:
            from app.retention import sweep_services

            return sweep_services(self.services)
        except Exception as exc:  # a housekeeping task must never take the worker down
            log.warning("retention sweep failed: %s", exc)
            return None

    def maybe_backup(self) -> Any:
        """Copy the database once a day (D89): it alone holds the meetings' names."""
        if self.services is None:
            return None
        try:
            from app.db.backup import database_of, maybe_backup

            database = database_of(self.services.conn)
            if database is None:
                return None
            hours = float(self.config.get("backup.every_hours", 24))
            return maybe_backup(database, every_s=hours * 3600)
        except Exception as exc:  # a housekeeping task must never take the worker down
            log.warning("database backup failed: %s", exc)
            return None

    def maybe_finish_deletes(self) -> int:
        """Remove deleted transcriptions whose files were still in use, at most once in
        ``FINISH_DELETES_S`` (z8tj1hwqq1). The first call always runs: after a restart,
        nothing holds them any more."""
        if self.transcriptions is None:
            return 0
        now = self.clock.monotonic()
        if self._last_deletes is not None and now - self._last_deletes < FINISH_DELETES_S:
            return 0
        self._last_deletes = now
        try:
            return int(self.transcriptions.finish_pending_deletes())
        except Exception as exc:  # housekeeping must never take the worker down
            log.warning("finishing deletes failed: %s", exc)
            return 0

    def run_forever(self, *, idle_sleep: float = 2.0, busy_sleep: float = 5.0) -> None:
        while not self.stop_event.is_set():
            if not self.may_run():
                self.clock.sleep(busy_sleep)
                continue
            try:
                ran = self.run_once()
            except Exception as exc:
                # Nothing a job does may end the thread: with it gone, nothing would be
                # transcribed again until a restart (the PR #1 review).
                log.exception("worker: a job failed outside its own error handling")
                from app.diagnostics.reporter import report

                report(exc, where="worker")
                self.clock.sleep(busy_sleep)
                continue
            if not ran:
                # Only with nothing else to do: housekeeping never competes with a job.
                self.maybe_finish_deletes()
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
