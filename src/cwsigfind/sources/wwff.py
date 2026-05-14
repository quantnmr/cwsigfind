"""WWFFSource — polls the public WWFF Spotline JSON feed.

Endpoint:
    GET https://spots.wwff.co/static/spots.json

Returns a JSON array of recent WWFF (World Wide Flora & Fauna) park spots. No
auth required. Each entry looks like::

    {
      "id": 98874,
      "activator": "WB8MIW",
      "frequency_khz": 7046,
      "mode": "CW",
      "reference": "KFF-2482",
      "reference_name": "Fort Snelling",
      "remarks": "Re-spotted via RBN",
      "spotter": "WE9V",
      "latitude": 44.86604,
      "longitude": -93.19118,
      "spot_time": 1778777319,
      "spot_time_formatted": "2026-05-14 16:48:39"
    }

WWFF asks consumers not to hammer the endpoint, so the default poll interval
is 30 seconds and we don't go faster.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from ..geo import country_for_callsign
from ..spot import Spot
from .base import SpotSource

log = logging.getLogger(__name__)

WWFF_SPOTS_URL = "https://spots.wwff.co/static/spots.json"


class WWFFSource(SpotSource):
    name = "WWFF"

    def __init__(
        self,
        bus,
        store,
        *,
        poll_interval: float = 30.0,
        url: str = WWFF_SPOTS_URL,
        request_timeout: float = 10.0,
        user_agent: str = "cwsigfind/0.1 (+https://github.com/local)",
    ) -> None:
        super().__init__(bus, store)
        # WWFF spotline is a hot file — be polite, never poll below 30s.
        self.poll_interval = max(30.0, float(poll_interval))
        self.url = url
        self.request_timeout = request_timeout
        self.user_agent = user_agent

    async def run(self) -> None:
        async with httpx.AsyncClient(
            timeout=self.request_timeout,
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
            log.warning("WWFF fetch failed: %s", e)
            return
        except ValueError as e:
            log.warning("WWFF returned non-JSON: %s", e)
            return

        if not isinstance(payload, list):
            log.warning("WWFF returned non-list payload: %r", type(payload))
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
        log.debug("WWFF poll: %d total, %d new", len(payload), new_count)

    @staticmethod
    def _parse_time(raw: Any) -> datetime:
        if raw is None:
            return datetime.now(timezone.utc)
        try:
            return datetime.fromtimestamp(int(raw), tz=timezone.utc)
        except (TypeError, ValueError, OverflowError):
            return datetime.now(timezone.utc)

    def _to_spot(self, raw: dict[str, Any]) -> Spot | None:
        freq_raw = raw.get("frequency_khz")
        if freq_raw in (None, ""):
            return None
        try:
            freq_khz = float(freq_raw)
        except (TypeError, ValueError):
            log.debug("Skipping WWFF spot with bad frequency: %r", freq_raw)
            return None

        callsign = str(raw.get("activator") or "").upper().strip()
        if not callsign:
            return None

        lat = raw.get("latitude")
        lon = raw.get("longitude")
        spot_id = raw.get("id")
        reference = raw.get("reference")
        reference_name = raw.get("reference_name")
        country = country_for_callsign(callsign)

        return Spot(
            source="WWFF",
            callsign=callsign,
            frequency_khz=freq_khz,
            mode=str(raw.get("mode") or "").upper().strip() or "UNKNOWN",
            spotter=raw.get("spotter"),
            comment=(raw.get("remarks") or "").strip() or None,
            spotted_at=self._parse_time(raw.get("spot_time")),
            program="WWFF",
            activity_ref=str(reference) if reference else None,
            activity_name=str(reference_name) if reference_name else None,
            latitude=float(lat) if lat is not None else None,
            longitude=float(lon) if lon is not None else None,
            country=country,
            source_id=str(spot_id) if spot_id is not None else None,
        )
