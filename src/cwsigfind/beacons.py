"""NCDXF / IARU International Beacon Project schedule.

Eighteen beacons take turns transmitting on five HF bands (14.100, 18.110,
21.150, 24.930, 28.200 MHz). Each beacon transmits for 10 seconds on each
band before moving to the next, so:

  - One full cycle = 18 stations × 10s = 180s = 3 minutes.
  - At any one second exactly five beacons are on the air (one per band).
  - Each station starts each cycle on a different band, offset by its slot
    index, so the band rotation across stations is staggered.

Reference: https://www.ncdxf.org/beacon/beaconSchedule.html

This module is dependency-free and purely deterministic, given a UTC time.
It's used to drive a small sidebar widget in the web UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

# Order is canonical (NCDXF schedule). Slot index = position in this tuple.
BEACONS: tuple[str, ...] = (
    "4U1UN",   # 0
    "VE8AT",   # 1
    "W6WX",    # 2
    "KH6RS",   # 3
    "ZL6B",    # 4
    "VK6RBP",  # 5
    "JA2IGY",  # 6
    "RR9O",    # 7
    "VR2B",    # 8
    "4S7B",    # 9
    "ZS6DN",   # 10
    "5Z4B",    # 11
    "4X6TU",   # 12
    "OH2B",    # 13
    "CS3B",    # 14
    "LU4AA",   # 15
    "OA4B",    # 16
    "YV5B",    # 17
)

# Hand-curated country/location string per beacon, parallel to BEACONS by
# index. Hard-coded rather than derived from callsign prefix because special
# calls like 4U1UN (UN HQ, New York) and CS3B (Madeira) don't match a
# straight DXCC-prefix lookup, and the NCDXF table is canonical.
BEACON_LOCATIONS: tuple[str, ...] = (
    "United Nations",     # 4U1UN
    "Canada",             # VE8AT (Nunavut)
    "USA",                # W6WX (California)
    "Hawaii",             # KH6RS
    "New Zealand",        # ZL6B
    "Australia",          # VK6RBP (Perth)
    "Japan",              # JA2IGY
    "Russia",             # RR9O (Novosibirsk)
    "Hong Kong",          # VR2B
    "Sri Lanka",          # 4S7B
    "South Africa",       # ZS6DN
    "Kenya",              # 5Z4B
    "Israel",             # 4X6TU
    "Finland",            # OH2B
    "Madeira",            # CS3B
    "Argentina",          # LU4AA
    "Peru",               # OA4B
    "Venezuela",          # YV5B
)
assert len(BEACONS) == len(BEACON_LOCATIONS), "BEACONS/BEACON_LOCATIONS length drift"

# Each band is the next 10-second slot after the previous one. The first
# beacon (4U1UN) starts each cycle on 14.100 MHz at second 0.
BAND_FREQS_KHZ: tuple[float, ...] = (
    14100.0,  # 20m
    18110.0,  # 17m
    21150.0,  # 15m
    24930.0,  # 12m
    28200.0,  # 10m
)
BAND_NAMES: tuple[str, ...] = ("20m", "17m", "15m", "12m", "10m")
SLOT_SECONDS = 10
CYCLE_SECONDS = SLOT_SECONDS * len(BEACONS)  # 180s


@dataclass(frozen=True)
class BeaconTx:
    """A single beacon currently transmitting on a given band."""

    band: str
    frequency_khz: float
    callsign: str
    location: str  # country / territory, e.g. "South Africa", "United Nations"
    slot_seconds_remaining: int  # 1..10


def _slot_index_for(now: datetime) -> int:
    """Which 10-second slot of the 3-minute cycle are we in (0..17)?"""
    epoch = int(now.timestamp())
    return (epoch % CYCLE_SECONDS) // SLOT_SECONDS


def _slot_remaining(now: datetime) -> int:
    """Seconds remaining in the current 10-second slot (always 1..10)."""
    epoch = now.timestamp()
    in_slot = epoch % SLOT_SECONDS
    remaining = SLOT_SECONDS - in_slot
    # Clamp to integer 1..10 so the UI always shows a meaningful count.
    return max(1, min(SLOT_SECONDS, int(round(remaining))))


def current_beacon(now: datetime | None = None) -> list[BeaconTx]:
    """Return the beacons currently on the air — one per band, in band order.

    The schedule is deterministic: at second ``t`` of a 180-second cycle the
    beacon at slot index ``(t // 10) - b`` (mod 18) is transmitting on band
    ``b`` (where band 0 = 14.100 MHz, band 4 = 28.200 MHz). Equivalently:
    station ``i`` transmits on band ``b`` during the slot ``(i + b) mod 18``.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    slot = _slot_index_for(now)
    remaining = _slot_remaining(now)
    out: list[BeaconTx] = []
    for b in range(len(BAND_FREQS_KHZ)):
        # Slot 0 on band 0 = station 0; on band 1 = station offset back by 1
        # (so each *next* band shows the station that was on the *previous*
        # band one slot ago — i.e. its "trailing" position in the cycle).
        station_idx = (slot - b) % len(BEACONS)
        out.append(
            BeaconTx(
                band=BAND_NAMES[b],
                frequency_khz=BAND_FREQS_KHZ[b],
                callsign=BEACONS[station_idx],
                location=BEACON_LOCATIONS[station_idx],
                slot_seconds_remaining=remaining,
            )
        )
    return out
