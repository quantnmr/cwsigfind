"""Composable filtering of Spots by mode, band, region, and callsign prefix."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .spot import Spot


@dataclass
class SpotFilter:
    """A spot passes only if every populated criterion matches.

    Empty/None criteria are treated as "no constraint" so a default SpotFilter
    matches everything.
    """

    modes: set[str] = field(default_factory=set)
    bands: set[str] = field(default_factory=set)
    regions: list[re.Pattern[str]] = field(default_factory=list)
    callsign_prefixes: set[str] = field(default_factory=set)

    def matches(self, spot: Spot) -> bool:
        if self.modes and spot.mode.upper() not in self.modes:
            return False
        if self.bands and spot.band not in self.bands:
            return False
        if self.regions:
            loc = spot.location_desc or ""
            if not any(p.search(loc) for p in self.regions):
                return False
        if self.callsign_prefixes:
            call = spot.callsign.upper()
            if not any(call.startswith(p) for p in self.callsign_prefixes):
                return False
        return True
