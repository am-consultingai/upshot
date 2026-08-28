"""The SSE event bus: recorder state, job transitions, toasts.

One-way server→client, which is why it is SSE and not a WebSocket — and SSE reconnects
itself.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from app.log import get

log = get(__name__)

HISTORY = 200


@dataclass
class Event:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    id: int = 0

    def sse(self) -> str:
        body = json.dumps({"type": self.type, **self.payload}, ensure_ascii=False)
        return f"id: {self.id}\nevent: {self.type}\ndata: {body}\n\n"


class EventBus:
    """Thread-safe publish (the worker and the recorder are threads), async subscribe."""

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[Event]] = []
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._counter = 0
        self.history: deque[Event] = deque(maxlen=HISTORY)

    def bind_loop(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._loop = loop or asyncio.get_running_loop()

    def publish(self, type: str, **payload: Any) -> Event:
        with self._lock:
            self._counter += 1
            event = Event(type=type, payload=payload, id=self._counter)
            self.history.append(event)
            subscribers = list(self._subscribers)
            loop = self._loop
        for queue in subscribers:
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(self._offer, queue, event)
            else:
                self._offer(queue, event)
        return event

    @staticmethod
    def _offer(queue: asyncio.Queue[Event], event: Event) -> None:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:  # pragma: no cover - a stalled browser tab
            log.warning("dropping event for a subscriber that is not reading")

    def subscribe(self) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=256)
        with self._lock:
            self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        with self._lock:
            if queue in self._subscribers:
                self._subscribers.remove(queue)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def replay(self, after_id: int = 0) -> Iterator[Event]:
        for event in list(self.history):
            if event.id > after_id:
                yield event
