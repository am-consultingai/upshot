"""What each CLI said about itself, held for a few minutes.

Asking means running the CLI: ``claude auth status``, ``codex login status``, ``agy
--version`` and ``agy models``. Every refetch of the page's provider status did that, and
the calendar's sync refetched it once a minute, so the three CLIs ran all day. One of
them (``agy``) now and then starts a detached updater of its own, which opens a console
window the user sees flash (machine A, 2026-10-13).

Nothing here runs on a timer: an answer is reused while it is younger than ``ttl_s``,
and asked again only when someone wants the status after that. Sign-in, sign-out,
install and update forget a provider's answer at once, and the page asks for a fresh one
while it watches a sign-in (``fresh``).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

#: Long enough that no refetch storm runs the CLIs, short enough that a sign-in made in
#: the user's own terminal shows without a restart.
TTL_S = 300.0


class CliStatusCache:
    def __init__(self, ttl_s: float = TTL_S, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl_s = ttl_s
        self.clock = clock
        #: (provider, the program it runs) → (when asked, the answer).
        self._held: dict[tuple[str, str], tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(
        self,
        provider: str,
        probe: Callable[[], Any],
        *,
        program: str = "",
        fresh: bool = False,
    ) -> Any:
        """``provider``'s status: the one held, or ``probe()``'s when there is none, it is
        older than ``ttl_s``, or ``fresh`` asks for a new one. ``program`` is the CLI the
        settings point at: pointed at another, the provider is asked again."""
        key = (provider, program)
        now = self.clock()
        if not fresh:
            with self._lock:
                held = self._held.get(key)
            if held is not None and now - held[0] < self.ttl_s:
                return held[1]
        status = probe()
        with self._lock:
            self._held[key] = (self.clock(), status)
        return status

    def forget(self, *providers: str) -> None:
        """Drop what is held for ``providers``, or for all of them when none is named."""
        with self._lock:
            if not providers:
                self._held.clear()
            for key in [key for key in self._held if key[0] in providers]:
                del self._held[key]
