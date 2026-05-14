from datetime import datetime, timezone

from cwsigfind.spot import Spot, freq_to_band


def test_freq_to_band_known():
    assert freq_to_band(7025.0) == "40m"
    assert freq_to_band(14025.5) == "20m"
    assert freq_to_band(10125.0) == "30m"
    assert freq_to_band(28100.0) == "10m"
    assert freq_to_band(50125.0) == "6m"


def test_freq_to_band_out_of_band():
    assert freq_to_band(5000.0) is None
    assert freq_to_band(13999.9) is None


def test_dedup_key_uses_source_id():
    s = Spot(
        source="POTA",
        callsign="W1ABC",
        frequency_khz=14025.0,
        mode="CW",
        source_id="12345",
    )
    assert s.dedup_key() == "POTA:12345"


def test_dedup_key_buckets_by_minute_when_no_id():
    t = datetime(2026, 5, 14, 14, 32, 17, tzinfo=timezone.utc)
    s = Spot(
        source="DX",
        callsign="K3XYZ",
        frequency_khz=14025.4,
        mode="CW",
        spotted_at=t,
    )
    # Rounded freq is 14025, minute bucket is 14:32:00.
    assert s.dedup_key() == "DX:K3XYZ:14025:2026-05-14T14:32:00+00:00"
