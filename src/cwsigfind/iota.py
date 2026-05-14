"""IOTA reference catalog — maps refnos like "EU-005" to human-readable names.

Source: ``https://www.iota-world.org/islands-on-the-air/downloads/`` publishes
a ``groups.json`` file (~290 KB, 1178 groups) updated daily at 00:00 UTC. We
mirror it to ``~/.cwsigfind/iota_groups.json`` so the daemon can run offline
and avoid hitting iota-world.org on every restart.

Refresh policy: weekly. If the cache exists and is < 7 days old, we trust it.
Otherwise we attempt a refresh; if the refresh fails (offline / down) we keep
using the stale cache rather than dropping back to ref-only display.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)

IOTA_GROUPS_URL = (
    "https://www.iota-world.org/islands-on-the-air/downloads/"
    "download-file.html?path=groups.json"
)

# Same dir as the rig config — keeps user-state in one place.
CACHE_DIR = Path.home() / ".cwsigfind"
CACHE_FILE = CACHE_DIR / "iota_groups.json"

# Refresh if cached file is older than this. IOTA itself updates daily but
# the catalog rarely changes in practice; weekly is plenty.
DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 3600

# Module-level lookup. Populated by `init_catalog`; read by `group_name`.
_GROUPS: dict[str, str] = {}


def group_name(ref: str | None) -> str | None:
    """Look up a human-readable name for an IOTA reference like ``"EU-005"``.

    Returns None when:
    - the ref is None / empty,
    - the catalog hasn't been loaded yet,
    - or the ref doesn't exist in the catalog.
    """
    if not ref:
        return None
    return _GROUPS.get(ref.upper())


def loaded() -> bool:
    return bool(_GROUPS)


def _parse_groups(payload: object) -> dict[str, str]:
    """Turn the IOTA groups JSON (a list of dicts) into a flat refno -> name map."""
    if not isinstance(payload, list):
        log.warning("IOTA groups: expected list, got %s", type(payload).__name__)
        return {}
    out: dict[str, str] = {}
    for row in payload:
        if not isinstance(row, dict):
            continue
        ref = row.get("refno")
        name = row.get("name")
        if not ref or not name:
            continue
        out[str(ref).upper()] = str(name).strip()
    return out


def _load_cached() -> dict[str, str] | None:
    """Return the parsed cache if present, else None."""
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return _parse_groups(json.load(f))
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError) as e:
        log.warning("IOTA cache unreadable (%s); will try to refresh", e)
        return None


def _cache_is_fresh(max_age_seconds: float) -> bool:
    try:
        st = CACHE_FILE.stat()
    except FileNotFoundError:
        return False
    return (time.time() - st.st_mtime) < max_age_seconds


def _write_cache(payload: object) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    os.replace(tmp, CACHE_FILE)


async def _download() -> object | None:
    """Fetch the IOTA groups JSON. Returns the parsed payload or None on failure."""
    try:
        import httpx
    except ImportError:
        log.warning("httpx not available; cannot download IOTA catalog")
        return None
    try:
        async with httpx.AsyncClient(
            timeout=30.0,
            headers={
                "User-Agent": "cwsigfind/0.1 (+https://github.com/local)",
                "Accept": "application/json,*/*",
            },
            follow_redirects=True,
        ) as client:
            r = await client.get(IOTA_GROUPS_URL)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        log.warning("IOTA catalog download failed: %s", e)
        return None


async def init_catalog(*, max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS) -> int:
    """Populate the in-memory IOTA group index. Returns count of groups loaded.

    Strategy:
      1. If a cached file exists and is fresh, load from disk only.
      2. Otherwise, try to download a fresh copy and save it.
      3. If the download fails but a stale cache exists, fall back to that
         rather than coming up empty.
    """
    global _GROUPS

    if _cache_is_fresh(max_age_seconds):
        cached = _load_cached()
        if cached:
            _GROUPS = cached
            log.info("IOTA: loaded %d groups from cache", len(_GROUPS))
            return len(_GROUPS)

    payload = await _download()
    if payload is not None:
        try:
            _write_cache(payload)
        except OSError as e:
            log.warning("Could not write IOTA cache: %s", e)
        groups = _parse_groups(payload)
        if groups:
            _GROUPS = groups
            log.info("IOTA: downloaded and cached %d groups", len(_GROUPS))
            return len(_GROUPS)

    # Fall back to a stale cache rather than nothing.
    stale = _load_cached()
    if stale:
        _GROUPS = stale
        log.warning("IOTA: using stale cache (%d groups)", len(_GROUPS))
        return len(_GROUPS)

    log.warning("IOTA: no catalog available; spots will show ref only")
    return 0


def reset_for_test(groups: dict[str, str] | None = None) -> None:
    """Test hook: replace or clear the in-memory catalog."""
    global _GROUPS
    _GROUPS = dict(groups or {})
