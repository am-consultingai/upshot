"""Error taxonomy (TECHNICAL-DESIGN.md §15)."""

from __future__ import annotations


class UpshotError(Exception):
    """Base class for everything this application raises deliberately."""


class RecoverableError(UpshotError):
    """Retry with backoff."""


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
