"""Crashes Python never sees, reported at the next start (D87, C4).

A native library failing (ctranslate2, onnxruntime) or the memory running out ends the
process without an exception, so no excepthook runs. Two files catch it instead:

- ``running.json``: written by ``arm`` at start, removed by ``disarm`` on a clean quit.
- ``fault-<pid>.log``: Python's ``faulthandler`` writes every thread's stack there on a
  fatal error.

At the next start, ``found`` reads what a run that did not end cleanly left. Only a run
whose fault log has a stack in it is reported: a marker alone is also what a Windows
shutdown or a forced kill leaves (the installer's last resort), and those are not
crashes. The stack is reduced to module and function names and line numbers
(``scrub.frames_from_fault_dump``); the file, with its paths, never leaves.
"""

from __future__ import annotations

import contextlib
import faulthandler
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from app.diagnostics import scrub
from app.log import get

log = get(__name__)

_fault_file: IO[str] | None = None


def _folder(home: Path) -> Path:
    return home / "diagnostics"


def found(home: Path, *, own_pid: int | None = None) -> dict[str, Any] | None:
    """What an earlier run that crashed left behind, cleared once read; None otherwise."""
    folder = _folder(home)
    marker = folder / "running.json"
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    own_pid = os.getpid() if own_pid is None else own_pid
    result: dict[str, Any] | None = None
    if isinstance(data, dict) and data.get("pid") != own_pid:
        fault = folder / f"fault-{data.get('pid')}.log"
        try:
            dump = fault.read_text(encoding="utf-8", errors="replace")
        except OSError:
            dump = ""
        if dump.strip():
            result = {"version": data.get("version"), "frames": scrub.frames_from_fault_dump(dump)}
            log.warning("the run started %s ended in a crash", data.get("started"))
        else:
            log.info(
                "the run started %s did not quit cleanly (no crash recorded)", data.get("started")
            )
    for path in folder.glob("fault-*.log") if folder.is_dir() else ():
        if path.name != f"fault-{own_pid}.log":
            path.unlink(missing_ok=True)
    marker.unlink(missing_ok=True)
    return result


def arm(home: Path, *, version: str) -> None:
    """Mark this run as started and have faulthandler write here if it dies."""
    global _fault_file
    folder = _folder(home)
    folder.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    marker = {"pid": pid, "version": version, "started": datetime.now(UTC).isoformat()}
    (folder / "running.json").write_text(json.dumps(marker), encoding="utf-8")
    try:
        _fault_file = (folder / f"fault-{pid}.log").open("w", encoding="utf-8")
        faulthandler.enable(file=_fault_file, all_threads=True)
    except (OSError, RuntimeError) as exc:
        log.info("faulthandler not enabled: %s", exc)


def disarm(home: Path) -> None:
    """A clean quit: nothing to report next time."""
    global _fault_file
    with contextlib.suppress(RuntimeError):
        faulthandler.disable()
    if _fault_file is not None:
        _fault_file.close()
        _fault_file = None
    folder = _folder(home)
    (folder / "running.json").unlink(missing_ok=True)
    (folder / f"fault-{os.getpid()}.log").unlink(missing_ok=True)
