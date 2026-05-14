from cwsigfind import iota


SAMPLE_PAYLOAD = [
    {"refno": "AF-001", "name": "Agalega Islands", "dxcc_num": "4"},
    {"refno": "EU-005", "name": "Great Britain", "dxcc_num": "223"},
    {"refno": "NA-052", "name": "USA - Maine State East Group", "dxcc_num": "291"},
    {"refno": "", "name": "Bad row, skipped"},
    {"name": "Bad row, no refno"},
    "not a dict, ignored",
]


def test_parse_groups_extracts_refno_to_name_map():
    out = iota._parse_groups(SAMPLE_PAYLOAD)
    assert out == {
        "AF-001": "Agalega Islands",
        "EU-005": "Great Britain",
        "NA-052": "USA - Maine State East Group",
    }


def test_parse_groups_handles_non_list():
    assert iota._parse_groups(None) == {}
    assert iota._parse_groups({"groups": []}) == {}
    assert iota._parse_groups("nope") == {}


def test_group_name_returns_none_without_load(monkeypatch):
    iota.reset_for_test({})
    assert iota.group_name("EU-005") is None


def test_group_name_lookup_after_load():
    iota.reset_for_test({
        "EU-005": "Great Britain",
        "NA-052": "USA - Maine State East Group",
    })
    try:
        assert iota.group_name("EU-005") == "Great Britain"
        assert iota.group_name("eu-005") == "Great Britain"  # case insensitive
        assert iota.group_name("NA-052") == "USA - Maine State East Group"
        assert iota.group_name("XX-999") is None
        assert iota.group_name(None) is None
        assert iota.group_name("") is None
    finally:
        iota.reset_for_test({})


def test_dxcluster_iota_spot_gets_island_name_when_catalog_loaded():
    """End-to-end: a cluster spot tagged IOTA picks up the group name."""
    from cwsigfind.sources.dxcluster import DXClusterSource

    iota.reset_for_test({"EU-005": "Great Britain"})
    try:
        s = DXClusterSource.__new__(DXClusterSource).parse(
            "DX de M0XYZ:    14025.0  G0ABC        IOTA EU-005                  1230Z"
        )
        assert s is not None
        assert s.program == "IOTA"
        assert s.activity_ref == "EU-005"
        assert s.activity_name == "Great Britain"
    finally:
        iota.reset_for_test({})


def test_dxcluster_iota_spot_without_catalog_still_works():
    """If the catalog never loaded (offline first run), the ref is still tagged."""
    from cwsigfind.sources.dxcluster import DXClusterSource

    iota.reset_for_test({})  # empty catalog
    s = DXClusterSource.__new__(DXClusterSource).parse(
        "DX de M0XYZ:    14025.0  G0ABC        IOTA EU-005                  1230Z"
    )
    assert s is not None
    assert s.program == "IOTA"
    assert s.activity_ref == "EU-005"
    assert s.activity_name is None  # graceful: no name, just the ref
