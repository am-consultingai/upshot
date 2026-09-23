"""Logging (TECHNICAL-DESIGN.md §15): rotating app log + a per-meeting pipeline.log."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

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


class DropClientDisconnects(logging.Filter):
    """A browser going away is not an error.

    Closing a tab cancels the SSE task and resets the socket; uvicorn and asyncio both
    log that with a full traceback. Since `log_config=None` routes their loggers here so
    that *real* tracebacks reach the file, these have to be filtered out by hand — a log
    full of routine disconnects is one nobody reads.
    """

    BENIGN = (asyncio.CancelledError, ConnectionResetError, ConnectionAbortedError, BrokenPipeError)
    QUIET = ("timeout graceful shutdown exceeded",)

    def filter(self, record: logging.LogRecord) -> bool:
        exception = record.exc_info[1] if record.exc_info else None
        if isinstance(exception, self.BENIGN):
            return False
        return not any(marker in record.getMessage() for marker in self.QUIET)


def setup(level: int = logging.INFO, *, to_file: bool = True) -> None:
    global _configured
    root = logging.getLogger()
    root.setLevel(level)
    if _configured:
        return
    filt = _MeetingIdFilter()
    quiet = DropClientDisconnects()
    import sys

    # The frozen build is windowed: it has no stderr, and a handler on None fails every
    # record in silence.
    if sys.stderr is not None:
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter(FORMAT))
        stream.addFilter(filt)
        stream.addFilter(quiet)
        root.addHandler(stream)
    if to_file:
        d = paths.log_dir()
        d.mkdir(parents=True, exist_ok=True)
        # UTF-8, not the Windows code page: every line carries the meeting id, whose slug
        # holds the title, so in cp1252 each line of a Hebrew-titled meeting was dropped.
        fh = RotatingFileHandler(
            d / "app.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        fh.setFormatter(logging.Formatter(FORMAT))
        fh.addFilter(filt)
        fh.addFilter(quiet)
        root.addHandler(fh)
        _install_excepthooks()
    _configured = True


def _install_excepthooks() -> None:
    """Send crashes to the log file too. A traceback that exists only on a console that
    has since been closed is a traceback nobody can read."""
    import sys
    import threading

    root = logging.getLogger()

    def on_exception(kind: type[BaseException], value: BaseException, tb: Any) -> None:
        if issubclass(kind, KeyboardInterrupt):
            sys.__excepthook__(kind, value, tb)
            return
        root.critical("uncaught exception", exc_info=(kind, value, tb))

    def on_thread_exception(args: Any) -> None:
        if issubclass(args.exc_type, SystemExit):
            return
        root.critical(
            "uncaught exception in thread %s",
            args.thread.name if args.thread else "?",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = on_exception
    threading.excepthook = on_thread_exception


def meeting_log_handler(folder: Path) -> logging.Handler:
    """A handler writing this meeting's own pipeline.log."""
    folder.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(folder / "pipeline.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter(FORMAT))
    handler.addFilter(_MeetingIdFilter())
    return handler


def get(name: str) -> logging.Logger:
    return logging.getLogger(name)
