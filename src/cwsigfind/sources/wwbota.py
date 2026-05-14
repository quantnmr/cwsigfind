"""WWBOTASource — polls the WWBOTA (Bunkers On The Air) public spots API.

Endpoint:
    GET https://api.wwbota.org/spots/?age=<hours>

The OpenAPI spec lives at ``https://api.wwbota.org/openapi.json`` (FastAPI /
ReDoc), confirmed by hitting it during development. We pass ``age`` (max 24h)
so the API returns the recent window directly; default 1h is a reasonable
balance between completeness and payload size for a poll loop.

The API also supports ETag (``If-None-Match``) and Server-Sent Events
(``Last-Event-ID``). SSE is the upstream-preferred mechanism (see OpenHamClock
PR #550 for a reference SSE client) and is the obvious future optimization.
For now we keep parity with the other polling sources to minimize complexity:
60-second poll interval, ETag-aware for cheap 304s.

Each spot looks like::

    {
      "spotter": "M8HPI",
      "call": "M8HPI",
      "freq": 7.162,            # MHz, not kHz
      "mode": "SSB",
      "comment": "B/G-0977 WAB SE64 QRT",
      "type": "Live" | "QRT" | "Test",
      "time": "2026-05-14T14:06:06.020981Z",
      "references": [
        { "reference": "B/G-0977", "name": "ROC Post Fulford Site 1",
          "lat": 53.92, "long": -1.07, "dxcc": 223, "scheme": "UKBOTA",
          "type": "ROC Bunker", ... }
      ]
    }

We treat the first reference as the primary one. Spots with ``type="QRT"`` are
still surfaced (a "going QRT" notification is useful), but we tag them in the
comment so the UI can differentiate.
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

WWBOTA_SPOTS_URL = "https://api.wwbota.org/spots/"


class WWBOTASource(SpotSource):
    name = "BOTA"

    def __init__(
        self,
        bus,
        store,
        *,
        poll_interval: float = 60.0,
        url: str = WWBOTA_SPOTS_URL,
        age_hours: int = 1,
        request_timeout: float = 15.0,
        user_agent: str = "cwsigfind/0.1 (+https://github.com/local)",
    ) -> None:
        super().__init__(bus, store)
        # WWBOTA asks for "polite" polling; never go below 60s.
        self.poll_interval = max(60.0, float(poll_interval))
        self.url = url
        self.age_hours = max(1, min(int(age_hours), 24))
        self.request_timeout = request_timeout
        self.user_agent = user_agent
        # ETag from the last successful response; lets the server reply 304.
        self._etag: str | None = None

    async def run(self) -> None:
        async with httpx.AsyncClient(
            timeout=self.request_timeout,
            headers={"User-Agent": self.user_agent, "Accept": "application/json"},
        ) as client:
            while True:
                await self._poll_once(client)
                await asyncio.sleep(self.poll_interval)

    async def _poll_once(self, client: httpx.AsyncClient) -> None:
        headers: dict[str, str] = {}
        if self._etag:
            headers["If-None-Match"] = self._etag
        try:
            r = await client.get(
                self.url, params={"age": self.age_hours}, headers=headers
            )
            if r.status_code == 304:
                log.debug("WWBOTA: 304 Not Modified")
                return
            r.raise_for_status()
            payload = r.json()
        except httpx.HTTPError as e:
            log.warning("WWBOTA fetch failed: %s", e)
            return
        except ValueError as e:
            log.warning("WWBOTA returned non-JSON: %s", e)
            return

        new_etag = r.headers.get("etag")
        if new_etag:
            self._etag = new_etag

        if not isinstance(payload, list):
            log.warning("WWBOTA returned non-list payload: %r", type(payload))
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
        log.debug("WWBOTA poll: %d total, %d new", len(payload), new_count)

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

    @staticmethod
    def _stable_id(raw: dict[str, Any]) -> str | None:
        """WWBOTA doesn't expose a numeric spot id. Build a content-stable one.

        Calltype + activator + time-to-the-second + freq is unique enough for
        dedup across polls without colliding spots that are minutes apart on the
        same bunker.
        """
        time = raw.get("time")
        call = raw.get("call")
        freq = raw.get("freq")
        spot_type = raw.get("type", "Live")
        if time and call and freq is not None:
            return f"{call}:{spot_type}:{time}:{freq}"
        return None

    def _to_spot(self, raw: dict[str, Any]) -> Spot | None:
        freq_raw = raw.get("freq")
        if freq_raw in (None, ""):
            # Some QRT spots may omit freq; without freq we can't do anything useful.
            return None
        try:
            # WWBOTA delivers frequency in MHz.
            freq_khz = float(freq_raw) * 1000.0
        except (TypeError, ValueError):
            log.debug("Skipping WWBOTA spot with bad frequency: %r", freq_raw)
            return None

        callsign = str(raw.get("call") or "").upper().strip()
        if not callsign:
            return None

        refs = raw.get("references")
        primary: dict[str, Any] | None = None
        if isinstance(refs, list) and refs and isinstance(refs[0], dict):
            primary = refs[0]

        ref = None
        ref_name = None
        lat = None
        lon = None
        if primary is not None:
            ref = primary.get("reference")
            ref_name = primary.get("name")
            lat = primary.get("lat")
            lon = primary.get("long")
        if ref is None:
            # Fall back to first B/XX-NNNN token in the comment.
            comment = str(raw.get("comment") or "")
            m = re.search(r"\bB/[A-Z0-9]{1,3}-\d{4}\b", comment)
            if m:
                ref = m.group(0)

        spot_type = str(raw.get("type") or "Live")
        base_comment = (raw.get("comment") or "").strip()
        # Surface QRT/Test type in the comment column so users see it at a glance.
        if spot_type and spot_type != "Live":
            comment = f"[{spot_type}] {base_comment}".strip()
        else:
            comment = base_comment or None

        country = country_for_callsign(callsign)

        return Spot(
            source="BOTA",
            callsign=callsign,
            frequency_khz=freq_khz,
            mode=str(raw.get("mode") or "").upper().strip() or "UNKNOWN",
            spotter=raw.get("spotter"),
            comment=comment,
            spotted_at=self._parse_time(raw.get("time")),
            program="BOTA",
            activity_ref=str(ref) if ref else None,
            activity_name=str(ref_name) if ref_name else None,
            latitude=float(lat) if lat is not None else None,
            longitude=float(lon) if lon is not None else None,
            country=country,
            source_id=self._stable_id(raw),
        )
