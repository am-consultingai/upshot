"""The pre-roll ring (DETECTION.md §4, TECHNICAL-DESIGN.md §4.6).

Post-resample int16 frames only, bounded at ``preroll_s`` per track: 1.9 MB each at
16 kHz. It never touches disk before commit — that is what makes speculative Tier-1
capture free.
"""

from __future__ import annotations

from collections import deque
from typing import Protocol

import numpy as np


class PcmSink(Protocol):
    def write_pcm(self, track: str, samples: np.ndarray) -> None: ...


class PreRollRing:
    def __init__(self, seconds: float = 60.0, rate: int = 16000) -> None:
        self.seconds = seconds
        self.rate = rate
        self.capacity = int(seconds * rate)
        self._blocks: deque[np.ndarray] = deque()
        self._length = 0
        self.dropped = 0

    def __len__(self) -> int:
        return self._length

    @property
    def duration_ms(self) -> int:
        return round(self._length * 1000 / self.rate)

    def push(self, samples: np.ndarray) -> None:
        samples = np.asarray(samples, dtype=np.int16)
        if len(samples) == 0:
            return
        if len(samples) >= self.capacity:
            self.dropped += self._length + len(samples) - self.capacity
            self._blocks.clear()
            self._blocks.append(samples[-self.capacity :])
            self._length = self.capacity
            return
        self._blocks.append(samples)
        self._length += len(samples)
        while self._length > self.capacity:
            oldest = self._blocks[0]
            excess = self._length - self.capacity
            if len(oldest) <= excess:
                self._blocks.popleft()
                self._length -= len(oldest)
                self.dropped += len(oldest)
            else:
                self._blocks[0] = oldest[excess:]
                self._length -= excess
                self.dropped += excess

    def peek(self) -> np.ndarray:
        if not self._blocks:
            return np.zeros(0, dtype=np.int16)
        return np.concatenate(list(self._blocks))

    def flush_to(self, writer: PcmSink, track: str) -> int:
        """Commit: the ring becomes the head of chunk 0001."""
        samples = self.peek()
        self.drop()
        if len(samples) == 0:
            return 0
        writer.write_pcm(track, samples)
        return len(samples)

    def drop(self) -> None:
        self._blocks.clear()
        self._length = 0
