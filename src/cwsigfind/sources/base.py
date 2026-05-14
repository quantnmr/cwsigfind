"""Base class for spot sources with crash-restart-with-backoff supervision."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod

from ..bus import SpotBus
from ..store import SpotStore

log = logging.getLogger(__name__)


class SpotSource(ABC):
    """A long-running task that publishes Spots to the bus.

    Subclasses implement `run`, which should loop until cancelled. The
    supervisor wrapper restarts `run` on exceptions with exponential backoff so
    transient network failures don't take down the daemon.
    """

    name: str = "base"

    def __init__(self, bus: SpotBus, store: SpotStore) -> None:
        self.bus = bus
        self.store = store
        self._task: asyncio.Task[None] | None = None

    @abstractmethod
    async def run(self) -> None:
        """Long-running coroutine. Should not return under normal operation."""

    def start(self) -> asyncio.Task[None]:
        async def supervisor() -> None:
            backoff = 1.0
            while True:
                try:
                    await self.run()
                    # Source returned cleanly — unusual, restart anyway.
                    log.info("Source %s returned, restarting", self.name)
                    backoff = 1.0
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception(
                        "Source %s crashed; retrying in %.1fs", self.name, backoff
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2.0, 60.0)

        self._task = asyncio.create_task(supervisor(), name=f"source-{self.name}")
        return self._task

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):
            pass
