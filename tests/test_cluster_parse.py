import asyncio

import pytest

from cwsigfind.sources.dxcluster import (
    DXClusterSource,
    extract_program_ref,
    infer_mode,
    infer_mode_from_frequency,
)
from cwsigfind.sources.rbn import RBNSource


def _make_dx():
    return DXClusterSource.__new__(DXClusterSource)


def _make_rbn():
    return RBNSource.__new__(RBNSource)


def test_dxcluster_parses_standard_spot():
    s = _make_dx().parse(
        "DX de W1ABC:    14025.0  K3XYZ        CW POTA K-0034              1432Z"
    )
    assert s is not None
    assert s.source == "DX"
    assert s.spotter == "W1ABC"
    assert s.callsign == "K3XYZ"
    assert s.frequency_khz == 14025.0
    assert s.mode == "CW"
    assert "POTA" in (s.comment or "")
    assert s.band == "20m"


def test_dxcluster_normalizes_usb_lsb_to_ssb():
    s = _make_dx().parse(
        "DX de N0XYZ:     7200.0  K1AAA        USB nice signal             0102Z"
    )
    assert s is not None
    assert s.mode == "SSB"


def test_dxcluster_returns_none_on_garbage():
    assert _make_dx().parse("login: ") is None
    assert _make_dx().parse("") is None
    assert _make_dx().parse("Welcome to the cluster!") is None


def test_rbn_parses_skimmer_line():
    s = _make_rbn().parse(
        "DX de W3OA-#:    14025.5  K3XYZ        CW    25 dB  22 WPM  CQ      1432Z"
    )
    assert s is not None
    assert s.source == "RBN"
    assert s.callsign == "K3XYZ"
    assert s.frequency_khz == 14025.5
    assert s.mode == "CW"
    assert "25dB" in (s.comment or "")
    assert "22wpm" in (s.comment or "")


def test_rbn_ignores_non_skimmer_lines():
    # Plain DX cluster format is missing the SNR/WPM bits.
    assert _make_rbn().parse(
        "DX de W1ABC:    14025.0  K3XYZ        CW POTA K-0034              1432Z"
    ) is None


# ---------------------------------------------------------------------------
# Mode inference (the big one — fixes the "DX spots never show under CW filter"
# bug because cluster operators often omit the mode in their comment).
# ---------------------------------------------------------------------------


def test_infer_mode_from_frequency_cw_subbands():
    # Bottom of each HF band is CW by convention.
    assert infer_mode_from_frequency(7025.0) == "CW"
    assert infer_mode_from_frequency(14025.0) == "CW"
    assert infer_mode_from_frequency(21025.0) == "CW"
    assert infer_mode_from_frequency(28025.0) == "CW"
    assert infer_mode_from_frequency(3525.0) == "CW"


def test_infer_mode_from_frequency_ssb_subbands():
    assert infer_mode_from_frequency(7200.0) == "SSB"
    assert infer_mode_from_frequency(14250.0) == "SSB"
    assert infer_mode_from_frequency(21300.0) == "SSB"
    assert infer_mode_from_frequency(28500.0) == "SSB"


def test_infer_mode_from_frequency_ft8_hotspots():
    # FT8 calling frequencies are at known hot-spots inside CW/SSB regions.
    assert infer_mode_from_frequency(14074.0) == "FT8"
    assert infer_mode_from_frequency(14075.5) == "FT8"  # within ±3 kHz
    assert infer_mode_from_frequency(7074.0) == "FT8"
    assert infer_mode_from_frequency(10136.0) == "FT8"


def test_infer_mode_from_frequency_ft4_hotspots():
    assert infer_mode_from_frequency(14080.0) == "FT4"
    assert infer_mode_from_frequency(21140.0) == "FT4"


def test_infer_mode_from_frequency_returns_none_off_band():
    assert infer_mode_from_frequency(9000.0) is None
    assert infer_mode_from_frequency(13000.0) is None


def test_infer_mode_prefers_comment_over_frequency():
    # Comment says SSB but frequency is in the CW sub-band — trust the spotter.
    assert infer_mode("SSB nice signal", 14025.0) == "SSB"


def test_infer_mode_falls_back_to_frequency_when_comment_silent():
    # Spotter said nothing useful — frequency tells us this is CW territory.
    assert infer_mode("POTA K-1234", 14025.0) == "CW"
    assert infer_mode("CQ DX!", 14250.0) == "SSB"
    assert infer_mode(None, 7074.0) == "FT8"


def test_infer_mode_unknown_only_when_nothing_works():
    assert infer_mode("", 9000.0) == "UNKNOWN"
    assert infer_mode(None, 9000.0) == "UNKNOWN"


def test_dxcluster_parse_uses_frequency_when_comment_silent():
    # This is the original symptom: a "naked" POTA-spot from the cluster.
    # Previously this came out as mode=UNKNOWN and got filtered out by [filter].modes = ["CW"].
    s = _make_dx().parse(
        "DX de W1ABC:    14025.0  K3XYZ        POTA K-0034              1432Z"
    )
    assert s is not None
    assert s.mode == "CW"  # inferred from the 20m CW sub-band


# ---------------------------------------------------------------------------
# Program reference extraction (IOTA/SOTA/POTA tags inside cluster comments).
# ---------------------------------------------------------------------------


