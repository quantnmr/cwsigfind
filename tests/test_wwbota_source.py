from cwsigfind.sources.wwbota import WWBOTASource


def _src() -> WWBOTASource:
    return WWBOTASource.__new__(WWBOTASource)


def test_wwbota_to_spot_full():
    raw = {
        "mode": "SSB",
        "spotter": "M8HPI",
        "type": "Live",
        "references": [
            {
                "scheme": "UKBOTA",
                "dxcc": 223,
                "reference": "B/G-0977",
                "name": "ROC Post Fulford Site 1",
                "type": "ROC Bunker",
                "locator": "IO93LW",
                "lat": 53.925523,
                "long": -1.070433,
            }
        ],
        "freq": 7.162,
        "comment": "B/G-0977 WAB SE64 calling CQ",
        "call": "M8HPI",
        "time": "2026-05-14T14:06:06.020981Z",
    }
    s = _src()._to_spot(raw)
    assert s is not None
    assert s.source == "BOTA"
    assert s.program == "BOTA"
    assert s.callsign == "M8HPI"
    # MHz -> kHz.
    assert s.frequency_khz == 7162.0
    assert s.band == "40m"
    assert s.mode == "SSB"
    assert s.activity_ref == "B/G-0977"
    assert s.activity_name == "ROC Post Fulford Site 1"
    assert s.latitude == 53.925523
    assert s.longitude == -1.070433
    # UK callsign prefix → UK country fallback (no QRT tag because type=Live).
    assert s.country == "UK"
    assert s.comment == "B/G-0977 WAB SE64 calling CQ"


def test_wwbota_qrt_is_surfaced_with_tag():
    raw = {
        "mode": "SSB",
        "spotter": "M7VDP",
        "type": "QRT",
        "references": [{"reference": "B/G-0748", "name": "ROC Post Harlaston"}],
        "freq": 7.097,
        "comment": "B/G-0748 QRT",
        "call": "M7VDP",
        "time": "2026-05-14T13:17:29.920573Z",
    }
    s = _src()._to_spot(raw)
    assert s is not None
    # QRT spots still surface, with the type prepended so the UI can call them out.
    assert s.comment is not None
    assert s.comment.startswith("[QRT]")


def test_wwbota_skips_missing_frequency():
    src = _src()
    base = {
        "call": "M0XYZ",
        "mode": "CW",
        "time": "2026-05-14T00:00:00Z",
        "references": [],
        "comment": "",
    }
    assert src._to_spot({**base, "freq": None}) is None
    assert src._to_spot({**base, "freq": ""}) is None


def test_wwbota_skips_missing_callsign():
    src = _src()
    raw = {
        "freq": 14.025,
        "mode": "CW",
        "call": "",
        "time": "2026-05-14T00:00:00Z",
        "references": [],
        "comment": "",
    }
    assert src._to_spot(raw) is None


def test_wwbota_ref_falls_back_to_comment_when_references_empty():
    raw = {
        "mode": "CW",
        "spotter": "K1ABC",
        "type": "Live",
        "references": [],
        "freq": 14.025,
        "comment": "B/DL-0033 working POTA",
        "call": "DL1XYZ",
        "time": "2026-05-14T00:00:00Z",
    }
    s = _src()._to_spot(raw)
    assert s is not None
    assert s.activity_ref == "B/DL-0033"
    assert s.country == "Germany"
