"""Verify the NCDXF/IARU beacon schedule against known reference points.

The canonical schedule (https://www.ncdxf.org/beacon/beaconschedule.html) is
deterministic. At minute mm:ss of any UTC hour, modulo the 3-minute cycle:

  00:00 — 4U1UN on 14.100, then YV5B on 18.110 (started 10s ago last cycle),
           OA4B on 21.150 (started 20s ago), LU4AA on 24.930, CS3B on 28.200.
  00:10 — VE8AT on 14.100, 4U1UN on 18.110, YV5B on 21.150, OA4B on 24.930,
           LU4AA on 28.200.
  00:50 — VK6RBP on 14.100 (per the official table).
"""

from datetime import datetime, timezone

from cwsigfind.beacons import BAND_NAMES, BEACON_LOCATIONS, BEACONS, current_beacon


def _at(seconds: int) -> list:
    """Cycle starts at the top of the minute when minute is a multiple of 3."""
    t = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp() + seconds
    return current_beacon(datetime.fromtimestamp(t, tz=timezone.utc))


def _by_band(beacons) -> dict[str, str]:
    return {b.band: b.callsign for b in beacons}


def test_cycle_start_at_t0():
    # Reference: official NCDXF table, "00:00" row by column.
    res = _by_band(_at(0))
    assert res["20m"] == "4U1UN"   # 14.100 column row 4U1UN
    assert res["17m"] == "YV5B"    # 18.110 column row YV5B reads 00:00
    assert res["15m"] == "OA4B"    # 21.150 column row OA4B reads 00:00
    assert res["12m"] == "LU4AA"   # 24.930 column row LU4AA reads 00:00
    assert res["10m"] == "CS3B"    # 28.200 column row CS3B reads 00:00


def test_slot_one_at_t10():
    res = _by_band(_at(10))
    assert res["20m"] == "VE8AT"   # VE8AT row says 14.100 at 00:10
    assert res["17m"] == "4U1UN"   # 4U1UN moved up from 20m to 17m
    assert res["15m"] == "YV5B"
    assert res["12m"] == "OA4B"
    assert res["10m"] == "LU4AA"


def test_slot_five_at_t50_vk6rbp_on_20m():
    # The VK6RBP row in the official table reads 00:50 on 14.100.
    res = _by_band(_at(50))
    assert res["20m"] == "VK6RBP"


def test_cycle_wraps_at_180s():
    # Second 180 is the start of the next cycle — same beacons as second 0.
    a = _by_band(_at(0))
    b = _by_band(_at(180))
    assert a == b


def test_each_band_has_exactly_one_beacon():
    for s in (0, 7, 13, 90, 179):
        beacons = _at(s)
        assert len(beacons) == len(BAND_NAMES)
        # Each entry should map to a known callsign.
        for b in beacons:
            assert b.callsign in BEACONS
            assert b.band in BAND_NAMES
            assert 1 <= b.slot_seconds_remaining <= 10


def test_slot_remaining_decreases_through_slot():
    # Within a 10-second slot, remaining should be 10 at the boundary and
    # smaller as time advances. (1..10 inclusive — clamped at the edges.)
    r0 = _at(0)[0].slot_seconds_remaining
    r4 = _at(4)[0].slot_seconds_remaining
    r9 = _at(9)[0].slot_seconds_remaining
    assert r0 >= r4 >= r9
    assert r9 >= 1


def test_beacon_locations_match_callsigns():
    """Every beacon has a location entry, and a few well-known ones are correct.

    The NCDXF table is canonical; this test guards against accidental drift
    if anyone edits the BEACONS tuple without updating BEACON_LOCATIONS.
    """
    assert len(BEACONS) == len(BEACON_LOCATIONS)
    mapping = dict(zip(BEACONS, BEACON_LOCATIONS))
    assert mapping["4U1UN"] == "United Nations"
    assert mapping["W6WX"] == "USA"
    assert mapping["KH6RS"] == "Hawaii"
    assert mapping["ZS6DN"] == "South Africa"
    assert mapping["OH2B"] == "Finland"
    assert mapping["VK6RBP"] == "Australia"
    assert mapping["CS3B"] == "Madeira"
    # No empty / placeholder entries crept in.
    for loc in BEACON_LOCATIONS:
        assert loc and loc.strip(), f"Empty BEACON_LOCATIONS entry"


def test_current_beacon_carries_location():
    """The location field flows through current_beacon to each BeaconTx."""
    res = _at(0)
    by_call = {b.callsign: b.location for b in res}
    # 4U1UN is on 20m at t=0 per the canonical schedule.
    assert by_call["4U1UN"] == "United Nations"
    # CS3B is on 10m at t=0.
    assert by_call["CS3B"] == "Madeira"
