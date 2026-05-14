"""Verify hamqsl XML parsing + the singleton snapshot accessor."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from cwsigfind import propagation
from cwsigfind.propagation import (
    PropagationSnapshot,
    fetch_and_update,
    get_snapshot,
    parse_solarxml,
)


# Live sample captured 2026-05-14 18:31 GMT — mirrors the canonical structure
# documented at https://www.hamqsl.com/solar.html. Kept here as a fixture so
# tests don't reach out to the network.
SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8" ?>
<solar>
\t<solardata>
\t\t<source url="http://www.hamqsl.com/solar.html">N0NBH</source>
\t\t<updated> 14 May 2026 1833 GMT</updated>
\t\t<solarflux>103</solarflux>
\t\t<aindex> 10</aindex>
\t\t<kindex> 0</kindex>
\t\t<kindexnt>No Report</kindexnt>
\t\t<xray>C4.0</xray>
\t\t<sunspots>52</sunspots>
\t\t<heliumline>102.3</heliumline>
\t\t<protonflux>846</protonflux>
\t\t<electonflux>4210</electonflux>
\t\t<aurora> 1</aurora>
\t\t<normalization>1.99</normalization>
\t\t<latdegree>67.5</latdegree>
\t\t<solarwind>397.9</solarwind>
\t\t<magneticfield>  3.3</magneticfield>
\t\t<calculatedconditions>
\t\t\t<band name="80m-40m" time="day">Good</band>
\t\t\t<band name="30m-20m" time="day">Good</band>
\t\t\t<band name="17m-15m" time="day">Fair</band>
\t\t\t<band name="12m-10m" time="day">Poor</band>
\t\t\t<band name="80m-40m" time="night">Good</band>
\t\t\t<band name="30m-20m" time="night">Good</band>
\t\t\t<band name="17m-15m" time="night">Fair</band>
\t\t\t<band name="12m-10m" time="night">Poor</band>
\t\t</calculatedconditions>
\t\t<calculatedvhfconditions>
\t\t\t<phenomenon name="vhf-aurora" location="northern_hemi">Band Closed</phenomenon>
\t\t\t<phenomenon name="E-Skip" location="europe">Band Closed</phenomenon>
\t\t\t<phenomenon name="E-Skip" location="north_america">Band Closed</phenomenon>
\t\t\t<phenomenon name="E-Skip" location="europe_6m">Band Closed</phenomenon>
\t\t\t<phenomenon name="E-Skip" location="europe_4m">Band Closed</phenomenon>
\t\t</calculatedvhfconditions>
\t\t<geomagfield>INACTIVE</geomagfield>
\t\t<signalnoise>S0-S1</signalnoise>
\t\t<fof2></fof2>
\t\t<muffactor></muffactor>
\t\t<muf>NoRpt</muf>
\t</solardata>
</solar>"""


@pytest.fixture(autouse=True)
def _reset_snapshot():
    """Ensure each test starts with a fresh singleton snapshot."""
    propagation.reset_snapshot_for_tests()
    yield
    propagation.reset_snapshot_for_tests()


def test_parses_all_core_fields():
    snap = parse_solarxml(SAMPLE_XML)
    assert snap.sfi == 103
    assert snap.ssn == 52
    assert snap.a_index == 10
    assert snap.k_index == 0
    assert snap.xray == "C4.0"
    assert snap.helium_line == pytest.approx(102.3)
    assert snap.proton_flux == 846
    assert snap.electron_flux == 4210
    assert snap.aurora == 1
    assert snap.solar_wind == pytest.approx(397.9)
    assert snap.magnetic_field == pytest.approx(3.3)
    assert snap.geomag_field == "INACTIVE"
    assert snap.signal_noise == "S0-S1"
    assert snap.muf == "NoRpt"
    assert snap.source == "hamqsl.com (N0NBH)"


def test_parses_updated_timestamp_as_utc():
    snap = parse_solarxml(SAMPLE_XML)
    assert snap.updated is not None
    assert snap.updated.tzinfo == timezone.utc
    assert snap.updated == datetime(2026, 5, 14, 18, 33, tzinfo=timezone.utc)


def test_parses_hf_band_conditions():
    snap = parse_solarxml(SAMPLE_XML)
    # All four band buckets, both day and night.
    assert set(snap.hf_conditions.keys()) == {
        "80m-40m", "30m-20m", "17m-15m", "12m-10m",
    }
    assert snap.hf_conditions["80m-40m"] == {"day": "Good", "night": "Good"}
    assert snap.hf_conditions["17m-15m"] == {"day": "Fair", "night": "Fair"}
    assert snap.hf_conditions["12m-10m"] == {"day": "Poor", "night": "Poor"}


