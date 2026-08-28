"""WASAPI capture over PyAudioWPatch. Implemented in Phase 4 (the spike).

Kept importable everywhere: PyAudioWPatch is a Windows-only wheel, so the import lives
inside :meth:`WasapiCapture.start`.
"""

from __future__ import annotations

import queue

from app.audio.capture import AudioFormat, CaptureStats


class WasapiCapture:
    """Placeholder wired by :mod:`app.audio.factory`; filled in by Phase 4."""

    def __init__(self, track: str = "them", *, queue_seconds: float = 10.0) -> None:
        self.track = track
        self.format = AudioFormat()
        self.stats = CaptureStats()
        self.frames: queue.Queue[bytes] = queue.Queue()
        self.queue_seconds = queue_seconds

    def start(self) -> None:
        raise NotImplementedError("WasapiCapture lands in Phase 4")

    def stop(self) -> None:
        return None

    def read(self, timeout: float = 0.1) -> bytes | None:
        return None
