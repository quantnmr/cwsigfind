"""Async pub/sub bus: sources publish, the web layer (and future rig layer) subscribe."""

from __future__ import annotations

import asyncio
import logging

from .spot import Spot

log = logging.getLogger(__name__)


class SpotBus:
    """Fan-out async pub/sub for Spot objects.

    Each subscriber gets its own bounded queue. If a subscriber is slow and its
    queue fills, we drop the oldest item to make room — better to lose one spot
    than wedge the whole pipeline.
    """

    def __init__(self, queue_size: int = 2000) -> None:
        self._queue_size = queue_size
        self._subs: list[asyncio.Queue[Spot]] = []
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[Spot]:
        q: asyncio.Queue[Spot] = asyncio.Queue(maxsize=self._queue_size)
        async with self._lock:
            self._subs.append(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue[Spot]) -> None:
        async with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    async def publish(self, spot: Spot) -> None:
        async with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(spot)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    q.put_nowait(spot)
                except Exception:
                    log.debug("Dropping spot for slow subscriber")
