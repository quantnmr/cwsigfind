"""CLI entrypoint: wire up sources, bus, store, and the web UI; run until SIGINT."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from pathlib import Path

import uvicorn

from .bus import SpotBus
from .config import Config, load_config
from . import iota
from .rig import RigController, load_saved_rig
from .sources.base import SpotSource
from .sources.dxcluster import DXClusterSource
from .sources.pota import POTASource
from .sources.rbn import RBNSource
from .sources.sota import SOTASource
from .sources.wwbota import WWBOTASource
from .sources.wwff import WWFFSource
from .store import SpotStore
from .web.app import create_app

log = logging.getLogger("cwsigfind")


def _build_sources(cfg: Config, bus: SpotBus, store: SpotStore) -> list[SpotSource]:
    sources: list[SpotSource] = []

    pota = cfg.sources.get("pota")
    if pota and pota.enabled:
        sources.append(POTASource(bus, store, poll_interval=pota.poll_interval_seconds))

    sota = cfg.sources.get("sota")
    if sota and sota.enabled:
        sources.append(SOTASource(bus, store, poll_interval=sota.poll_interval_seconds))

    wwff = cfg.sources.get("wwff")
    if wwff and wwff.enabled:
        sources.append(
            WWFFSource(
                bus,
                store,
                poll_interval=wwff.poll_interval_seconds,
                request_timeout=wwff.request_timeout_seconds,
            )
        )

    wwbota = cfg.sources.get("wwbota")
    if wwbota and wwbota.enabled:
        sources.append(
            WWBOTASource(
                bus,
                store,
                poll_interval=wwbota.poll_interval_seconds,
                request_timeout=wwbota.request_timeout_seconds,
            )
        )

    for cluster in cfg.clusters:
        if not cluster.enabled or not cluster.host:
            continue
        sources.append(
            DXClusterSource(
                bus,
                store,
                host=cluster.host,
                port=cluster.port,
                callsign=cluster.login_callsign or cfg.callsign,
                login_commands=cluster.login_commands,
                label=cluster.label,
            )
        )

    rbn = cfg.rbn
    if rbn and rbn.enabled and rbn.host:
        sources.append(
            RBNSource(
                bus,
                store,
                host=rbn.host,
                port=rbn.port or 7000,
                callsign=rbn.login_callsign or cfg.callsign,
                login_commands=rbn.login_commands,
            )
        )

    return sources


async def amain(config_path: Path) -> None:
    cfg = load_config(config_path)

    # IOTA reference catalog: lazy, weekly-refreshed, cached to ~/.cwsigfind.
    # We do this in the background so a cold start doesn't block on a network
    # round-trip to iota-world.org if it's slow or down.
    iota_task = asyncio.create_task(iota.init_catalog(), name="iota-init")

    bus = SpotBus()
    store = SpotStore()
    sources = _build_sources(cfg, bus, store)

    if not sources:
        log.error("No sources enabled in config — nothing to do.")
        return

    for s in sources:
        s.start()
        log.info("Started source: %s", s.name)

    rig = RigController()
    saved = load_saved_rig()
    rig_task: asyncio.Task[None] | None = None
    if saved is not None and cfg.rig.auto_connect:
        log.info(
            "Auto-reconnecting saved rig (model #%d, port=%s) in background...",
            saved.model_id, saved.port,
        )

        async def _try_auto_connect() -> None:
            try:
                st = await rig.connect(
                    model_id=saved.model_id,
                    port=saved.port,
                    baud=saved.baud,
                    listen_port=saved.listen_port,
                    persist=False,
                )
                if st.state == "connected":
                    log.info(
                        "Rig auto-reconnect succeeded: %s @ %.3f kHz %s",
                        st.model_name, st.freq_khz or 0.0, st.mode or "",
                    )
                else:
                    log.warning("Rig auto-reconnect failed: %s", st.error)
            except Exception:
                log.exception("Rig auto-reconnect crashed")

        rig_task = asyncio.create_task(_try_auto_connect(), name="rig-auto-connect")
    elif saved is not None:
        log.info(
            "Saved rig (model #%d, port=%s) found; auto_connect is off, "
            "Radio drawer will pre-fill the form.",
            saved.model_id, saved.port,
        )

    app = create_app(bus, store, cfg.spot_filter, rig)
    uvi_cfg = uvicorn.Config(
        app, host=cfg.web.host, port=cfg.web.port, log_level="info", access_log=False
    )
    server = uvicorn.Server(uvi_cfg)

    log.info("Web UI: http://%s:%d", cfg.web.host, cfg.web.port)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            # Windows or non-main-thread fallback.
            pass

    server_task = asyncio.create_task(server.serve(), name="uvicorn")

    try:
        await stop.wait()
    finally:
        log.info("Shutting down...")
        server.should_exit = True
        for s in sources:
            await s.stop()
        if rig_task is not None and not rig_task.done():
            rig_task.cancel()
            try:
                await rig_task
            except (asyncio.CancelledError, Exception):
                pass
        if not iota_task.done():
            iota_task.cancel()
            try:
                await iota_task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            await rig.disconnect()
        except Exception:
            log.exception("Error disconnecting rig on shutdown")
        await server_task


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="cwsigfind",
        description="Live ham radio CW spot finder: POTA, DX cluster, RBN.",
    )
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        default=Path("config.toml"),
        help="Path to TOML config (defaults to config.toml, falls back to config.example.toml).",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    cfg_path: Path = args.config
    if not cfg_path.exists():
        fallback = Path("config.example.toml")
        if fallback.exists():
            log.warning("%s not found; using %s", cfg_path, fallback)
            cfg_path = fallback
        else:
            parser.error(f"Config not found: {cfg_path}")

    asyncio.run(amain(cfg_path))


if __name__ == "__main__":
    main()
