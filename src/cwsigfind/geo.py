"""Geographic enrichment for spots: country + state lookup.

Two independent paths:

1. POTA spots arrive with a `locationDesc` like ``"US-AZ"`` or ``"DE-NW"``.
   `parse_location_desc` turns that into ``("USA", "Arizona")`` /
   ``("Germany", "NW")``.

2. DX cluster and RBN spots have no location info; we derive **country** from
   the callsign prefix via a longest-prefix lookup. We don't try to derive
   state from US calls — the digit hasn't reflected call district / state
   since the FCC stopped enforcing it. POTA is the source of truth for state.

The prefix table below is curated, not exhaustive. It covers the common
DXCC entities encountered in everyday operation; missing entries fall back
to ``None`` and the UI just leaves the cell blank. If you want exhaustive
accuracy, swap in `pyhamtools` with a cty.dat — every function here would
keep the same signature.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Country code -> human readable name. POTA's locationDesc uses ISO 3166-1
# alpha-2 codes (with a couple of community-specific exceptions).
# ---------------------------------------------------------------------------

COUNTRY_NAMES: dict[str, str] = {
    "US": "USA", "CA": "Canada", "MX": "Mexico",
    "GB": "UK", "UK": "UK",
    "DE": "Germany", "FR": "France", "IT": "Italy", "ES": "Spain",
    "PT": "Portugal", "NL": "Netherlands", "BE": "Belgium",
    "CH": "Switzerland", "AT": "Austria", "LU": "Luxembourg",
    "SE": "Sweden", "NO": "Norway", "DK": "Denmark", "FI": "Finland",
    "IS": "Iceland", "IE": "Ireland",
    "PL": "Poland", "CZ": "Czechia", "SK": "Slovakia",
    "HU": "Hungary", "RO": "Romania", "BG": "Bulgaria",
    "GR": "Greece", "TR": "Turkey",
    "RU": "Russia", "UA": "Ukraine", "BY": "Belarus",
    "LT": "Lithuania", "LV": "Latvia", "EE": "Estonia",
    "SI": "Slovenia", "HR": "Croatia", "RS": "Serbia",
    "BA": "Bosnia", "MK": "N. Macedonia", "AL": "Albania",
    "MT": "Malta", "CY": "Cyprus", "MD": "Moldova",
    "IL": "Israel", "JO": "Jordan", "LB": "Lebanon",
    "SA": "Saudi Arabia", "AE": "UAE", "QA": "Qatar",
    "EG": "Egypt", "MA": "Morocco", "TN": "Tunisia", "DZ": "Algeria",
    "ZA": "South Africa", "NG": "Nigeria", "KE": "Kenya",
    "JP": "Japan", "CN": "China", "KR": "S. Korea",
    "TW": "Taiwan", "HK": "Hong Kong", "MO": "Macao",
    "SG": "Singapore", "MY": "Malaysia", "TH": "Thailand",
    "ID": "Indonesia", "PH": "Philippines", "VN": "Vietnam",
    "IN": "India", "PK": "Pakistan", "BD": "Bangladesh",
    "AU": "Australia", "NZ": "New Zealand",
    "BR": "Brazil", "AR": "Argentina", "CL": "Chile",
    "PE": "Peru", "CO": "Colombia", "VE": "Venezuela",
    "UY": "Uruguay", "PY": "Paraguay", "BO": "Bolivia",
    "EC": "Ecuador",
    "CR": "Costa Rica", "PA": "Panama", "GT": "Guatemala",
    "HN": "Honduras", "NI": "Nicaragua", "SV": "El Salvador",
    "CU": "Cuba", "DO": "Dominican Rep.", "JM": "Jamaica",
    "PR": "Puerto Rico", "VI": "US Virgin Is.",
    "BS": "Bahamas", "BB": "Barbados", "TT": "Trinidad",
}


# ---------------------------------------------------------------------------
# Subdivision tables for the two countries where POTA's spot volume warrants
# spelling out names. Anything else falls through to the raw subdivision code.
# ---------------------------------------------------------------------------

US_STATES: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana",
    "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "DC": "District of Columbia",
    "PR": "Puerto Rico", "VI": "US Virgin Is.",
    "GU": "Guam", "AS": "American Samoa",
    "MP": "N. Mariana Is.",
}

CA_PROVINCES: dict[str, str] = {
    "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba",
    "NB": "New Brunswick", "NL": "Newfoundland & Labrador",
    "NS": "Nova Scotia", "NT": "Northwest Territories",
    "NU": "Nunavut", "ON": "Ontario", "PE": "Prince Edward Island",
    "QC": "Quebec", "SK": "Saskatchewan", "YT": "Yukon",
}


def country_name(code: str | None) -> str | None:
    """Look up a country name by ISO code; fall back to the raw code."""
    if not code:
        return None
    code = code.upper()
    return COUNTRY_NAMES.get(code, code)


def parse_location_desc(loc: str | None) -> tuple[str | None, str | None]:
    """Turn POTA's locationDesc into (country, state) human-readable strings.

    Handles ``"US-AZ"`` / ``"DE-NW"`` / ``"US"`` / ``"US,CA"`` (multi-state POTA
    parks). For multi-state ``A,B`` we report state ``"A/B"`` and the country
    of the first segment.
    """
    if not loc:
        return None, None
    raw = loc.upper().strip()

    # Multi-state POTA parks: "US-FL,US-GA" → "FL/GA" within USA.
    if "," in raw:
        segments = [s.strip() for s in raw.split(",") if s.strip()]
        countries = []
        states = []
        for seg in segments:
            c, s = parse_location_desc(seg)
            if c and c not in countries:
                countries.append(c)
            if s:
                states.append(s)
        country = countries[0] if countries else None
        # Pick short codes for the joined display if all segments share country.
        if len(countries) == 1:
            short_states = [seg.split("-", 1)[1] for seg in segments if "-" in seg]
            state = "/".join(short_states) if short_states else None
        else:
            state = ", ".join(states) if states else None
        return country, state

    parts = raw.split("-", 1)
    cc = parts[0]
    sub = parts[1] if len(parts) > 1 else None
    country = country_name(cc)
    state: str | None = None
    if sub:
        if cc == "US":
            state = US_STATES.get(sub, sub)
        elif cc == "CA":
            state = CA_PROVINCES.get(sub, sub)
        else:
            state = sub
    return country, state


# ---------------------------------------------------------------------------
# Callsign prefix -> country. Longest prefix wins.
# ---------------------------------------------------------------------------

# We build the index once. Order in source doesn't matter — lookup tries
# longest prefix first.
_PREFIX_TO_COUNTRY: dict[str, str] = {
    # --- USA territories / DXCC subdivisions (3-char prefixes win over US) ---
    "KH0": "Mariana Is.",
    "KH1": "USA", "KH3": "USA", "KH4": "USA", "KH5": "USA", "KH9": "USA",
    "KH2": "Guam", "KH6": "Hawaii", "KH7": "Hawaii",
    "KH8": "American Samoa",
    "KL7": "Alaska", "KL": "Alaska",
    "KP1": "USA", "KP5": "Puerto Rico",
    "KP2": "US Virgin Is.", "KP3": "Puerto Rico", "KP4": "Puerto Rico",
    "NH0": "Mariana Is.", "NH2": "Guam",
    "NH6": "Hawaii", "NH7": "Hawaii", "NH8": "American Samoa",
    "NL7": "Alaska", "NL": "Alaska",
    "NP2": "US Virgin Is.", "NP3": "Puerto Rico", "NP4": "Puerto Rico",
    "WH0": "Mariana Is.", "WH2": "Guam",
    "WH6": "Hawaii", "WH7": "Hawaii", "WH8": "American Samoa",
    "WL7": "Alaska", "WL": "Alaska",
    "WP2": "US Virgin Is.", "WP3": "Puerto Rico", "WP4": "Puerto Rico",
    "AH0": "Mariana Is.", "AH2": "Guam",
    "AH6": "Hawaii", "AH7": "Hawaii", "AH8": "American Samoa",
    "AL7": "Alaska", "AL": "Alaska",
    # --- USA mainland (2-char Extra/Advanced; 1-char K/W/N as fallback) ---
    "AA": "USA", "AB": "USA", "AC": "USA", "AD": "USA",
    "AE": "USA", "AF": "USA", "AG": "USA", "AI": "USA",
    "AJ": "USA", "AK": "USA",
    "K": "USA", "W": "USA", "N": "USA",
    # --- Canada ---
    "VE": "Canada", "VA": "Canada", "VO": "Canada", "VY": "Canada",
    "CY0": "Canada", "CY9": "Canada",
    "XJ": "Canada", "XK": "Canada", "XL": "Canada", "XM": "Canada",
    "XN": "Canada", "XO": "Canada",
    # --- Mexico ---
    "XE": "Mexico", "XF": "Mexico", "4A": "Mexico", "4B": "Mexico",
    "4C": "Mexico", "6D": "Mexico", "6E": "Mexico", "6F": "Mexico",
    "6G": "Mexico", "6H": "Mexico", "6I": "Mexico", "6J": "Mexico",
    # --- UK & Crown Dependencies ---
    "G": "UK", "M": "UK", "2E": "UK", "2I": "UK", "2M": "UK",
    "2U": "UK", "2W": "UK", "2D": "UK",
    "GD": "Isle of Man", "MD": "Isle of Man",
    "GI": "N. Ireland", "MI": "N. Ireland",
    "GJ": "Jersey", "MJ": "Jersey",
    "GM": "Scotland", "MM": "Scotland",
    "GU": "Guernsey", "MU": "Guernsey",
    "GW": "Wales", "MW": "Wales",
    # --- Western Europe ---
    "DL": "Germany", "DA": "Germany", "DB": "Germany", "DC": "Germany",
    "DD": "Germany", "DE": "Germany", "DF": "Germany", "DG": "Germany",
    "DH": "Germany", "DJ": "Germany", "DK": "Germany",
    "DM": "Germany", "DO": "Germany", "DP": "Germany",
    "DQ": "Germany", "DR": "Germany",
    "F": "France", "TM": "France", "TH": "France",
    "I": "Italy", "IK": "Italy", "IZ": "Italy", "IW": "Italy",
    "II": "Italy", "IO": "Italy", "IQ": "Italy", "IR": "Italy",
    "IS": "Sardinia", "IM0": "Sardinia",
    "IT9": "Sicily",
    "EA": "Spain", "EB": "Spain", "EC": "Spain", "ED": "Spain",
    "EE": "Spain", "EF": "Spain", "EG": "Spain", "EH": "Spain",
    "EA6": "Balearic Is.", "EA8": "Canary Is.", "EA9": "Ceuta & Melilla",
    "CT": "Portugal", "CR": "Portugal", "CS": "Portugal",
    "CT3": "Madeira", "CU": "Azores",
    "PA": "Netherlands", "PB": "Netherlands", "PC": "Netherlands",
    "PD": "Netherlands", "PE": "Netherlands", "PF": "Netherlands",
    "PG": "Netherlands", "PH": "Netherlands", "PI": "Netherlands",
    "ON": "Belgium", "OO": "Belgium", "OP": "Belgium",
    "OQ": "Belgium", "OR": "Belgium", "OS": "Belgium", "OT": "Belgium",
    "LX": "Luxembourg",
    "HB": "Switzerland", "HB9": "Switzerland", "HB0": "Liechtenstein",
    "OE": "Austria",
    "EI": "Ireland", "EJ": "Ireland",
    # --- Nordics ---
    "SM": "Sweden", "SA": "Sweden", "SB": "Sweden", "SC": "Sweden",
    "SD": "Sweden", "SE": "Sweden", "SF": "Sweden", "SG": "Sweden",
    "SH": "Sweden", "SI": "Sweden", "SJ": "Sweden", "SK": "Sweden",
    "SL": "Sweden",
    "LA": "Norway", "LB": "Norway", "LC": "Norway", "LD": "Norway",
    "LE": "Norway", "LF": "Norway", "LG": "Norway", "LH": "Norway",
    "LI": "Norway", "LJ": "Norway", "LK": "Norway", "LM": "Norway",
    "LN": "Norway",
    "OZ": "Denmark", "OU": "Denmark", "OV": "Denmark",
    "OW": "Denmark", "OX": "Greenland", "OY": "Faroe Is.",
    "OH": "Finland", "OF": "Finland", "OG": "Finland", "OI": "Finland",
    "OJ": "Finland", "OH0": "Aland Is.",
    "TF": "Iceland",
    # --- Central / Eastern Europe ---
    "SP": "Poland", "SN": "Poland", "SO": "Poland", "SQ": "Poland",
    "SR": "Poland",
    "OK": "Czechia", "OL": "Czechia",
    "OM": "Slovakia",
    "HA": "Hungary", "HG": "Hungary",
    "YO": "Romania", "YP": "Romania", "YQ": "Romania", "YR": "Romania",
    "LZ": "Bulgaria",
    "SV": "Greece", "SX": "Greece", "SY": "Greece", "SZ": "Greece",
    "SV5": "Dodecanese", "SV9": "Crete",
    "TA": "Turkey", "TB": "Turkey", "TC": "Turkey",
    "S5": "Slovenia",
    "9A": "Croatia",
    "YU": "Serbia", "YT": "Serbia", "YZ": "Serbia",
    "Z3": "N. Macedonia",
    "E7": "Bosnia",
    "ZA": "Albania",
    "9H": "Malta",
    "5B": "Cyprus", "C4": "Cyprus", "H2": "Cyprus", "P3": "Cyprus",
    "ER": "Moldova",
    # --- Baltics ---
    "LY": "Lithuania",
    "YL": "Latvia",
    "ES": "Estonia",
    # --- Russia / CIS ---
    "UA": "Russia", "UB": "Russia", "UC": "Russia", "UD": "Russia",
    "UE": "Russia", "UF": "Russia", "UG": "Russia", "UH": "Russia",
    "UI": "Russia",
    "R": "Russia", "RA": "Russia", "RC": "Russia", "RD": "Russia",
    "RE": "Russia", "RF": "Russia", "RG": "Russia", "RH": "Russia",
    "RJ": "Russia", "RK": "Russia", "RL": "Russia", "RM": "Russia",
    "RN": "Russia", "RO": "Russia", "RP": "Russia", "RQ": "Russia",
    "RR": "Russia", "RS": "Russia", "RT": "Russia", "RU": "Russia",
    "RV": "Russia", "RW": "Russia", "RX": "Russia", "RY": "Russia",
    "RZ": "Russia",
    "UR": "Ukraine", "US": "Ukraine", "UT": "Ukraine", "UU": "Ukraine",
    "UV": "Ukraine", "UW": "Ukraine", "UX": "Ukraine", "UY": "Ukraine",
    "UZ": "Ukraine", "EM": "Ukraine", "EN": "Ukraine", "EO": "Ukraine",
    "EU": "Belarus", "EV": "Belarus", "EW": "Belarus",
    # --- Middle East / North Africa ---
    "4X": "Israel", "4Z": "Israel",
    "JY": "Jordan",
    "OD": "Lebanon",
    "HZ": "Saudi Arabia",
    "A4": "Oman", "A5": "Bhutan", "A6": "UAE", "A7": "Qatar", "A9": "Bahrain",
    "EP": "Iran", "EQ": "Iran",
    "YK": "Syria",
    "YI": "Iraq",
    "SU": "Egypt",
    "CN": "Morocco",
    "3V": "Tunisia",
    "7X": "Algeria",
    # --- Sub-Saharan Africa ---
    "ZS": "South Africa", "ZR": "South Africa", "ZT": "South Africa",
    "ZU": "South Africa",
    "5N": "Nigeria",
    "5Z": "Kenya",
    "C9": "Mozambique",
    "Z2": "Zimbabwe",
    # --- East Asia ---
    "JA": "Japan", "JE": "Japan", "JF": "Japan", "JG": "Japan",
    "JH": "Japan", "JI": "Japan", "JJ": "Japan", "JK": "Japan",
    "JL": "Japan", "JM": "Japan", "JN": "Japan", "JO": "Japan",
    "JP": "Japan", "JQ": "Japan", "JR": "Japan", "JS": "Japan",
    "7J": "Japan", "7K": "Japan", "7L": "Japan", "7M": "Japan",
    "7N": "Japan", "8J": "Japan", "8N": "Japan",
    "BA": "China", "BD": "China", "BG": "China", "BH": "China",
    "BI": "China", "BY": "China",
    "BV": "Taiwan", "BU": "Taiwan", "BX": "Taiwan",
    "HL": "S. Korea", "DS": "S. Korea", "6K": "S. Korea", "6L": "S. Korea",
    "VR": "Hong Kong",
    "XX9": "Macao",
    # --- Southeast Asia / South Asia ---
    "9V": "Singapore",
    "9M": "Malaysia", "9W": "Malaysia",
    "HS": "Thailand", "E2": "Thailand",
    "YB": "Indonesia", "YC": "Indonesia", "YD": "Indonesia",
    "YE": "Indonesia", "YF": "Indonesia", "YG": "Indonesia", "YH": "Indonesia",
    "DU": "Philippines", "DV": "Philippines", "DW": "Philippines",
    "DX": "Philippines", "DY": "Philippines", "DZ": "Philippines",
    "XV": "Vietnam", "3W": "Vietnam",
    "VU": "India",
    "AP": "Pakistan",
    "S2": "Bangladesh",
    # --- Oceania ---
    "VK": "Australia", "AX": "Australia",
    "ZL": "New Zealand", "ZM": "New Zealand",
    "VK9": "Australia", "VK0": "Australia",
    # --- South America ---
    "PY": "Brazil", "PP": "Brazil", "PQ": "Brazil", "PR": "Brazil",
    "PS": "Brazil", "PU": "Brazil", "PV": "Brazil", "PW": "Brazil",
    "PX": "Brazil", "ZV": "Brazil", "ZW": "Brazil", "ZX": "Brazil",
    "ZY": "Brazil", "ZZ": "Brazil",
    "LU": "Argentina", "LW": "Argentina", "AY": "Argentina", "AZ": "Argentina",
    "CE": "Chile", "XQ": "Chile", "3G": "Chile",
    "OA": "Peru", "OB": "Peru", "OC": "Peru",
    "HK": "Colombia", "HJ": "Colombia",
    "YV": "Venezuela", "YW": "Venezuela", "YX": "Venezuela", "YY": "Venezuela",
    "CX": "Uruguay",
    "ZP": "Paraguay",
    "CP": "Bolivia",
    "HC": "Ecuador", "HD": "Ecuador",
    # --- Central America / Caribbean ---
    "TI": "Costa Rica", "TE": "Costa Rica",
    "HP": "Panama", "H3": "Panama", "H8": "Panama", "H9": "Panama",
    "TG": "Guatemala", "TD": "Guatemala",
    "HR": "Honduras",
    "YN": "Nicaragua",
    "YS": "El Salvador",
    "CO": "Cuba", "CM": "Cuba", "CL": "Cuba",
    "HI": "Dominican Rep.", "HJ4": "Dominican Rep.",
    "6Y": "Jamaica",
    "C6": "Bahamas",
    "8P": "Barbados",
    "9Y": "Trinidad", "9Z": "Trinidad",
}


def _normalize_callsign(call: str) -> str:
    """Best-effort: strip prefix/suffix indicators from a "/portable" callsign.

    Examples:
      ``K3XYZ/P`` → ``K3XYZ`` (suffix is meaningless for country lookup)
      ``K3XYZ/4`` → ``K3XYZ`` (operating from district 4, but country is USA)
      ``VE3/W1ABC`` → ``VE3``   (location prefix wins; op is in Canada)
      ``KH6/JA1XYZ`` → ``KH6``  (visiting Hawaii)
    """
    if not call:
        return ""
    s = call.upper().strip()
    if "/" not in s:
        return s
    parts = [p for p in s.split("/") if p]
    if not parts:
        return ""
    # A short leading segment (≤ 3 chars, mostly letters/digits) typically
    # indicates the location ("VE3/W1ABC" → I'm in VE3-land).
    common_suffixes = {"P", "M", "MM", "AM", "QRP", "QRPP", "T", "STN"}
    if len(parts) >= 2 and 1 <= len(parts[0]) <= 3 and parts[0] not in common_suffixes:
        # Leading segment is a location prefix.
        if any(ch.isdigit() for ch in parts[0]) or len(parts[0]) >= 2:
            return parts[0]
    return parts[0]


def country_for_callsign(call: str | None) -> str | None:
    """Best-effort country lookup from a callsign using longest-prefix-match."""
    if not call:
        return None
    cs = _normalize_callsign(call)
    if not cs:
        return None
    for plen in range(min(4, len(cs)), 0, -1):
        prefix = cs[:plen]
        if prefix in _PREFIX_TO_COUNTRY:
            return _PREFIX_TO_COUNTRY[prefix]
    return None


def enrich(callsign: str | None, location_desc: str | None) -> tuple[str | None, str | None]:
    """Combined: prefer POTA's locationDesc; fall back to prefix lookup."""
    country, state = parse_location_desc(location_desc)
    if not country and callsign:
        country = country_for_callsign(callsign)
    return country, state


def format_country_state(country: str | None, state: str | None) -> str:
    """Render `country` and `state` for the UI."""
    if country and state:
        return f"{country} · {state}"
    return country or state or ""
