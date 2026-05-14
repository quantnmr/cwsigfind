from datetime import datetime, timedelta, timezone

from cwsigfind.spot import Spot
from cwsigfind.store import SpotStore


def _spot(spot_id, spotted_at=None):
    return Spot(
        source="POTA",
        callsign="W1ABC",
        frequency_khz=14025.0,
        mode="CW",
        source_id=str(spot_id),
        spotted_at=spotted_at or datetime.now(timezone.utc),
    )


def test_store_dedups_by_key():
    store = SpotStore()
    assert store.add(_spot(1)) is True
    assert store.add(_spot(1)) is False
    assert store.add(_spot(2)) is True


def test_store_keeps_recent_in_reverse_order():
    store = SpotStore()
    for i in range(5):
        store.add(_spot(i))
    recent = store.recent(10)
    assert [s.source_id for s in recent] == ["4", "3", "2", "1", "0"]


def test_store_caps_ring_buffer():
    store = SpotStore(max_spots=3)
    for i in range(10):
        store.add(_spot(i))
    recent = store.recent(100)
    assert len(recent) == 3
    assert recent[0].source_id == "9"


def test_store_recent_sorts_by_spot_time_not_insertion_order():
    """When two sources dump batches in different orders, recent() should
    interleave them by spot time so the UI doesn't render as
    'all-POTA-then-all-SOTA'."""
    store = SpotStore()
    t0 = datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)
    # Insert a POTA batch (oldest), then a SOTA batch that is actually newer.
    pota_old = _spot("p1", spotted_at=t0)
    pota_mid = _spot("p2", spotted_at=t0 + timedelta(minutes=2))
    sota_newest = _spot("s1", spotted_at=t0 + timedelta(minutes=5))
    sota_middle = _spot("s2", spotted_at=t0 + timedelta(minutes=3))

    # Insert in source-batch order: POTA first, then SOTA.
    store.add(pota_old)
    store.add(pota_mid)
    store.add(sota_newest)
    store.add(sota_middle)

    recent = store.recent(10)
    times = [s.spotted_at for s in recent]
    # Strictly descending by time, regardless of which source they came from.
    assert times == sorted(times, reverse=True)
    assert [s.source_id for s in recent] == ["s1", "s2", "p2", "p1"]
