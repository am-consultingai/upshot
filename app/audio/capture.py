"""The capture seam.

Everything above this protocol is unaffected by which Windows audio API is underneath —
which is the point: the Phase 4 fallback ladder changes only ``wasapi.py``.
"""

from __future__ import annotations

import queue
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

TRACKS = ("me", "them")


@dataclass(frozen=True)
class AudioFormat:
    rate: int = 48000
    channels: int = 2
    dtype: str = "float32"

    @property
    def bytes_per_frame(self) -> int:
        width = {"float32": 4, "int16": 2}[self.dtype]
        return width * self.channels


@dataclass
class CaptureStats:
    frames: int = 0
    xruns: int = 0
    dropped: int = 0
    blocks: int = 0
    started_mono: float | None = None
    errors: list[str] = field(default_factory=list)


@runtime_checkable
class AudioCapture(Protocol):
    """A running stream handing raw device-format bytes to the writer thread."""

    track: str
    format: AudioFormat
    stats: CaptureStats
    frames: queue.Queue[bytes]

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def read(self, timeout: float = 0.1) -> bytes | None: ...


class StreamError(RuntimeError):
    """The device went away mid-meeting. Recoverable: reopen and note a gap."""
