"""Config loader tests — especially the new multi-cluster support."""

from pathlib import Path

from cwsigfind.config import load_config


def _write(tmp_path: Path, contents: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(contents)
    return p


def test_load_config_with_multiple_clusters(tmp_path):
    cfg_path = _write(
        tmp_path,
        """
callsign = "K1ABC"

[filter]
modes = ["CW"]
bands = []
regions = []
callsign_prefixes = []

[sources.pota]
enabled = true

[sources.wwff]
enabled = true
poll_interval_seconds = 30

[sources.wwbota]
enabled = true
poll_interval_seconds = 60

[[sources.dxcluster]]
name = "NC7J"
host = "dxc.nc7j.com"
port = 7373
login_commands = []

[[sources.dxcluster]]
name = "S50CLX"
host = "s50clx.infrax.si"
port = 41112
login_commands = ["ACC/SPOTS INFO -POTA-"]

[[sources.dxcluster]]
name = "DXMAPS"
enabled = false
host = "dxmaps.com"
port = 7300

[sources.rbn]
enabled = false
host = "telnet.reversebeacon.net"
port = 7000
""",
    )

    cfg = load_config(cfg_path)
    assert cfg.callsign == "K1ABC"
    # Three clusters parsed in order.
    assert [c.label for c in cfg.clusters] == ["NC7J", "S50CLX", "DXMAPS"]
    assert cfg.clusters[0].host == "dxc.nc7j.com"
    assert cfg.clusters[0].port == 7373
    assert cfg.clusters[1].login_commands == ["ACC/SPOTS INFO -POTA-"]
    assert cfg.clusters[2].enabled is False
    # RBN parsed as a single-cluster-style entry, off by default in this fixture.
    assert cfg.rbn is not None
    assert cfg.rbn.host == "telnet.reversebeacon.net"
    assert cfg.rbn.enabled is False
    # WWFF and WWBOTA both populated.
    assert cfg.sources["wwff"].enabled
    assert cfg.sources["wwff"].poll_interval_seconds == 30
    assert cfg.sources["wwbota"].enabled
    assert cfg.sources["wwbota"].poll_interval_seconds == 60


def test_load_config_back_compat_single_cluster_table(tmp_path):
    """A legacy single ``[sources.dxcluster]`` table still works."""
    cfg_path = _write(
        tmp_path,
        """
callsign = "K1ABC"

[sources.dxcluster]
host = "dxc.nc7j.com"
port = 7373
""",
    )
    cfg = load_config(cfg_path)
    assert len(cfg.clusters) == 1
    assert cfg.clusters[0].host == "dxc.nc7j.com"
    assert cfg.clusters[0].enabled is True


def test_load_config_no_clusters(tmp_path):
    cfg_path = _write(tmp_path, """callsign = "N0CALL"
""")
    cfg = load_config(cfg_path)
    assert cfg.clusters == []
    assert cfg.rbn is None
