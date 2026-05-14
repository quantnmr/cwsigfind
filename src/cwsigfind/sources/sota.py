"""SOTASource — polls the Summits On The Air spots API.

Endpoint:
    GET https://api2.sota.org.uk/api/spots/-1/all

Returns a JSON array of recent SOTA spots. No auth.

Each spot looks like::

    {
      "id": 309153,
      "timeStamp": "2026-05-14T16:17:24",
      "callsign": "EA3HIG",
      "associationCode": "EA3",
      "summitCode": "BC-039",
      "activatorCallsign": "EA3HIG",
      "activatorName": "Juanjo",
      "frequency": "7.039",     # MHz, string
      "mode": "CW",
      "summitDetails": "Bellmunt, 1247m, 4 points",
      "comments": "CQ SOTA "
    }
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from ..geo import country_for_callsign
from ..spot import Spot
from .base import SpotSource

log = logging.getLogger(__name__)

SOTA_SPOTS_URL = "https://api2.sota.org.uk/api/spots/-1/all"


def _parse_summit_details(details: str | None) -> tuple[str | None, str | None]:
    """Split "Name, 1247m, 4 points" into ("Name", "1247m · 4 pts")."""
    if not details:
        return None, None
    parts = [p.strip() for p in details.split(",") if p.strip()]
    if not parts:
        return None, None
    name = parts[0]
    extras: list[str] = []
    for p in parts[1:]:
        cleaned = re.sub(r"\bpoints?\b", "pts", p, flags=re.IGNORECASE)
        extras.append(cleaned)
    extra = " · ".join(extras) if extras else None
    return name, extra


class SOTASource(SpotSource):
    name = "SOTA"

    def __init__(
        self,
        bus,
        store,
        *,
        poll_interval: float = 60.0,
        url: str = SOTA_SPOTS_URL,
        user_agent: str = "cwsigfind/0.1 (+https://github.com/local)",
    ) -> None:
        super().__init__(bus, store)
        self.poll_interval = poll_interval
        self.url = url
        self.user_agent = user_agent

    async def run(self) -> None:
        async with httpx.AsyncClient(
            timeout=20.0,
            headers={"User-Agent": self.user_agent, "Accept": "application/json"},
        ) as client:
            while True:
                await self._poll_once(client)
                await asyncio.sleep(self.poll_interval)

    async def _poll_once(self, client: httpx.AsyncClient) -> None:
        try:
            r = await client.get(self.url)
            r.raise_for_status()
            payload = r.json()
        except httpx.HTTPError as e:
            log.warning("SOTA fetch failed: %s", e)
            return
        except ValueError as e:
            log.warning("SOTA returned non-JSON: %s", e)
            return

        if not isinstance(payload, list):
            log.warning("SOTA returned non-list payload: %r", type(payload))
            return

        new_count = 0
        for raw in payload:
            if not isinstance(raw, dict):
                continue
            spot = self._to_spot(raw)
            if spot is None:
                continue
            if self.store.add(spot):
                new_count += 1
                await self.bus.publish(spot)
        log.debug("SOTA poll: %d total, %d new", len(payload), new_count)

    @staticmethod
    def _parse_time(raw: Any) -> datetime:
        if not raw:
            return datetime.now(timezone.utc)
        s = str(raw)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def _to_spot(self, raw: dict[str, Any]) -> Spot | None:
        try:
            freq_raw = raw.get("frequency")
            if freq_raw in (None, ""):
                return None
            # SOTA delivers frequency in MHz as a string.
            freq_khz = float(str(freq_raw).strip()) * 1000.0
        except (TypeError, ValueError):
            log.debug("Skipping SOTA spot with bad frequency: %r", raw.get("frequency"))
            return None

        callsign = str(raw.get("activatorCallsign") or raw.get("callsign") or "").upper().strip()
        if not callsign:
            return None

        association = str(raw.get("associationCode") or "").upper().strip()
        region_num = str(raw.get("summitCode") or "").upper().strip()
        summit_ref = f"{association}/{region_num}" if association and region_num else (
            region_num or association or None
        )

        name, extra = _parse_summit_details(raw.get("summitDetails"))

        # Country: the *summit's* country, derived from the association code
        # which doubles as a callsign-style prefix (G, W6, EA3, ...).
        country = country_for_callsign(association) if association else None
        if country is None:
            country = country_for_callsign(callsign)

        spot_id = raw.get("id")

        return Spot(
            source="SOTA",
            callsign=callsign,
            frequency_khz=freq_khz,
            mode=str(raw.get("mode") or "").upper().strip() or "UNKNOWN",
            spotter=str(raw.get("callsign") or "") if raw.get("callsign") != callsign else None,
            comment=(raw.get("comments") or "").strip() or None,
            spotted_at=self._parse_time(raw.get("timeStamp")),
            program="SOTA",
            activity_ref=summit_ref,
            activity_name=name,
            activity_extra=extra,
            country=country,
            source_id=str(spot_id) if spot_id is not None else None,
        )
