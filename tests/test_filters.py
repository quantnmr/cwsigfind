import re

from cwsigfind.filters import SpotFilter
from cwsigfind.spot import Spot


def _spot(**overrides):
    base = dict(source="POTA", callsign="W1ABC", frequency_khz=14025.0, mode="CW")
    base.update(overrides)
    return Spot(**base)


def test_empty_filter_matches_everything():
    f = SpotFilter()
    assert f.matches(_spot())
    assert f.matches(_spot(mode="FT8"))


def test_mode_filter():
    f = SpotFilter(modes={"CW"})
    assert f.matches(_spot(mode="CW"))
    assert not f.matches(_spot(mode="FT8"))
    assert f.matches(_spot(mode="cw"))  # case insensitive normalization happens in Spot


def test_band_filter():
    f = SpotFilter(bands={"40m", "20m"})
    assert f.matches(_spot(frequency_khz=7025.0))
    assert f.matches(_spot(frequency_khz=14025.0))
    assert not f.matches(_spot(frequency_khz=28010.0))


def test_region_filter():
    f = SpotFilter(regions=[re.compile(r"^US-", re.IGNORECASE)])
    assert f.matches(_spot(location_desc="US-AZ"))
    assert not f.matches(_spot(location_desc="CA-ON"))
    assert not f.matches(_spot(location_desc=None))


def test_callsign_prefix_filter():
    f = SpotFilter(callsign_prefixes={"W", "K"})
    assert f.matches(_spot(callsign="W1ABC"))
    assert f.matches(_spot(callsign="K3XYZ"))
    assert not f.matches(_spot(callsign="DL1ABC"))


def test_filters_compose_with_AND():
    f = SpotFilter(modes={"CW"}, bands={"20m"})
    assert f.matches(_spot(mode="CW", frequency_khz=14025.0))
    assert not f.matches(_spot(mode="SSB", frequency_khz=14025.0))
    assert not f.matches(_spot(mode="CW", frequency_khz=7025.0))
