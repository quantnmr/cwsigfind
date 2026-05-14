"""RBNSource — Reverse Beacon Network telnet feed.

RBN is a worldwide network of CW (and some digital) skimmer receivers. Spots
include SNR and WPM, e.g.:

    DX de W3OA-#:    14025.5  K3XYZ        CW    25 dB  22 WPM  CQ      1432Z

This is a firehose — make sure to filter aggressively before showing in the UI.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from ..geo import country_for_callsign
from ..spot import Spot
from .dxcluster import DXClusterSource

log = logging.getLogger(__name__)


RBN_RE = re.compile(
    r"^DX de\s+(?P<spotter>[A-Z0-9/\-#]+):\s*"
    r"(?P<freq>\d+(?:\.\d+)?)\s+"
    r"(?P<call>[A-Z0-9/]+)\s+"
    r"(?P<mode>CW|RTTY|PSK\d*|BPSK\d*|FT8|FT4|JT\d+)\s+"
    r"(?P<snr>\d+)\s*dB\s+"
    r"(?P<rate>\d+)\s*(?P<rate_unit>WPM|BPS)\s+"
    r"(?P<info>.*?)\s+"
    r"(?P<time>\d{4})Z",
    re.IGNORECASE,
)


class RBNSource(DXClusterSource):
    name = "RBN"

    def parse(self, line: str) -> Spot | None:
        m = RBN_RE.match(line.strip())
        if not m:
            return None
        try:
            freq_khz = float(m.group("freq"))
        except ValueError:
            return None
        snr = m.group("snr")
        rate = m.group("rate")
        rate_unit = m.group("rate_unit").lower()
        info = m.group("info").strip()
        comment = f"{snr}dB {rate}{rate_unit} {info}".strip()
        callsign = m.group("call").upper()
        return Spot(
            source="RBN",
            callsign=callsign,
            frequency_khz=freq_khz,
            mode=m.group("mode").upper(),
            spotter=m.group("spotter"),
            comment=comment,
            spotted_at=datetime.now(timezone.utc),
            country=country_for_callsign(callsign),
        )
