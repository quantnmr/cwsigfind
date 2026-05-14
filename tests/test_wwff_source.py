from cwsigfind.sources.wwff import WWFFSource


def _src() -> WWFFSource:
    return WWFFSource.__new__(WWFFSource)


def test_wwff_to_spot_full():
    raw = {
        "id": 98874,
        "activator": "WB8MIW",
        "frequency_khz": 7046.0,
        "mode": "CW",
        "reference": "KFF-2482",
        "reference_name": "Fort Snelling",
        "remarks": "Re-spotted via RBN",
        "spotter": "WE9V",
        "latitude": 44.86604,
        "longitude": -93.19118,
        "spot_time": 1778777319,
        "spot_time_formatted": "2026-05-14 16:48:39",
    }
    s = _src()._to_spot(raw)
    assert s is not None
    assert s.source == "WWFF"
    assert s.program == "WWFF"
    assert s.callsign == "WB8MIW"
    assert s.frequency_khz == 7046.0
    assert s.band == "40m"
    assert s.mode == "CW"
    assert s.activity_ref == "KFF-2482"
    assert s.activity_name == "Fort Snelling"
    assert s.latitude == 44.86604
    assert s.longitude == -93.19118
    assert s.source_id == "98874"
    # Country falls back to callsign-prefix lookup.
    assert s.country == "USA"
    # Timestamp comes from unix `spot_time`, not wall clock.
    assert s.spotted_at.tzinfo is not None
    assert s.spotted_at.year == 2026


def test_wwff_skips_missing_frequency():
    src = _src()
    assert src._to_spot({"activator": "K1ABC", "frequency_khz": None}) is None
    assert src._to_spot({"activator": "K1ABC", "frequency_khz": ""}) is None
    assert src._to_spot({"activator": "K1ABC", "frequency_khz": "not-a-number"}) is None


def test_wwff_skips_missing_callsign():
    assert _src()._to_spot({"frequency_khz": 14025.0, "activator": ""}) is None


def test_wwff_country_fallback_from_callsign():
    # WWFF doesn't surface country directly — we lean on the prefix table.
    s = _src()._to_spot(
        {
            "id": 1,
            "activator": "DL1ABC",
            "frequency_khz": 14025.0,
            "mode": "CW",
            "reference": "DLFF-0033",
            "reference_name": "Some German Park",
            "spot_time": 1778777319,
        }
    )
    assert s is not None
    assert s.country == "Germany"


def test_wwff_handles_malformed_timestamp():
    s = _src()._to_spot(
        {
            "id": 2,
            "activator": "K1ABC",
            "frequency_khz": 14025.0,
            "mode": "CW",
            "reference": "KFF-0001",
            "spot_time": "garbage",
        }
    )
    assert s is not None
    # Falls back to "now" so spots aren't dropped just because the API misbehaves.
    assert s.spotted_at is not None
    assert s.spotted_at.tzinfo is not None


def test_wwff_handles_empty_remarks():
    s = _src()._to_spot(
        {
            "id": 3,
            "activator": "K1ABC",
            "frequency_khz": 14025.0,
            "mode": "CW",
            "reference": "KFF-0001",
            "remarks": "",
            "spot_time": 1778777319,
        }
    )
    assert s is not None
    assert s.comment is None
