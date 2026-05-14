"""Common Spot dataclass and frequency-to-band mapping shared by all sources."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

SourceTag = Literal["POTA", "DX", "RBN", "SOTA", "WWFF", "BOTA"]
ProgramTag = Literal["POTA", "SOTA", "IOTA", "WWFF", "BOTA", "WCA"]


# (low_khz, high_khz, name) — IARU-ish amateur band edges, broad enough to catch edge spots.
_BANDS: tuple[tuple[float, float, str], ...] = (
    (1800.0, 2000.0, "160m"),
    (3500.0, 4000.0, "80m"),
    (5250.0, 5450.0, "60m"),
    (7000.0, 7300.0, "40m"),
    (10100.0, 10150.0, "30m"),
    (14000.0, 14350.0, "20m"),
    (18068.0, 18168.0, "17m"),
    (21000.0, 21450.0, "15m"),
    (24890.0, 24990.0, "12m"),
    (28000.0, 29700.0, "10m"),
    (50000.0, 54000.0, "6m"),
    (144000.0, 148000.0, "2m"),
    (222000.0, 225000.0, "1.25m"),
    (420000.0, 450000.0, "70cm"),
)


def freq_to_band(khz: float) -> str | None:
    """Map a frequency in kHz to its amateur band name, or None if out of band."""
    for lo, hi, name in _BANDS:
        if lo <= khz <= hi:
            return name
    return None


@dataclass(frozen=True)
class Spot:
    """A normalized spot from any source. Immutable so it's safe to share across tasks."""

    source: SourceTag
    callsign: str
    frequency_khz: float
    mode: str
    spotter: str | None = None
    comment: str | None = None
    spotted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Program-level reference for the activity, source-agnostic:
    #   POTA → park reference (e.g. "K-0034")
    #   SOTA → summit reference (e.g. "W6/CT-001")
    #   IOTA → island group reference (e.g. "NA-052"), usually parsed out of a
    #          DX cluster comment.
    program: ProgramTag | None = None
    activity_ref: str | None = None
    activity_name: str | None = None
    activity_extra: str | None = None  # "1742m · 8 pts" for SOTA, etc.

    location_desc: str | None = None
    grid: str | None = None
    latitude: float | None = None
    longitude: float | None = None

    # Geocoded fields. `country` is filled from POTA's locationDesc when
    # available, otherwise inferred from the callsign prefix. `state` is only
    # populated for POTA spots (POTA tells us; callsign prefixes don't).
    country: str | None = None
    state: str | None = None

    # Optional source-native ID used for dedup when available (e.g. POTA spotId).
    source_id: str | None = None

    @property
    def band(self) -> str | None:
        return freq_to_band(self.frequency_khz)

    def dedup_key(self) -> str:
        """Stable key used by SpotStore to avoid re-emitting the same spot.

        POTA gives us a `spotId` we can trust. Cluster/RBN don't, so we bucket by
        (call, freq_rounded, source, minute) — same callsign on the same kHz in the
        same minute on the same source is treated as one spot.
        """
        if self.source_id:
            return f"{self.source}:{self.source_id}"
        minute = self.spotted_at.replace(second=0, microsecond=0).isoformat()
        return f"{self.source}:{self.callsign}:{round(self.frequency_khz)}:{minute}"
