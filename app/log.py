"""Logging (TECHNICAL-DESIGN.md §15): rotating app log + a per-meeting pipeline.log."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app import paths

_meeting_id: ContextVar[str] = ContextVar("meeting_id", default="-")

FORMAT = "%(asctime)s %(levelname)-7s [%(meeting_id)s] %(name)s: %(message)s"


class _MeetingIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.meeting_id = _meeting_id.get()
        return True


@contextmanager
def meeting_context(meeting_id: str) -> Iterator[None]:
    token = _meeting_id.set(meeting_id)
    try:
        yield
    finally:
        _meeting_id.reset(token)


def current_meeting_id() -> str:
    return _meeting_id.get()


_configured = False


def setup(level: int = logging.INFO, *, to_file: bool = True) -> None:
    global _configured
    root = logging.getLogger()
    root.setLevel(level)
    if _configured:
        return
    filt = _MeetingIdFilter()
    stream = logging.StreamHandler()
    stream.setFormatter(logging.Formatter(FORMAT))
    stream.addFilter(filt)
    root.addHandler(stream)
    if to_file:
        d = paths.log_dir()
        d.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(d / "app.log", maxBytes=10 * 1024 * 1024, backupCount=5)
        fh.setFormatter(logging.Formatter(FORMAT))
        fh.addFilter(filt)
        root.addHandler(fh)
    _configured = True


def meeting_log_handler(folder: Path) -> logging.Handler:
    """A handler writing this meeting's own pipeline.log."""
    folder.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(folder / "pipeline.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter(FORMAT))
    handler.addFilter(_MeetingIdFilter())
    return handler


def get(name: str) -> logging.Logger:
    return logging.getLogger(name)
