"""FastAPI app: recent spots, live WS, and radio control endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from ..beacons import current_beacon
from ..bus import SpotBus
from ..filters import SpotFilter
from ..rig import (
    DEFAULT_BAUD_RATES,
    DEFAULT_LISTEN_PORT,
    RigController,
    hamlib_available,
    list_models,
    list_serial_ports,
    load_saved_rig,
)
from ..spot import Spot
from ..store import SpotStore

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


class RigConnectBody(BaseModel):
    model_id: int
    port: str = Field(..., min_length=1)
    baud: int | None = None
    listen_port: int = DEFAULT_LISTEN_PORT


class RigTuneBody(BaseModel):
    freq_khz: float
    mode: str | None = None


def _spot_to_dict(s: Spot) -> dict:
    d = asdict(s)
    d["spotted_at"] = s.spotted_at.isoformat()
    d["band"] = s.band
    return d


def create_app(
    bus: SpotBus,
    store: SpotStore,
    spot_filter: SpotFilter,
    rig: RigController,
) -> FastAPI:
    app = FastAPI(title="CWSigFind", version="0.1.0")

    # -- pages & spot feed -------------------------------------------------

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/spots")
    async def get_spots(limit: int = 200) -> JSONResponse:
        spots = store.recent(min(max(limit, 1), 1000))
        return JSONResponse([_spot_to_dict(s) for s in spots])

    @app.get("/api/beacons/current")
    async def beacons_now() -> JSONResponse:
        """NCDXF/IARU live beacon snapshot — one beacon per band, refreshed by client."""
        items = current_beacon()
        return JSONResponse(
            {
                "beacons": [
                    {
                        "band": b.band,
                        "frequency_khz": b.frequency_khz,
                        "callsign": b.callsign,
                        "location": b.location,
                        "slot_seconds_remaining": b.slot_seconds_remaining,
                    }
                    for b in items
                ]
            }
        )

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        q = await bus.subscribe()
        try:
            recent = [s for s in store.recent(200) if spot_filter.matches(s)]
            await websocket.send_text(
                json.dumps(
                    {"type": "snapshot", "spots": [_spot_to_dict(s) for s in recent]}
                )
            )
            while True:
                spot = await q.get()
                if not spot_filter.matches(spot):
                    continue
                await websocket.send_text(
                    json.dumps({"type": "spot", "spot": _spot_to_dict(spot)})
                )
        except WebSocketDisconnect:
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("WebSocket error")
        finally:
            await bus.unsubscribe(q)

    # -- radio control -----------------------------------------------------

    @app.get("/api/rig/env")
    async def rig_env() -> JSONResponse:
        """Static-ish info: is Hamlib installed, default baud rates, default listen port."""
        return JSONResponse(
            {
                "hamlib_installed": hamlib_available(),
                "baud_rates": list(DEFAULT_BAUD_RATES),
                "default_listen_port": DEFAULT_LISTEN_PORT,
            }
        )

    @app.get("/api/rig/models")
    async def rig_models() -> JSONResponse:
        return JSONResponse([asdict(m) for m in list_models()])

    @app.get("/api/rig/serial-ports")
    async def rig_serial_ports() -> JSONResponse:
        return JSONResponse([asdict(p) for p in list_serial_ports()])

    @app.get("/api/rig/status")
    async def rig_status() -> JSONResponse:
        # Cheap live refresh so the UI sees VFO changes the user made on the rig.
        await rig.refresh()
        return JSONResponse(asdict(rig.status))

    @app.get("/api/rig/saved")
    async def rig_saved() -> JSONResponse:
        """Return the persisted rig config (or null) so the UI can pre-fill the form."""
        saved = load_saved_rig()
        return JSONResponse(asdict(saved) if saved is not None else None)

    @app.post("/api/rig/connect")
    async def rig_connect(body: RigConnectBody) -> JSONResponse:
        st = await rig.connect(
            model_id=body.model_id,
            port=body.port,
            baud=body.baud,
            listen_port=body.listen_port,
        )
        return JSONResponse(asdict(st), status_code=200 if st.state == "connected" else 400)

    @app.post("/api/rig/disconnect")
    async def rig_disconnect() -> JSONResponse:
        st = await rig.disconnect()
        return JSONResponse(asdict(st))

    @app.post("/api/rig/tune")
    async def rig_tune(body: RigTuneBody) -> JSONResponse:
        if rig.status.state != "connected":
            raise HTTPException(status_code=409, detail="Rig not connected")
        st = await rig.tune(freq_khz=body.freq_khz, mode=body.mode)
        return JSONResponse(asdict(st))

    return app
