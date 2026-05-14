from cwsigfind.geo import (
    country_for_callsign,
    enrich,
    format_country_state,
    parse_location_desc,
)


# ---- parse_location_desc ----------------------------------------------------


def test_parse_location_desc_us_state():
    assert parse_location_desc("US-AZ") == ("USA", "Arizona")
    assert parse_location_desc("US-CA") == ("USA", "California")
    assert parse_location_desc("US-NY") == ("USA", "New York")


def test_parse_location_desc_canada_province():
    assert parse_location_desc("CA-ON") == ("Canada", "Ontario")
    assert parse_location_desc("CA-BC") == ("Canada", "British Columbia")


def test_parse_location_desc_other_country_keeps_raw_sub():
    # We don't have a German state map; show raw "NW" rather than nothing.
    assert parse_location_desc("DE-NW") == ("Germany", "NW")
    assert parse_location_desc("FR-IDF") == ("France", "IDF")


def test_parse_location_desc_country_only():
    assert parse_location_desc("DE") == ("Germany", None)
    assert parse_location_desc("JP") == ("Japan", None)


def test_parse_location_desc_unknown_country_passes_through():
    assert parse_location_desc("XZ-99") == ("XZ", "99")


def test_parse_location_desc_empty():
    assert parse_location_desc(None) == (None, None)
    assert parse_location_desc("") == (None, None)


def test_parse_location_desc_multistate_park():
    # POTA parks that straddle state lines.
    country, state = parse_location_desc("US-VA,US-NC")
    assert country == "USA"
    assert state in ("VA/NC", "NC/VA")  # order shouldn't matter to the user


# ---- country_for_callsign ---------------------------------------------------


def test_country_from_us_callsigns():
    assert country_for_callsign("K3XYZ") == "USA"
    assert country_for_callsign("W1AW") == "USA"
    assert country_for_callsign("N0CALL") == "USA"
    assert country_for_callsign("AA9XYZ") == "USA"
    assert country_for_callsign("AK4ZZ") == "USA"  # mainland Extra


def test_country_for_us_territory_prefixes_beats_K():
    # KH6 / KL7 / KP4 etc. must win over plain "K" thanks to longest-prefix-match.
    assert country_for_callsign("KH6XYZ") == "Hawaii"
    assert country_for_callsign("KL7ABC") == "Alaska"
    assert country_for_callsign("KP4DEF") == "Puerto Rico"
    assert country_for_callsign("KP2GHI") == "US Virgin Is."
    assert country_for_callsign("AH6JKL") == "Hawaii"


def test_country_from_european_callsigns():
    assert country_for_callsign("DL1ABC") == "Germany"
    assert country_for_callsign("F5XYZ") == "France"
    assert country_for_callsign("G0ABC") == "UK"
    assert country_for_callsign("GM4ABC") == "Scotland"
    assert country_for_callsign("EA3XYZ") == "Spain"
    assert country_for_callsign("OE1ABC") == "Austria"
    assert country_for_callsign("HB9XYZ") == "Switzerland"


def test_country_from_canadian_callsign():
    assert country_for_callsign("VE3ABC") == "Canada"
    assert country_for_callsign("VA7XYZ") == "Canada"


def test_country_from_japan_china_oceania():
    assert country_for_callsign("JA1XYZ") == "Japan"
    assert country_for_callsign("BG2ABC") == "China"
    assert country_for_callsign("VK3DEF") == "Australia"
    assert country_for_callsign("ZL1ABC") == "New Zealand"


def test_country_from_south_america():
    assert country_for_callsign("PY2XYZ") == "Brazil"
    assert country_for_callsign("LU1ABC") == "Argentina"
    assert country_for_callsign("CE3XYZ") == "Chile"


def test_country_for_callsign_with_portable_suffix():
    # "/P" / "/M" / "/4" are operating-status indicators, not country prefixes.
    assert country_for_callsign("K3XYZ/P") == "USA"
    assert country_for_callsign("K3XYZ/4") == "USA"
    assert country_for_callsign("DL1ABC/QRP") == "Germany"


def test_country_for_callsign_with_location_prefix():
    # "VE3/W1ABC" means a US op currently in Ontario — country should be Canada.
    assert country_for_callsign("VE3/W1ABC") == "Canada"
    # "KH6/JA1XYZ" → visiting Hawaii.
    assert country_for_callsign("KH6/JA1XYZ") == "Hawaii"


def test_country_for_unknown_callsign_returns_none():
    # QA-QZ is reserved in the ITU allocation table for Q-signals, never used
    # for callsign prefixes — a safe "definitely unallocated" choice.
    assert country_for_callsign("QA1TEST") is None
    assert country_for_callsign("") is None
    assert country_for_callsign(None) is None


# ---- enrich + format --------------------------------------------------------


def test_enrich_prefers_pota_location_desc_for_country():
    # Even if the callsign would map to a different country, POTA's
    # locationDesc is authoritative because the operator picked it.
    country, state = enrich("DL1ABC", "US-AZ")
    assert country == "USA"
    assert state == "Arizona"


def test_enrich_falls_back_to_callsign_when_no_location_desc():
    country, state = enrich("DL1ABC", None)
    assert country == "Germany"
    assert state is None


def test_enrich_returns_none_when_nothing_works():
    assert enrich(None, None) == (None, None)


def test_format_country_state():
    assert format_country_state("USA", "Arizona") == "USA · Arizona"
    assert format_country_state("Germany", None) == "Germany"
    assert format_country_state(None, "Arizona") == "Arizona"
    assert format_country_state(None, None) == ""