def test_extract_iota_reference():
    p, r = extract_program_ref("CQ IOTA NA-052 from Cape Cod")
    assert p == "IOTA"
    assert r == "NA-052"


def test_extract_iota_continent_codes():
    for code, sample in [
        ("AF", "AF-001"),
        ("AN", "AN-014"),
        ("AS", "AS-099"),
        ("EU", "EU-005"),
        ("NA", "NA-052"),
        ("OC", "OC-077"),
        ("SA", "SA-031"),
    ]:
        p, r = extract_program_ref(f"working {sample} now")
        assert p == "IOTA"
        assert r == sample


def test_extract_pota_reference():
    p, r = extract_program_ref("CQ POTA K-0034 73")
    assert p == "POTA"
    assert r == "K-0034"


def test_extract_sota_reference():
    p, r = extract_program_ref("SOTA W6/CT-001 summit pileup")
    assert p == "SOTA"
    assert r == "W6/CT-001"


def test_extract_sota_beats_pota():
    # A SOTA ref looks like "W6/CT-001"; the "CT-001" sub-part would also match
    # the POTA regex. The function must return SOTA, not POTA.
    p, r = extract_program_ref("nice activation at W6/CT-001")
    assert p == "SOTA"
    assert r == "W6/CT-001"


def test_extract_no_reference():
    assert extract_program_ref(None) == (None, None)
    assert extract_program_ref("") == (None, None)
    assert extract_program_ref("CQ CQ DE W1AW") == (None, None)


def test_dxcluster_parse_tags_iota_spot():
    s = _make_dx().parse(
        "DX de KP4XYZ:   14025.0  CT3KN        IOTA AF-014                  1230Z"
    )
    assert s is not None
    assert s.program == "IOTA"
    assert s.activity_ref == "AF-014"


def test_dxcluster_parse_tags_pota_spot_from_cluster():
    s = _make_dx().parse(
        "DX de W1ABC:    14025.0  K3XYZ        POTA K-0034              1432Z"
    )
    assert s is not None
    assert s.program == "POTA"
    assert s.activity_ref == "K-0034"


# ---------------------------------------------------------------------------
# Extended program-ref extraction: WWFF, BOTA bunkers, and castle awards.
# ---------------------------------------------------------------------------


def test_extract_wwff_reference():
    p, r = extract_program_ref("CQ WWFF GFF-0123 calling")
    assert p == "WWFF"
    assert r == "GFF-0123"


def test_extract_wwff_three_letter_country_code():
    p, r = extract_program_ref("Activating DLFF-0033")
    assert p == "WWFF"
    assert r == "DLFF-0033"


def test_extract_wwff_beats_pota():
    # WWFF refs (XFF-NNNN) could match the POTA regex (X-NNNN) if WWFF wasn't
    # tried first — the priority order in extract_program_ref must place WWFF
    # above POTA.
    p, r = extract_program_ref("KFF-2432 plus POTA K-1234")
    assert p == "WWFF"
    assert r == "KFF-2432"


def test_extract_bota_bunker_reference():
    p, r = extract_program_ref("On the air from B/G-2453 cqcq")
    assert p == "BOTA"
    assert r == "B/G-2453"


def test_extract_bota_numeric_prefix():
    p, r = extract_program_ref("CQ B/9A-0001 from castle")
    assert p == "BOTA"
    assert r == "B/9A-0001"


def test_extract_wca_castle():
    p, r = extract_program_ref("WCA EA-01234 working hunter")
    assert p == "WCA"
    assert r == "EA-01234"


def test_extract_dci_castle():
    p, r = extract_program_ref("DCI BS-002 working hunter")
    assert p == "WCA"
    assert r == "DCI BS-002"


def test_extract_dfcf_castle():
    p, r = extract_program_ref("DFCF 13-001 calling CQ")
    assert p == "WCA"
    assert r == "DFCF 13-001"


def test_extract_priority_sota_over_bota():
    # SOTA refs are the most specific; they win even when a BOTA-shaped token
    # is also present in the comment.
    p, r = extract_program_ref("W6/CT-001 paired with B/G-0001")
    assert p == "SOTA"
    assert r == "W6/CT-001"


def test_extract_priority_bota_over_pota():
    # B/G-0001 should win over a bare "G-0001"-like token (which wouldn't even
    # match POTA's regex, but this confirms priority ordering).
    p, r = extract_program_ref("B/G-0001 (also visited K-5051)")
    assert p == "BOTA"
    assert r == "B/G-0001"


# ---------------------------------------------------------------------------
# Half-open TCP detection — regression test for the case where RBN connects
# successfully but the socket then silently goes half-open (peer crash, NAT
# eviction, etc.). _read_loop must time out and raise so the supervisor can
# reconnect; without this the source produces zero spots forever.
# ---------------------------------------------------------------------------


class _SilentReader:
    """Asyncio reader stub whose readline() hangs until cancelled."""

    async def readline(self) -> bytes:
        await asyncio.Event().wait()  # never set
        return b""


def test_read_loop_times_out_when_peer_goes_silent():
    src = DXClusterSource.__new__(DXClusterSource)
    src.name = "RBN-test"
    src.read_timeout = 0.05  # tiny timeout so the test runs fast

    async def go():
        await src._read_loop(_SilentReader())

    with pytest.raises(ConnectionError, match="no data for"):
        asyncio.run(go())
