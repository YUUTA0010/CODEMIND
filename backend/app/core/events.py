"""In-process event bus used for live job progress (WebSocket fan-out). Thread-safe publish; asyncio subscribers.

Event mechanism documented in docs/api_reference.md: clients connect to /api/v1/ws/events and receive JSON objects
shaped like {"type": "index.progress" | "index.done" | "experiment.progress" | "verify.progress" | ..., "data": {...}}.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._subs: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._lock = threading.Lock()
        self.history: list[dict] = []  # small ring buffer so late subscribers/pollers can see recent events

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        with self._lock:
            self._subs.append((asyncio.get_running_loop(), q))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs = [(l, s) for (l, s) in self._subs if s is not q]

    def publish(self, type_: str, data: dict[str, Any]) -> None:
        event = {"type": type_, "data": data}
        with self._lock:
            self.history.append(event)
            del self.history[:-200]
            subs = list(self._subs)
        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(self._put, q, event)
            except RuntimeError:  # loop closed
                pass

    @staticmethod
    def _put(q: asyncio.Queue, event: dict) -> None:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:  # drop for slow consumers rather than blocking workers
            pass


bus = EventBus()
