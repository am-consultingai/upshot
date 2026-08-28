"""Stage idempotency: every stage checks for its own output before doing work."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path


def up_to_date(output: Path, inputs: Sequence[Path]) -> bool:
    """True when ``output`` exists and is newer than every existing input."""
    if not output.exists() or output.stat().st_size == 0:
        return False
    out_mtime = output.stat().st_mtime_ns
    return all(not (source.exists() and source.stat().st_mtime_ns > out_mtime) for source in inputs)
