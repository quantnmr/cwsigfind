from cwsigfind.sources.pota import POTASource


def test_pota_to_spot_full():
    src = POTASource.__new__(POTASource)  # bypass __init__; we only test _to_spot
    raw = {
        "spotId": 9876543,
        "activator": "w1abc",
        "frequency": "14025.5",
        "mode": "CW",
        "reference": "K-0034",
        "parkName": "Grand Canyon National Park",
        "name": "Grand Canyon NP",
        "locationDesc": "US-AZ",
        "grid4": "DM35",
        "grid6": "DM35vv",
        "latitude": 36.05,
        "longitude": -112.14,
        "spotter": "KC1XYZ",
        "comments": "CQ POTA",
        "spotTime": "2026-05-14T14:32:17Z",
    }
    s = src._to_spot(raw)
    assert s is not None
    assert s.source == "POTA"
    assert s.callsign == "W1ABC"
    assert s.frequency_khz == 14025.5
    assert s.mode == "CW"
    assert s.program == "POTA"
    assert s.activity_ref == "K-0034"
    assert s.activity_name == "Grand Canyon National Park"
    assert s.location_desc == "US-AZ"
    assert s.grid == "DM35vv"
    assert s.latitude == 36.05
    assert s.longitude == -112.14
    assert s.source_id == "9876543"
    assert s.band == "20m"


def test_pota_to_spot_skips_missing_frequency():
    src = POTASource.__new__(POTASource)
    assert src._to_spot({"activator": "W1ABC", "frequency": None}) is None
    assert src._to_spot({"activator": "W1ABC", "frequency": ""}) is None
    assert src._to_spot({"activator": "W1ABC", "frequency": "not-a-number"}) is None


def test_pota_to_spot_skips_missing_callsign():
    src = POTASource.__new__(POTASource)
    assert src._to_spot({"frequency": "14025", "activator": ""}) is None


def test_pota_parse_time_handles_naive_iso():
    src = POTASource.__new__(POTASource)
    s = src._to_spot(
        {
            "activator": "W1ABC",
            "frequency": "7025",
            "mode": "CW",
            "spotTime": "2026-05-14T14:32:17",
        }
    )
    assert s is not None
    assert s.spotted_at.tzinfo is not None
