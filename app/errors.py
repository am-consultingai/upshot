"""Error taxonomy (TECHNICAL-DESIGN.md §15)."""

from __future__ import annotations

from datetime import datetime


class UpshotError(Exception):
    """Base class for everything this application raises deliberately."""


class RecoverableError(UpshotError):
    """Retry with backoff."""


class Deferred(RecoverableError):
    """Not now, and retrying in a few seconds cannot change that. Back to the queue.

    The ordinary backoff is built for a blip — 2, 4, 8, 16 seconds, then the job fails.
    A subscription whose allowance is spent for four hours, or a CLI nobody has signed
    into yet, is not a blip: five quick retries would fail the meeting for a reason that
    fixes itself (or is fixed in Settings) later. So the worker parks the job until
    ``retry_at`` without counting an attempt, and the message stays on the job where the
    meeting page and ``/api/attention`` read it. The audio and the transcript are already
    safe on disk; only the summary is waiting.
    """

    #: How long to park the job when the provider did not say when to come back.
    default_wait_s = 3600.0

    def __init__(
        self, message: str, *, provider: str = "", retry_at: datetime | None = None
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.retry_at = retry_at


class QuotaExhausted(Deferred):
    """A subscription's allowance is used up: the plan's limit, not a transient 429.

    The one failure ``llm.fallback_provider`` answers to. Anything else — a timeout, a
    malformed answer, a signed-out CLI — never sends the transcript to a second provider,
    because a meeting quietly leaving the machine for a provider the user did not pick is
    exactly the surprise this application exists to avoid.
    """


class SignInRequired(Deferred):
    """The CLI is installed but nobody is signed in. A Settings problem, not a failed job.

    Waits a quarter of an hour at a time rather than an hour: signing in takes a minute,
    and the summary should follow soon after without anyone pressing Retry.
    """

    default_wait_s = 900.0


class PermanentError(UpshotError):
    """Fail the job; surface it in /attention. Never retried."""

    def __init__(self, message: str, *, category: str | None = None) -> None:
        super().__init__(message)
        self.category = category


class Preempted(UpshotError):
    """A stage yielded to a recording. Releases the job without counting an attempt."""


class IllegalTransition(UpshotError):
    """A state transition that is not in LEGAL_TRANSITIONS. Always a bug."""


class ConfigError(UpshotError):
    """Configuration is invalid and the application cannot start."""
