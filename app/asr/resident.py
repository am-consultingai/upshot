"""Whisper models kept on the GPU between back-to-back jobs (D108, z8tj1hwjvw).

Measured on Windows with CTranslate2 4.8.1 on CUDA (2026-11-09): every Whisper model that
runs once on the GPU leaves 8 to 9 MB of host memory behind when it is freed. A model
loaded and freed without running leaves nothing, and one model run eight times leaves
nothing. A file job loaded two (the classifier, then the transcriber), so a program
sending 90-second clips grew Upshot by about 17 MB a job: about 10 GB after 600 jobs,
when Windows could no longer start ffmpeg.

So a job hands its GPU models back here instead of freeing them, and the next job takes
them. They are freed when no job has taken them for ``asr.keep_loaded_s``, when a model
that would not fit beside them is about to load, at the end of a meeting's transcription
and before a local LLM answers (ASR and the LLM are never resident together, DESIGN.md
§20.4). The CPU path is unchanged: there the weights are in RAM, which the 12 GB minimum
(D66) budgets for one model at a time.
"""

from __future__ import annotations

import gc
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from app.config import Config
from app.log import get

log = get(__name__)

#: How long a model nobody has taken stays loaded, when the config does not say.
KEEP_LOADED_S = 300.0


@dataclass(frozen=True)
class Key:
    """One loaded model: its role (for the room it needs), its files and its precision."""

    role: str
    reference: str
    compute_type: str


class Timer(Protocol):
    def start(self) -> None: ...
    def cancel(self) -> None: ...


TimerFactory = Callable[[float, Callable[[], None]], Timer]


def _thread_timer(seconds: float, action: Callable[[], None]) -> Timer:
    timer = threading.Timer(seconds, action)
    timer.daemon = True
    timer.name = "asr-keep-loaded"
    return timer


def keep_loaded_s(config: Config) -> float:
    """``asr.keep_loaded_s``; 0 or less frees every model as soon as its job ends."""
    try:
        return float(config.get("asr.keep_loaded_s", KEEP_LOADED_S))
    except (TypeError, ValueError):
        return KEEP_LOADED_S


class Resident:
    """The models no job is using right now. A model is in here or in one job, never both."""

    def __init__(self, *, timer: TimerFactory = _thread_timer) -> None:
        self._lock = threading.Lock()
        self._held: dict[Key, Any] = {}
        self._make_timer = timer
        self._timer: Timer | None = None
        #: Bumped whenever the set changes hands, so a timer armed earlier does nothing.
        self._generation = 0
        self.reuses = 0

    def held(self) -> list[Key]:
        with self._lock:
            return list(self._held)

    def take(self, key: Key) -> Any | None:
        """The loaded model for ``key``, now the caller's; None when none is held."""
        with self._lock:
            model = self._held.pop(key, None)
            if model is not None:
                self.reuses += 1
            return model

    def make_room(
        self, role: str, vram_mb: Callable[[], int | None], need_mb: Mapping[str, int]
    ) -> None:
        """Before a model for ``role`` loads on the GPU: free what is held unless all of it
        fits beside the new one, by each role's ``need_mb`` (``MIN_VRAM_MB``). A card whose
        memory cannot be read frees everything."""
        with self._lock:
            if not self._held:
                return
            total = need_mb.get(role, 0) + sum(need_mb.get(key.role, 0) for key in self._held)
            memory = vram_mb()
            if memory is not None and total <= memory:
                return
            dropped = self._clear_locked()
        log.info("asr: freed %d loaded model(s) to make room for %s", len(dropped), role)
        _free(dropped)

    def park(self, key: Key, model: Any, *, idle_s: float) -> None:
        """Keep ``model`` for the next job, for ``idle_s`` seconds at most."""
        if idle_s <= 0:
            _free([model])
            return
        with self._lock:
            replaced = self._held.pop(key, None)
            self._held[key] = model
            self._arm_locked(idle_s)
        if replaced is not None:
            _free([replaced])

    def drop_all(self) -> int:
        """Free every held model now. How many there were."""
        with self._lock:
            dropped = self._clear_locked()
        count = len(dropped)
        _free(dropped)
        return count

    def _clear_locked(self) -> list[Any]:
        models = list(self._held.values())
        self._held.clear()
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._generation += 1
        return models

    def _arm_locked(self, idle_s: float) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._generation += 1
        generation = self._generation
        self._timer = self._make_timer(idle_s, lambda: self._expire(generation))
        self._timer.start()

    def _expire(self, generation: int) -> None:
        with self._lock:
            if generation != self._generation:
                return
            self._timer = None
            dropped = self._clear_locked()
        if dropped:
            log.info("asr: freed %d model(s) nobody used for a while", len(dropped))
        _free(dropped)


def _free(models: list[Any]) -> None:
    if not models:
        return
    models.clear()
    gc.collect()


#: The process's one set: there is one GPU and one worker.
RESIDENT = Resident()
