"""POTASource — polls the public Parks On The Air spots API.

Endpoint: https://api.pota.app/spot/activator
Returns a JSON array of currently-active activator spots. No auth required.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from ..geo import enrich
from ..spot import Spot
from .base import SpotSource

log = logging.getLogger(__name__)

POTA_SPOTS_URL = "https://api.pota.app/spot/activator"


class POTASource(SpotSource):
    name = "POTA"

    def __init__(
        self,
        bus,
        store,
        *,
        poll_interval: float = 30.0,
        url: str = POTA_SPOTS_URL,
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
            log.warning("POTA fetch failed: %s", e)
            return
        except ValueError as e:
            log.warning("POTA returned non-JSON: %s", e)
            return

        if not isinstance(payload, list):
            log.warning("POTA returned non-list payload: %r", type(payload))
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
        log.debug("POTA poll: %d total, %d new", len(payload), new_count)

    @staticmethod
    def _parse_time(raw: Any) -> datetime:
        if not raw:
            return datetime.now(timezone.utc)
        s = str(raw)
        # POTA uses ISO 8601, sometimes with trailing Z, sometimes without TZ.
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
            freq_khz = float(str(freq_raw).strip())
        except (TypeError, ValueError):
            log.debug("Skipping POTA spot with bad frequency: %r", raw.get("frequency"))
            return None

        callsign = str(raw.get("activator") or "").upper().strip()
        if not callsign:
            return None

        lat = raw.get("latitude")
        lon = raw.get("longitude")
        spot_id = raw.get("spotId")
        location_desc = raw.get("locationDesc")
        country, state = enrich(callsign, location_desc)

        return Spot(
            source="POTA",
            callsign=callsign,
            frequency_khz=freq_khz,
            mode=str(raw.get("mode") or "").upper().strip() or "UNKNOWN",
            spotter=raw.get("spotter"),
            comment=raw.get("comments"),
            spotted_at=self._parse_time(raw.get("spotTime")),
            program="POTA",
            activity_ref=raw.get("reference"),
            activity_name=raw.get("parkName") or raw.get("name"),
            location_desc=location_desc,
            grid=raw.get("grid6") or raw.get("grid4"),
            latitude=float(lat) if lat is not None else None,
            longitude=float(lon) if lon is not None else None,
            country=country,
            state=state,
            source_id=str(spot_id) if spot_id is not None else None,
        )