def test_parses_vhf_phenomena():
    snap = parse_solarxml(SAMPLE_XML)
    assert len(snap.vhf_conditions) == 5
    # Order is preserved from the XML so the UI can display them as-is.
    assert snap.vhf_conditions[0] == {
        "phenomenon": "vhf-aurora",
        "location": "northern_hemi",
        "status": "Band Closed",
    }
    assert snap.vhf_conditions[1]["phenomenon"] == "E-Skip"
    assert snap.vhf_conditions[1]["location"] == "europe"
    assert snap.vhf_conditions[1]["status"] == "Band Closed"


def test_round_trip_to_json_dict():
    """to_json_dict() must include every documented field with no surprises."""
    snap = parse_solarxml(SAMPLE_XML)
    d = snap.to_json_dict()
    assert d["sfi"] == 103
    assert d["ssn"] == 52
    assert d["xray"] == "C4.0"
    assert d["hf_conditions"]["80m-40m"]["day"] == "Good"
    # ISO string for the timestamp, not a raw datetime.
    assert d["updated"] == "2026-05-14T18:33:00+00:00"
    assert d["source"] == "hamqsl.com (N0NBH)"
    assert d["error"] is None
    # All four hamqsl band buckets survived the round-trip.
    for band in ("80m-40m", "30m-20m", "17m-15m", "12m-10m"):
        for tod in ("day", "night"):
            assert band in d["hf_conditions"]
            assert tod in d["hf_conditions"][band]


def test_missing_fields_degrade_gracefully():
    minimal = (
        "<solar><solardata>"
        "<updated>14 May 2026 1833 GMT</updated>"
        "<solarflux>117</solarflux>"
        "</solardata></solar>"
    )
    snap = parse_solarxml(minimal)
    assert snap.sfi == 117
    # Everything else stays None and the structured collections stay empty.
    assert snap.ssn is None
    assert snap.k_index is None
    assert snap.a_index is None
    assert snap.xray is None
    assert snap.hf_conditions == {}
    assert snap.vhf_conditions == []


def test_malformed_xml_raises_parse_error():
    import xml.etree.ElementTree as ET

    with pytest.raises(ET.ParseError):
        parse_solarxml("<solar><solardata><sfi>oops")


def test_unparseable_updated_falls_back_to_none():
    xml = (
        "<solar><solardata>"
        "<updated>not a date</updated>"
        "<solarflux>100</solarflux>"
        "</solardata></solar>"
    )
    snap = parse_solarxml(xml)
    assert snap.updated is None
    assert snap.sfi == 100


def test_non_numeric_fields_stay_none_without_raising():
    xml = (
        "<solar><solardata>"
        "<solarflux>abc</solarflux>"
        "<sunspots></sunspots>"
        "<aindex>--</aindex>"
        "</solardata></solar>"
    )
    snap = parse_solarxml(xml)
    assert snap.sfi is None
    assert snap.ssn is None
    assert snap.a_index is None


class _FakeXmlResponse:
    """Minimal stand-in for httpx.Response for the mocked client below."""

    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=None,  # type: ignore[arg-type]
                response=None,  # type: ignore[arg-type]
            )


class _FakeClient:
    """A tiny replacement for httpx.AsyncClient used by fetch_and_update."""

    def __init__(self, response_or_exc):
        self._payload = response_or_exc

    async def get(self, url: str):  # pragma: no cover - trivial
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload


@pytest.mark.asyncio
async def test_fetch_and_update_populates_singleton():
    client = _FakeClient(_FakeXmlResponse(SAMPLE_XML))
    snap = await fetch_and_update(client, url="ignored")
    assert snap.error is None
    assert snap.sfi == 103
    # The singleton accessor now returns the same fields.
    cached = get_snapshot()
    assert cached.sfi == 103
    assert cached.fetched_at is not None
    assert cached.fetched_at.tzinfo == timezone.utc


@pytest.mark.asyncio
async def test_fetch_and_update_preserves_last_known_on_error():
    # Seed the singleton with a good fetch, then fail and verify fields survive.
    good = _FakeClient(_FakeXmlResponse(SAMPLE_XML))
    await fetch_and_update(good, url="ignored")

    import httpx

    bad = _FakeClient(httpx.ConnectError("boom"))
    snap = await fetch_and_update(bad, url="ignored")
    assert snap.sfi == 103  # last-known values preserved
    assert snap.error is not None
    assert "ConnectError" in snap.error
