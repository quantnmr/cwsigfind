"""In-memory ring buffer of recent spots, plus dedup tracking."""

from __future__ import annotations

from collections import OrderedDict, deque
from threading import Lock

from .spot import Spot


class SpotStore:
    """Bounded ring buffer of recent spots with a separate dedup-key LRU.

    `add` returns True the first time it sees a given `Spot.dedup_key()`, and
    False on subsequent duplicates. The web layer reads `recent()` to populate
    the initial table when a client connects.
    """

    def __init__(self, max_spots: int = 2000, dedup_window: int = 5000) -> None:
        self._spots: deque[Spot] = deque(maxlen=max_spots)
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._dedup_window = dedup_window
        self._lock = Lock()

    def add(self, spot: Spot) -> bool:
        key = spot.dedup_key()
        with self._lock:
            if key in self._seen:
                self._seen.move_to_end(key)
                return False
            self._seen[key] = None
            while len(self._seen) > self._dedup_window:
                self._seen.popitem(last=False)
            self._spots.append(spot)
            return True

    def recent(self, n: int = 200) -> list[Spot]:
        with self._lock:
            spots = list(self._spots)
        # Order strictly by spot time (newest first), not by insertion order.
        # Each source dumps a batch when it polls, so insertion-order would
        # group POTA-then-SOTA-then-DX instead of interleaving by time.
        # Python's list.sort is stable, so spots with equal timestamps keep
        # their relative insertion order.
        spots.sort(key=lambda s: s.spotted_at, reverse=True)
        return spots[:n]
