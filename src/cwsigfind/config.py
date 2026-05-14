"""TOML config loader. See config.example.toml for the schema.

Source sections accept either a single table (``[sources.xxx]``) or an array of
tables (``[[sources.xxx]]``). The cluster sources in particular use the array
form because most operators connect to multiple DX cluster nodes for
redundancy and complementary coverage.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .filters import SpotFilter


@dataclass
class SourceConfig:
    """Generic source config for the polling-style sources (POTA/SOTA/WWFF/WWBOTA)."""

    enabled: bool = True
    poll_interval_seconds: float = 30.0
    request_timeout_seconds: float = 20.0


@dataclass
class ClusterConfig:
    """One DX-cluster connection. Multiple instances allowed in TOML.

    The ``label`` is the operator-visible name for this cluster (e.g. "NC7J",
    "S50CLX", "DXMAPS"). It's used in supervisor task names and prefixed onto
    the spot's comment so the UI shows which cluster surfaced a spot. The
    ``source`` field on the resulting Spot is always ``"DX"`` so the UI
    source-chip filter stays simple.
    """

    label: str
    enabled: bool = True
    host: str = ""
    port: int = 7373
    login_callsign: str | None = None  # Falls back to top-level `callsign` if unset.
    auto_reconnect_seconds: float = 5.0
    login_commands: list[str] = field(default_factory=list)


@dataclass
class WebConfig:
    host: str = "127.0.0.1"
    port: int = 8765


@dataclass
class RigSettings:
    """Behaviour knobs for the rig controller."""

    auto_connect: bool = True


@dataclass
class PropagationConfig:
    """Settings for the space-weather indices panel.

    ``source`` is currently always ``"hamqsl"``; carrying it explicitly leaves
    room for an alternate provider later without changing the schema.
    """

    enabled: bool = True
    source: str = "hamqsl"
    poll_interval_s: float = 900.0  # 15 minutes


@dataclass
class Config:
    callsign: str = "N0CALL"
    spot_filter: SpotFilter = field(default_factory=SpotFilter)
    # Singleton-style polling/streaming sources, keyed by source name
    # (``pota``, ``sota``, ``rbn``, ``wwff``, ``wwbota``).
    sources: dict[str, SourceConfig] = field(default_factory=dict)
    # Per-host telnet config: RBN and the (one-or-many) DX cluster nodes.
    rbn: ClusterConfig | None = None
    clusters: list[ClusterConfig] = field(default_factory=list)
    web: WebConfig = field(default_factory=WebConfig)
    rig: RigSettings = field(default_factory=RigSettings)
    propagation: PropagationConfig = field(default_factory=PropagationConfig)


def _parse_source(raw: dict | None) -> SourceConfig:
    raw = raw or {}
    return SourceConfig(
        enabled=bool(raw.get("enabled", True)),
        poll_interval_seconds=float(
            raw.get("poll_interval_seconds", raw.get("poll_interval_s", 30.0))
        ),
        request_timeout_seconds=float(
            raw.get("request_timeout_seconds", raw.get("request_timeout_s", 20.0))
        ),
    )


def _parse_cluster(raw: dict, *, default_label: str) -> ClusterConfig:
    return ClusterConfig(
        label=str(raw.get("name") or raw.get("label") or default_label),
        enabled=bool(raw.get("enabled", True)),
        host=str(raw.get("host") or ""),
        port=int(raw.get("port") or 7373),
        login_callsign=raw.get("login_callsign"),
        auto_reconnect_seconds=float(
            raw.get("auto_reconnect_seconds", raw.get("auto_reconnect_s", 5.0))
        ),
        login_commands=list(raw.get("login_commands") or []),
    )


def _parse_clusters(raw_sources: dict) -> list[ClusterConfig]:
    """Accept ``[sources.dxcluster]`` (single) or ``[[sources.dxcluster]]`` (list).

    For backwards compatibility, a single table with no explicit ``name`` keeps
    a generic label so existing configs continue to work unchanged.
    """
    entry = raw_sources.get("dxcluster")
    if entry is None:
        return []
    if isinstance(entry, dict):
        if not entry.get("host"):
            return []
        return [_parse_cluster(entry, default_label=entry.get("name") or "DXC")]
    if isinstance(entry, list):
        out: list[ClusterConfig] = []
        for i, item in enumerate(entry):
            if not isinstance(item, dict) or not item.get("host"):
                continue
            out.append(_parse_cluster(item, default_label=f"DXC{i+1}"))
        return out
    return []


def load_config(path: Path) -> Config:
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    f_raw = raw.get("filter", {}) or {}
    spot_filter = SpotFilter(
        modes={str(m).upper() for m in f_raw.get("modes", [])},
        bands=set(f_raw.get("bands", [])),
        regions=[re.compile(p, re.IGNORECASE) for p in f_raw.get("regions", [])],
        callsign_prefixes={str(p).upper() for p in f_raw.get("callsign_prefixes", [])},
    )

    sources_raw = raw.get("sources") or {}

    pota = _parse_source(sources_raw.get("pota"))
    sota = _parse_source(sources_raw.get("sota"))
    wwff = _parse_source(sources_raw.get("wwff"))
    wwbota_raw = sources_raw.get("wwbota")
    if wwbota_raw is None:
        # WWBOTA is wired in but starts disabled by default if not configured.
        wwbota = SourceConfig(enabled=False, poll_interval_seconds=60.0)
    else:
        wwbota = _parse_source(wwbota_raw)

    sources: dict[str, SourceConfig] = {
        "pota": pota,
        "sota": sota,
        "wwff": wwff,
        "wwbota": wwbota,
    }

    rbn_raw = sources_raw.get("rbn")
    rbn = _parse_cluster(rbn_raw, default_label="RBN") if isinstance(rbn_raw, dict) else None

    clusters = _parse_clusters(sources_raw)

    w = raw.get("web", {}) or {}
    web = WebConfig(
        host=str(w.get("host", "127.0.0.1")),
        port=int(w.get("port", 8765)),
    )

    r = raw.get("rig", {}) or {}
    rig = RigSettings(auto_connect=bool(r.get("auto_connect", True)))

    p = raw.get("propagation", {}) or {}
    propagation = PropagationConfig(
        enabled=bool(p.get("enabled", True)),
        source=str(p.get("source", "hamqsl")),
        # Floor is enforced again in propagation.run_loop, but we also clamp
        # here so misconfigured TOMLs don't silently get rounded up later.
        poll_interval_s=max(
            300.0,
            float(p.get("poll_interval_s", p.get("poll_interval_seconds", 900.0))),
        ),
    )

    return Config(
        callsign=str(raw.get("callsign", "N0CALL")),
        spot_filter=spot_filter,
        sources=sources,
        rbn=rbn,
        clusters=clusters,
        web=web,
        rig=rig,
        propagation=propagation,
    )
