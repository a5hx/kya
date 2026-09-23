"""Tiny in-process pub/sub that feeds the web UI's Server-Sent Events stream."""
from __future__ import annotations

import asyncio
import itertools
import threading
import time
from collections import deque
from typing import Any


class EventBus:
    def __init__(self, history: int = 200):
        self._subs: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._recent: deque[dict[str, Any]] = deque(maxlen=history)
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def publish(self, type_: str, data: dict[str, Any]) -> dict[str, Any]:
        event = {"id": next(self._ids), "type": type_, "ts": time.time(), "data": data}
        with self._lock:
            self._recent.append(event)
            subs = list(self._subs)
        for loop, queue in subs:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:  # loop closed
                pass
        return event

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._subs.append((asyncio.get_running_loop(), queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subs = [(l, q) for l, q in self._subs if q is not queue]

    def recent(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._recent)


class NullBus(EventBus):
    def publish(self, type_: str, data: dict[str, Any]) -> dict[str, Any]:
        return {"type": type_, "data": data}
