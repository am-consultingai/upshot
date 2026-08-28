"""Error taxonomy (TECHNICAL-DESIGN.md §15)."""

from __future__ import annotations


class MeetingAgentError(Exception):
    """Base class for everything this application raises deliberately."""


class RecoverableError(MeetingAgentError):
    """Retry with backoff."""


class PermanentError(MeetingAgentError):
    """Fail the job; surface it in /attention. Never retried."""

    def __init__(self, message: str, *, category: str | None = None) -> None:
        super().__init__(message)
        self.category = category


class Preempted(MeetingAgentError):
    """A stage yielded to a recording. Releases the job without counting an attempt."""


class IllegalTransition(MeetingAgentError):
    """A state transition that is not in LEGAL_TRANSITIONS. Always a bug."""


class ConfigError(MeetingAgentError):
    """Configuration is invalid and the application cannot start."""
