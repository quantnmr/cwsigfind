"""Space-weather / HF propagation indices.

Polls https://www.hamqsl.com/solarxml.php (the canonical N0NBH-curated feed)
on a polite schedule and exposes the parsed snapshot via :func:`get_snapshot`.
The web layer turns the snapshot into JSON for the bottom-right propagation
panel.

The upstream XML is refreshed at roughly hourly cadence, so 15 minutes is the
default poll interval and we enforce a 300s floor — being a polite poller is
explicitly called out as a constraint.

Dependencies are stdlib + the project's existing ``httpx``. Parsing uses
``xml.etree.ElementTree`` to avoid pulling in lxml. If any individual field is
missing or malformed it stays ``None``; the rest of the snapshot survives.
"""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

log = logging.getLogger(__name__)

HAMQSL_URL = "https://www.hamqsl.com/solarxml.php"
SOURCE_LABEL = "hamqsl.com (N0NBH)"

# Polite floor on the poll interval — upstream refreshes ~hourly, so anything
# under five minutes is wasted bandwidth on both sides.
MIN_POLL_INTERVAL_S = 300.0
DEFAULT_POLL_INTERVAL_S = 900.0


@dataclass
class PropagationSnapshot:
    """Latest parsed propagation indices.

    Every field is optional so a partial parse degrades gracefully. ``error``
    is populated when the most recent fetch attempt failed; the rest of the
    snapshot keeps the last-known values so the UI can show "stale data".
    """

    updated: datetime | None = None
    sfi: int | None = None
    ssn: int | None = None
    a_index: int | None = None
    k_index: int | None = None
    xray: str | None = None
    helium_line: float | None = None
    proton_flux: int | None = None
    electron_flux: int | None = None
    aurora: int | None = None  # geomag latitude threshold (degrees) per hamqsl
    solar_wind: float | None = None  # km/s
    magnetic_field: float | None = None  # Bz (nT)
    geomag_field: str | None = None
    signal_noise: str | None = None
    muf: str | None = None
    hf_conditions: dict[str, dict[str, str]] = field(default_factory=dict)
    vhf_conditions: list[dict[str, str]] = field(default_factory=list)
    source: str = SOURCE_LABEL
    error: str | None = None
    fetched_at: datetime | None = None  # when this snapshot was last refreshed

    def to_json_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict for the /api/propagation endpoint."""
        d = asdict(self)
        d["updated"] = self.updated.isoformat() if self.updated else None
        d["fetched_at"] = self.fetched_at.isoformat() if self.fetched_at else None
        return d


# ---------------------------------------------------------------------------
# Parsing helpers — every one is defensive and returns None on bad input so a
# single malformed field never wipes the whole snapshot.
# ---------------------------------------------------------------------------


def _text(root: ET.Element, tag: str) -> str | None:
    el = root.find(tag)
    if el is None:
        return None
    txt = (el.text or "").strip()
    return txt or None


def _int(root: ET.Element, tag: str) -> int | None:
    txt = _text(root, tag)
    if txt is None:
        return None
    try:
        return int(float(txt))  # tolerate "10" or "10.0"
    except (TypeError, ValueError):
        return None


def _float(root: ET.Element, tag: str) -> float | None:
    txt = _text(root, tag)
    if txt is None:
        return None
    try:
        return float(txt)
    except (TypeError, ValueError):
        return None


# hamqsl prints "14 May 2026 1831 GMT" — UTC, no comma, no padded fields.
_UPDATED_FORMATS = (
    "%d %b %Y %H%M GMT",
    "%d %b %Y %H%M UTC",
    "%d %b %Y %H%M",
)


def _parse_updated(raw: str | None) -> datetime | None:
    if not raw:
        return None
    s = raw.strip()
    for fmt in _UPDATED_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _parse_hf_conditions(root: ET.Element) -> dict[str, dict[str, str]]:
    """{"80m-40m": {"day": "Good", "night": "Fair"}, ...}"""
    out: dict[str, dict[str, str]] = {}
    cc = root.find("calculatedconditions")
    if cc is None:
        return out
    for band_el in cc.findall("band"):
        name = (band_el.get("name") or "").strip()
        time_of_day = (band_el.get("time") or "").strip().lower()
        status = (band_el.text or "").strip()
        if not name or not time_of_day or not status:
            continue
        out.setdefault(name, {})[time_of_day] = status
    return out


def _parse_vhf_conditions(root: ET.Element) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    cc = root.find("calculatedvhfconditions")
    if cc is None:
        return out
    for ph in cc.findall("phenomenon"):
        phenomenon = (ph.get("name") or "").strip()
        location = (ph.get("location") or "").strip()
        status = (ph.text or "").strip()
        if not phenomenon and not location and not status:
            continue
        out.append({"phenomenon": phenomenon, "location": location, "status": status})
    return out


def parse_solarxml(xml_text: str) -> PropagationSnapshot:
    """Parse the hamqsl solar XML into a snapshot.

    Raises :class:`xml.etree.ElementTree.ParseError` on malformed XML. Caller
    is expected to catch and record the failure in ``snapshot.error``.
    """
    root = ET.fromstring(xml_text)
    # Expected shape: <solar><solardata>...</solardata></solar>
    solardata = root.find("solardata") if root.tag != "solardata" else root
    if solardata is None:
        # Fall back to root if the document is just <solardata> at the top.
        solardata = root

    return PropagationSnapshot(
        updated=_parse_updated(_text(solardata, "updated")),
        sfi=_int(solardata, "solarflux"),
        ssn=_int(solardata, "sunspots"),
        a_index=_int(solardata, "aindex"),
        k_index=_int(solardata, "kindex"),
        xray=_text(solardata, "xray"),
        helium_line=_float(solardata, "heliumline"),
        proton_flux=_int(solardata, "protonflux"),
        # Note: hamqsl spells it "electonflux" (sic) — we keep the wire name on
        # the XML side and the corrected name on our snapshot side.
        electron_flux=_int(solardata, "electonflux"),
        aurora=_int(solardata, "aurora"),
        solar_wind=_float(solardata, "solarwind"),
        magnetic_field=_float(solardata, "magneticfield"),
        geomag_field=_text(solardata, "geomagfield"),
        signal_noise=_text(solardata, "signalnoise"),
        muf=_text(solardata, "muf"),
        hf_conditions=_parse_hf_conditions(solardata),
        vhf_conditions=_parse_vhf_conditions(solardata),
        source=SOURCE_LABEL,
    )


# ---------------------------------------------------------------------------
# Singleton snapshot + async fetcher.
# ---------------------------------------------------------------------------


_snapshot: PropagationSnapshot = PropagationSnapshot()
_lock = asyncio.Lock()


def get_snapshot() -> PropagationSnapshot:
    """Return the latest snapshot (may be partial / stale; check ``error``)."""
    return _snapshot


async def _fetch_once(client: httpx.AsyncClient, url: str) -> PropagationSnapshot:
    """One fetch + parse; raises on network/parse failure."""
    r = await client.get(url)
    r.raise_for_status()
    return parse_solarxml(r.text)


async def fetch_and_update(
    client: httpx.AsyncClient, url: str = HAMQSL_URL
) -> PropagationSnapshot:
    """Refresh the singleton snapshot. Always returns the current snapshot,
    populated with an ``error`` string if the fetch / parse failed (so the UI
    can keep showing the last-known values + a "stale" indicator).
    """
    global _snapshot
    try:
        fresh = await _fetch_once(client, url)
        fresh.fetched_at = datetime.now(timezone.utc)
        async with _lock:
            _snapshot = fresh
        return fresh
    except (httpx.HTTPError, ET.ParseError) as e:
        err = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
        log.warning("Propagation fetch failed: %s", err)
        async with _lock:
            # Preserve all previously-parsed fields; just stamp the error.
            _snapshot.error = err
            _snapshot.fetched_at = datetime.now(timezone.utc)
            return _snapshot


async def run_loop(
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    url: str = HAMQSL_URL,
    *,
    user_agent: str = "cwsigfind/0.1 (+https://github.com/quantnmr/cwsigfind)",
) -> None:
    """Background task: poll hamqsl forever, updating the singleton snapshot.

    Failures are absorbed and recorded on the snapshot; the loop keeps running
    (with backoff) so a transient network blip doesn't take the panel offline.
    """
    interval = max(MIN_POLL_INTERVAL_S, float(poll_interval_s))
    backoff = interval
    async with httpx.AsyncClient(
        timeout=20.0,
        headers={"User-Agent": user_agent, "Accept": "application/xml,text/xml,*/*"},
    ) as client:
        while True:
            snap = await fetch_and_update(client, url)
            if snap.error is None:
                backoff = interval
                log.debug(
                    "Propagation: SFI=%s SSN=%s A=%s K=%s xray=%s",
                    snap.sfi, snap.ssn, snap.a_index, snap.k_index, snap.xray,
                )
            else:
                # Exponential backoff, capped at the configured interval. We
                # never poll faster than the floor regardless of failures.
                backoff = min(interval * 2, max(interval, backoff * 1.5))
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                raise


def reset_snapshot_for_tests() -> None:
    """Test helper: clear the module-global snapshot between tests."""
    global _snapshot
    _snapshot = PropagationSnapshot()
