from cwsigfind.sources.sota import SOTASource, _parse_summit_details


def test_parse_summit_details_full():
    name, extra = _parse_summit_details("Bellmunt, 1247m, 4 points")
    assert name == "Bellmunt"
    assert extra == "1247m · 4 pts"


def test_parse_summit_details_single():
    name, extra = _parse_summit_details("Mt Wilson")
    assert name == "Mt Wilson"
    assert extra is None


def test_parse_summit_details_empty():
    assert _parse_summit_details(None) == (None, None)
    assert _parse_summit_details("") == (None, None)


def test_sota_to_spot_full():
    src = SOTASource.__new__(SOTASource)
    raw = {
        "id": 309153,
        "timeStamp": "2026-05-14T16:17:24",
        "callsign": "EA3HIG",
        "associationCode": "EA3",
        "summitCode": "BC-039",
        "activatorCallsign": "EA3HIG",
        "activatorName": "Juanjo",
        "frequency": "7.039",  # MHz, string
        "mode": "cw",
        "summitDetails": "Bellmunt, 1247m, 4 points",
        "comments": "CQ SOTA ",
    }
    s = src._to_spot(raw)
    assert s is not None
    assert s.source == "SOTA"
    assert s.program == "SOTA"
    assert s.callsign == "EA3HIG"
    # MHz -> kHz
    assert s.frequency_khz == 7039.0
    assert s.band == "40m"
    assert s.mode == "CW"
    assert s.activity_ref == "EA3/BC-039"
    assert s.activity_name == "Bellmunt"
    assert s.activity_extra == "1247m · 4 pts"
    assert s.country == "Spain"
    assert s.source_id == "309153"


def test_sota_skips_missing_frequency():
    src = SOTASource.__new__(SOTASource)
    assert src._to_spot({"callsign": "G4XYZ", "frequency": None}) is None
    assert src._to_spot({"callsign": "G4XYZ", "frequency": ""}) is None
    assert src._to_spot({"callsign": "G4XYZ", "frequency": "nope"}) is None


def test_sota_falls_back_to_callsign_for_country_when_assoc_unknown():
    src = SOTASource.__new__(SOTASource)
    raw = {
        "id": 1,
        "timeStamp": "2026-05-14T00:00:00",
        "callsign": "DL1ABC",
        "associationCode": "QQQ",  # Q-prefixes are ITU-reserved Q-signals, never used as callsigns
        "summitCode": "QQ-001",
        "activatorCallsign": "DL1ABC",
        "frequency": "14.061",
        "mode": "CW",
        "summitDetails": "Imaginary Summit",
    }
    s = src._to_spot(raw)
    assert s is not None
    # ZZZ doesn't map → falls back to callsign DL1ABC → Germany
    assert s.country == "Germany"
