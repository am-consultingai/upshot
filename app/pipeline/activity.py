"""Is the machine free to do expensive work? (DESIGN.md §5 job policies)"""

from __future__ import annotations

from typing import Protocol


class SystemActivity(Protocol):
    """Everything the ``when_idle`` policy needs to know."""

    def is_busy(self) -> bool: ...


class PsutilActivity:
    """Real signal: CPU load. Selected whenever a fake is not configured."""

    def __init__(self, cpu_threshold: float = 60.0) -> None:
        self.cpu_threshold = cpu_threshold

    def is_busy(self) -> bool:
        try:
            import psutil
        except Exception:  # pragma: no cover - psutil is a hard dependency
            return False
        return bool(psutil.cpu_percent(interval=0.1) > self.cpu_threshold)


class FakeActivity:
    """Test double. ``busy`` is flipped by the test, never patched into the worker."""

    def __init__(self, busy: bool = False) -> None:
        self.busy = busy

    def is_busy(self) -> bool:
        return self.busy


class RecorderState(Protocol):
    def is_active(self) -> bool: ...


class FakeRecorderState:
    def __init__(self, active: bool = False) -> None:
        self.active = active

    def is_active(self) -> bool:
        return self.active
