"""DXClusterSource — persistent telnet connection to a DX cluster node.

Cluster nodes emit lines like:

    DX de W1ABC-#:    14025.0  K3XYZ        CW POTA K-0034              1432Z

We log in with the configured callsign, then stream and parse "DX de" lines.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

import telnetlib3

from ..geo import country_for_callsign
from ..iota import group_name as iota_group_name
from ..spot import Spot
from .base import SpotSource

log = logging.getLogger(__name__)


# Anchored on "DX de" with flexible whitespace. The trailing time is "HHMMZ".
DX_DE_RE = re.compile(
    r"^DX de\s+(?P<spotter>[A-Z0-9/\-#]+):\s*"
    r"(?P<freq>\d+(?:\.\d+)?)\s+"
    r"(?P<call>[A-Z0-9/]+)\s+"
    r"(?P<comment>.*?)\s+"
    r"(?P<time>\d{4})Z",
    re.IGNORECASE,
)

# Heuristic: many spotters tag the mode somewhere in the comment.
MODE_HINT_RE = re.compile(
    r"\b(CW|SSB|USB|LSB|AM|FM|FT8|FT4|RTTY|PSK\d*|JT\d+|FSK\d*|MFSK\d*|OLIVIA|DIGI|DATA|SSTV)\b",
    re.IGNORECASE,
)

# IOTA references have a unique shape: one of the 7 continent codes
# followed by "-NNN". Embedded anywhere in the comment.
IOTA_RE = re.compile(r"\b(AF|AN|AS|EU|NA|OC|SA)-(\d{3})\b", re.IGNORECASE)

# POTA references like "K-1234", "DE-0034", "VE-1234" — we honour these when a
# DX cluster spot mentions a park, so the activity column shows the ref even
# for spots that arrive via cluster instead of the POTA feed.
POTA_RE = re.compile(r"\b([A-Z]{1,2})-(\d{4,5})\b")

# SOTA references like "W6/CT-001", "G/LD-001". Note: a region code can have
# letters and/or digits.
SOTA_RE = re.compile(r"\b([A-Z0-9]{1,3})/([A-Z]{1,2})-(\d{3})\b", re.IGNORECASE)

# WWFF park references like "KFF-2432", "GFF-0123", "DLFF-0033". Up to three
# leading letters identify the country (note: WWFF allows 2-letter and 3-letter
# country codes); the suffix is always exactly 4 digits.
WWFF_RE = re.compile(r"\b([A-Z]{1,3}FF)-(\d{4})\b")

# WWBOTA bunker references like "B/G-2453", "B/DL-0033", "B/9A-0001". The
# scheme is "B/<country>-NNNN" where country is 1-3 alphanumerics.
BOTA_RE = re.compile(r"\bB/([A-Z0-9]{1,3})-(\d{4})\b")

# WCA / DCI / DFCF "castle-family" awards. We only match these when the
# award keyword appears in the comment, because the raw "XX-NNNNN" / "XX-NNN"
# shapes are too generic and would overmatch.
WCA_RE = re.compile(r"\bWCA\s+([A-Z]{1,3}-\d{4,5})\b", re.IGNORECASE)
DCI_RE = re.compile(r"\bDCI\s+([A-Z]{2}-\d{3,4})\b", re.IGNORECASE)
DFCF_RE = re.compile(r"\bDFCF\s+(\d{2}-\d{3,4})\b", re.IGNORECASE)


def extract_program_ref(comment: str | None) -> tuple[str | None, str | None]:
    """Return (program, activity_ref) detected in a free-form comment.

    Priority (most specific first):
        1. SOTA   — distinctive "XXX/YY-NNN" shape
        2. WWBOTA — distinctive "B/XX-NNNN" shape
        3. WWFF   — distinctive "XFF-NNNN" shape
        4. WCA / DCI / DFCF — castle-family awards, gated by an explicit award
           keyword. These are checked *before* the loose POTA regex because
           a token like "EA-01234" would otherwise be eaten by POTA's
           ``[A-Z]{1,2}-\\d{4,5}`` pattern. The keyword anchor makes them
           strictly more specific when the keyword is present.
           Grouped under ``program="WCA"`` for UI simplicity.
        5. IOTA   — continent code + 3 digits
        6. POTA   — loosest pattern, last so it doesn't poach more specific refs.
    """
    if not comment:
        return None, None
    m = SOTA_RE.search(comment)
    if m:
        return "SOTA", f"{m.group(1).upper()}/{m.group(2).upper()}-{m.group(3)}"
    m = BOTA_RE.search(comment)
    if m:
        # Note: when both this DX-comment match AND a dedicated WWBOTASource
        # are running, the same bunker activation may surface as a "DX" spot
        # AND a "BOTA" spot. They have different dedup keys so they won't
        # merge; users see this as redundancy, similar to POTA/DX duplication.
        return "BOTA", f"B/{m.group(1).upper()}-{m.group(2)}"
    m = WWFF_RE.search(comment)
    if m:
        return "WWFF", f"{m.group(1).upper()}-{m.group(2)}"
    m = WCA_RE.search(comment)
    if m:
        return "WCA", m.group(1).upper()
    m = DCI_RE.search(comment)
    if m:
        return "WCA", f"DCI {m.group(1).upper()}"
    m = DFCF_RE.search(comment)
    if m:
        return "WCA", f"DFCF {m.group(1)}"
    m = IOTA_RE.search(comment)
    if m:
        return "IOTA", f"{m.group(1).upper()}-{m.group(2)}"
    m = POTA_RE.search(comment)
    if m:
        return "POTA", f"{m.group(1).upper()}-{m.group(2)}"
    return None, None


def _normalize_mode(raw: str) -> str:
    m = raw.upper()
    if m in ("USB", "LSB"):
        return "SSB"
    return m


# IARU Region 2 / US-centric band plan. We use this as a fallback when the
# spotter didn't include an explicit mode token in their comment. Cluster
# operators *frequently* omit the mode, so without this fallback a CW-only
# filter would drop almost every cluster spot.
#
# Each entry is (low_khz, high_khz, mode). Ordered by frequency; the first
# match wins. Digital hot-spots take precedence over the broader sub-bands.

_DIGITAL_HOTSPOTS_KHZ: tuple[tuple[float, str], ...] = (
    # FT8 calling frequencies — within ±3 kHz is "FT8".
    (1840.0, "FT8"),
    (3573.0, "FT8"),
    (5357.0, "FT8"),
    (7074.0, "FT8"),
    (10136.0, "FT8"),
    (14074.0, "FT8"),
    (18100.0, "FT8"),
    (21074.0, "FT8"),
    (24915.0, "FT8"),
    (28074.0, "FT8"),
    (50313.0, "FT8"),
    # FT4 calling frequencies.
    (3575.0, "FT4"),
    (7047.5, "FT4"),
    (10140.0, "FT4"),
    (14080.0, "FT4"),
    (18104.0, "FT4"),
    (21140.0, "FT4"),
    (24919.0, "FT4"),
    (28180.0, "FT4"),
    (50318.0, "FT4"),
)

_BANDPLAN: tuple[tuple[float, float, str], ...] = (
    # 160m
    (1800.0, 1838.0, "CW"),
    (1838.0, 2000.0, "SSB"),
    # 80m
    (3500.0, 3600.0, "CW"),
    (3600.0, 4000.0, "SSB"),
    # 60m channelized — call it SSB; CW is allowed but rare.
    (5250.0, 5450.0, "SSB"),
    # 40m
    (7000.0, 7125.0, "CW"),
    (7125.0, 7300.0, "SSB"),
    # 30m (CW + narrow data only by regulation; FT8 hotspot above handles digi)
    (10100.0, 10150.0, "CW"),
    # 20m
    (14000.0, 14150.0, "CW"),
    (14150.0, 14350.0, "SSB"),
    # 17m
    (18068.0, 18110.0, "CW"),
    (18110.0, 18168.0, "SSB"),
    # 15m
    (21000.0, 21200.0, "CW"),
    (21200.0, 21450.0, "SSB"),
    # 12m
    (24890.0, 24930.0, "CW"),
    (24930.0, 24990.0, "SSB"),
    # 10m
    (28000.0, 28300.0, "CW"),
    (28300.0, 29700.0, "SSB"),
    # 6m
    (50000.0, 50100.0, "CW"),
    (50100.0, 54000.0, "SSB"),
)


def infer_mode_from_frequency(freq_khz: float) -> str | None:
    """Best-effort mode guess from a frequency alone.

    Returns one of "CW"/"SSB"/"FT8"/"FT4", or None if out of any known HF/6m
    sub-band. Digital hot-spots are checked first (they're inside CW or SSB
    regions, so they need priority).
    """
    for hotspot, mode in _DIGITAL_HOTSPOTS_KHZ:
        if abs(freq_khz - hotspot) < 3.0:
            return mode
    for lo, hi, mode in _BANDPLAN:
        if lo <= freq_khz <= hi:
            return mode
    return None


def infer_mode(comment: str | None, freq_khz: float) -> str:
    """Pick the best mode label: explicit comment hint, then frequency, then UNKNOWN."""
    if comment:
        mm = MODE_HINT_RE.search(comment)
        if mm:
            return _normalize_mode(mm.group(1))
    guess = infer_mode_from_frequency(freq_khz)
    if guess:
        return guess
    return "UNKNOWN"


class DXClusterSource(SpotSource):
    name = "DX"

    def __init__(
        self,
        bus,
        store,
        *,
        host: str,
        port: int,
        callsign: str,
        login_commands: list[str] | None = None,
        connect_timeout: float = 20.0,
        login_timeout: float = 15.0,
        read_timeout: float = 180.0,
        encoding: str = "latin-1",
        label: str | None = None,
    ) -> None:
        super().__init__(bus, store)
        self.host = host
        self.port = port
        self.callsign = callsign
        self.login_commands = list(login_commands or [])
        self.connect_timeout = connect_timeout
        self.login_timeout = login_timeout
        # Max time we'll wait between any two bytes from the peer before we
        # declare the TCP connection half-open and let the supervisor reconnect.
        # Half-open sockets (peer crash, NAT eviction, ISP route flap) don't
        # surface as EOF or errors — they just silently hang. RBN and busy DX
        # cluster nodes always emit something well within 180s, so a timeout
        # this long catches dead connections without ever firing under load.
        self.read_timeout = read_timeout
        self.encoding = encoding
        # Human-friendly label used in logs and surfaced in `spot.comment` so
        # users can tell *which* cluster delivered a spot. The source tag
        # itself remains "DX" so the UI source-chip filter doesn't multiply.
        self.label = label
        if label:
            # Keep `name` distinct in supervisor task names (one per cluster),
            # while the spot's `source` field remains the "DX" tag.
            self.name = f"DX-{label}"

    async def run(self) -> None:
        log.info("%s: connecting to %s:%d", self.name, self.host, self.port)
        reader, writer = await asyncio.wait_for(
            telnetlib3.open_connection(self.host, self.port, encoding=self.encoding),
            timeout=self.connect_timeout,
        )
        try:
            await self._login(reader, writer)
            log.info("%s: connected and logged in as %s", self.name, self.callsign)
            await self._read_loop(reader)
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _login(self, reader, writer) -> None:
        # Read until we see a login prompt or timeout — then send callsign + any
        # custom commands. Many nodes are happy with just the callsign; some
        # accept SET commands like "SET/FT8" or "SH/DX 25" to seed history.
        prompt_re = re.compile(
            r"(login|call(?:sign)?|please enter your call|your call)\s*[:>]?",
            re.IGNORECASE,
        )
        buf = ""
        try:
            while True:
                chunk = await asyncio.wait_for(reader.read(1024), timeout=self.login_timeout)
                if not chunk:
                    break
                buf += chunk
                if prompt_re.search(buf):
                    break
        except asyncio.TimeoutError:
            log.warning("%s: login prompt timeout, sending callsign anyway", self.name)

        writer.write(self.callsign + "\r\n")
        for cmd in self.login_commands:
            writer.write(cmd + "\r\n")
        await writer.drain()

    async def _read_loop(self, reader) -> None:
        while True:
            try:
                line = await asyncio.wait_for(
                    reader.readline(), timeout=self.read_timeout
                )
            except asyncio.TimeoutError as e:
                # No bytes for read_timeout seconds → assume the socket is
                # half-open (silent peer disconnect). Raise so the supervisor
                # treats it as a crash and reconnects with backoff.
                raise ConnectionError(
                    f"{self.name}: no data for {self.read_timeout:.0f}s — "
                    "treating connection as dead"
                ) from e
            if not line:
                raise ConnectionError(f"{self.name}: peer closed connection")
            text = line.rstrip("\r\n")
            spot = self.parse(text)
            if spot is None:
                continue
            if self.store.add(spot):
                await self.bus.publish(spot)

    def parse(self, line: str) -> Spot | None:
        m = DX_DE_RE.match(line.strip())
        if not m:
            return None
        try:
            freq_khz = float(m.group("freq"))
        except ValueError:
            return None

        comment = m.group("comment").strip()
        mode = infer_mode(comment, freq_khz)
        callsign = m.group("call").upper()
        program, activity_ref = extract_program_ref(comment)
        # If we tagged the spot as IOTA, surface the island-group name from the
        # local catalog (downloaded at startup). For POTA/SOTA refs caught in
        # cluster comments we don't have a local name catalog, so just the ref.
        activity_name = (
            iota_group_name(activity_ref)
            if program == "IOTA" and activity_ref
            else None
        )

        # Annotate the comment with the cluster label so users can see which
        # node surfaced a given spot when multiple clusters are connected.
        # `getattr` is defensive: unit tests construct via __new__ and skip __init__.
        label = getattr(self, "label", None)
        if label:
            tagged_comment = f"[{label}] {comment}".strip() if comment else f"[{label}]"
        else:
            tagged_comment = comment or None

        return Spot(
            source="DX",
            callsign=callsign,
            frequency_khz=freq_khz,
            mode=mode,
            spotter=m.group("spotter"),
            comment=tagged_comment,
            spotted_at=datetime.now(timezone.utc),
            program=program,
            activity_ref=activity_ref,
            activity_name=activity_name,
            country=country_for_callsign(callsign),
        )
