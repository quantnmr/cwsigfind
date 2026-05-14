"""Radio control via Hamlib's `rigctld` subprocess.

We shell out to `rigctld` rather than use Hamlib's Python SWIG bindings, because
the bindings are painful to install reliably across Python versions, while
`rigctld` ships with `brew install hamlib` / `apt install libhamlib-utils` and
is the standard way logging programs talk to Hamlib.

Protocol reference (rigctld text mode, default):
    F <freq_hz>\n        — set freq;  reply: "RPRT 0\n" on success
    f\n                  — get freq;  reply: "<hz>\n"
    M <mode> <passband>\n— set mode;  reply: "RPRT 0\n"
    m\n                  — get mode;  reply: "<mode>\n<passband>\n"
    q\n                  — quit
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

RIGCTL_BIN = "rigctl"
RIGCTLD_BIN = "rigctld"

DEFAULT_LISTEN_PORT = 4532
DEFAULT_BAUD_RATES = (1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200)

CONFIG_DIR = Path.home() / ".cwsigfind"
RIG_STATE_FILE = CONFIG_DIR / "rig.json"


ConnState = Literal["disconnected", "connecting", "connected", "error"]


@dataclass(frozen=True)
class RigModel:
    """One row from `rigctl --list`."""

    model_id: int
    mfg: str
    model: str
    version: str = ""
    status: str = ""
    macro: str = ""


@dataclass(frozen=True)
class SerialPort:
    device: str
    description: str = ""


@dataclass
class RigStatus:
    state: ConnState = "disconnected"
    hamlib_installed: bool = False
    model_id: int | None = None
    model_name: str = ""
    port: str = ""
    baud: int | None = None
    freq_khz: float | None = None
    mode: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Pure helpers (no subprocess / no async)
# ---------------------------------------------------------------------------


def parse_rigctl_list(output: str) -> list[RigModel]:
    """Parse the output of `rigctl --list`.

    Columns are 2+-space separated. The Mfg or Model columns may themselves
    contain single spaces (e.g. "Dummy No VFO", "N2ADR James Ahlstrom").
    """
    models: list[RigModel] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) < 3:
            continue
        first = parts[0]
        if not first.isdigit():
            # Header line ("Rig #  Mfg ...") or footer.
            continue
        models.append(
            RigModel(
                model_id=int(first),
                mfg=parts[1] if len(parts) > 1 else "",
                model=parts[2] if len(parts) > 2 else "",
                version=parts[3] if len(parts) > 3 else "",
                status=parts[4] if len(parts) > 4 else "",
                macro=parts[5] if len(parts) > 5 else "",
            )
        )
    models.sort(key=lambda m: (m.mfg.lower(), m.model.lower()))
    return models


def normalize_mode_for_hamlib(mode: str, freq_khz: float | None = None) -> str | None:
    """Map our normalized Spot mode names to Hamlib mode strings.

    SSB is resolved using HF convention: USB above 10 MHz, LSB below.
    Returns None if we can't safely map (the caller should skip setting mode).
    """
    if not mode:
        return None
    m = mode.upper().strip()
    if m in ("CW", "CWR", "AM", "FM", "USB", "LSB", "RTTY", "RTTYR"):
        return m
    if m == "SSB":
        if freq_khz is None:
            return None
        return "USB" if freq_khz >= 10000 else "LSB"
    if m in ("FT8", "FT4", "JT65", "JT9", "MFSK", "OLIVIA"):
        # Digital modes typically run on USB with a soundcard.
        return "USB"
    if m.startswith("PSK"):
        return "PKTUSB"
    if m in ("DATA", "DIGI"):
        return "PKTUSB"
    return None


def hamlib_available() -> bool:
    return shutil.which(RIGCTL_BIN) is not None and shutil.which(RIGCTLD_BIN) is not None


# ---------------------------------------------------------------------------
# Cached system queries
# ---------------------------------------------------------------------------

_MODELS_CACHE: list[RigModel] | None = None


def list_models(refresh: bool = False) -> list[RigModel]:
    """Return the cached Hamlib-supported model list. Empty if Hamlib is missing."""
    global _MODELS_CACHE
    if _MODELS_CACHE is not None and not refresh:
        return _MODELS_CACHE
    if not hamlib_available():
        _MODELS_CACHE = []
        return _MODELS_CACHE
    try:
        result = subprocess.run(
            [RIGCTL_BIN, "--list"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except Exception as e:
        log.warning("rigctl --list failed: %s", e)
        _MODELS_CACHE = []
        return _MODELS_CACHE
    _MODELS_CACHE = parse_rigctl_list(result.stdout)
    log.info("Hamlib: discovered %d rig models", len(_MODELS_CACHE))
    return _MODELS_CACHE


def list_serial_ports() -> list[SerialPort]:
    """Enumerate serial devices visible to the OS."""
    try:
        from serial.tools import list_ports  # type: ignore[import-untyped]
    except ImportError:
        log.warning("pyserial not available; can't list serial ports")
        return []
    ports = [
        SerialPort(device=p.device, description=(p.description or "").strip())
        for p in list_ports.comports()
    ]
    ports.sort(key=lambda p: p.device)
    return ports


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


@dataclass
class SavedRigConfig:
    model_id: int
    port: str
    baud: int | None = None
    listen_port: int = DEFAULT_LISTEN_PORT


def load_saved_rig() -> SavedRigConfig | None:
    try:
        with open(RIG_STATE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return SavedRigConfig(
            model_id=int(raw["model_id"]),
            port=str(raw["port"]),
            baud=int(raw["baud"]) if raw.get("baud") else None,
            listen_port=int(raw.get("listen_port", DEFAULT_LISTEN_PORT)),
        )
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError):
        return None


def save_rig(cfg: SavedRigConfig) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = RIG_STATE_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(asdict(cfg), f)
    os.replace(tmp, RIG_STATE_FILE)


def clear_saved_rig() -> None:
    try:
        RIG_STATE_FILE.unlink()
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------


class RigController:
    """Manages one `rigctld` subprocess and a TCP connection to it."""

    def __init__(self) -> None:
        self.status = RigStatus(hamlib_installed=hamlib_available())
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self._listen_port: int = DEFAULT_LISTEN_PORT

    # -- public API --------------------------------------------------------

    async def connect(
        self,
        model_id: int,
        port: str,
        baud: int | None = None,
        listen_port: int = DEFAULT_LISTEN_PORT,
        persist: bool = True,
    ) -> RigStatus:
        async with self._lock:
            await self._teardown_locked()

            if not hamlib_available():
                self._set_error(
                    "Hamlib not installed. macOS: `brew install hamlib`. "
                    "Debian/Ubuntu: `sudo apt install libhamlib-utils`."
                )
                return self.status

            self.status = RigStatus(
                state="connecting",
                hamlib_installed=True,
                model_id=model_id,
                port=port,
                baud=baud,
                model_name=_lookup_model_name(model_id),
            )
            self._listen_port = listen_port

            args = [RIGCTLD_BIN, "-m", str(model_id), "-r", port, "-t", str(listen_port)]
            if baud:
                args.extend(["-s", str(baud)])
            log.info("Spawning rigctld: %s", " ".join(args))

            try:
                self._proc = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError as e:
                self._set_error(f"Failed to launch rigctld: {e}")
                return self.status

            # rigctld needs a moment to open the serial port and bind TCP.
            # Poll for either successful TCP connect, or fast process death.
            connected = False
            for _ in range(40):  # ~4s total
                await asyncio.sleep(0.1)
                if self._proc.returncode is not None:
                    err = await _read_all(self._proc.stderr)
                    self._set_error(
                        f"rigctld exited (code {self._proc.returncode}): {err.strip() or '(no stderr)'}"
                    )
                    self._proc = None
                    return self.status
                try:
                    self._reader, self._writer = await asyncio.open_connection(
                        "127.0.0.1", listen_port
                    )
                    connected = True
                    break
                except OSError:
                    continue
            if not connected:
                await self._teardown_locked()
                self._set_error(
                    f"rigctld started but didn't bind 127.0.0.1:{listen_port} within 4s"
                )
                return self.status

            # Verify the radio actually answers. Get freq + mode.
            try:
                freq_hz = await self._cmd_get_freq()
                mode, _pb = await self._cmd_get_mode()
            except Exception as e:
                # If rigctld printed something useful on stderr in the meantime,
                # surface that — the serial open error usually lives there.
                stderr = await _read_all(self._proc.stderr if self._proc else None)
                await self._teardown_locked()
                detail = stderr.strip() or f"{type(e).__name__}: {e}".strip(": ")
                self._set_error(f"Connected to rigctld but radio didn't respond — {detail}")
                return self.status

            self.status = RigStatus(
                state="connected",
                hamlib_installed=True,
                model_id=model_id,
                model_name=_lookup_model_name(model_id),
                port=port,
                baud=baud,
                freq_khz=freq_hz / 1000.0,
                mode=mode,
            )
            log.info(
                "Rig connected: %s on %s @ %.3f kHz %s",
                self.status.model_name, port, self.status.freq_khz, mode,
            )

            if persist:
                try:
                    save_rig(SavedRigConfig(
                        model_id=model_id, port=port, baud=baud, listen_port=listen_port,
                    ))
                except Exception:
                    log.exception("Failed to persist rig config")

            return self.status

    async def disconnect(self) -> RigStatus:
        async with self._lock:
            await self._teardown_locked()
            self.status = RigStatus(hamlib_installed=hamlib_available())
            return self.status

    async def tune(self, freq_khz: float, mode: str | None = None) -> RigStatus:
        async with self._lock:
            if (
                self.status.state != "connected"
                or self._writer is None
                or self._reader is None
            ):
                self.status.error = "Rig not connected"
                return self.status
            try:
                freq_hz = int(round(freq_khz * 1000))
                await self._cmd_set_freq(freq_hz)
                rig_mode = normalize_mode_for_hamlib(mode or "", freq_khz)
                if rig_mode:
                    try:
                        await self._cmd_set_mode(rig_mode, 0)
                    except Exception as e:
                        log.warning("set mode '%s' failed (continuing): %s", rig_mode, e)
                self.status.freq_khz = freq_khz
                if mode:
                    self.status.mode = mode
                self.status.error = None
            except Exception as e:
                self.status.error = f"Tune failed: {e}"
                log.warning("Tune failed: %s", e)
            return self.status

    async def refresh(self) -> RigStatus:
        """Re-read current freq/mode from the rig. Cheap; safe to poll."""
        async with self._lock:
            if self.status.state != "connected":
                return self.status
            try:
                freq_hz = await self._cmd_get_freq()
                mode, _pb = await self._cmd_get_mode()
                self.status.freq_khz = freq_hz / 1000.0
                self.status.mode = mode
                self.status.error = None
            except Exception as e:
                self.status.error = f"Refresh failed: {e}"
            return self.status

    # -- internals ---------------------------------------------------------

    async def _teardown_locked(self) -> None:
        if self._writer is not None:
            try:
                self._writer.write(b"q\n")
                await self._writer.drain()
            except Exception:
                pass
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None
        if self._proc is not None and self._proc.returncode is None:
            try:
                self._proc.terminate()
                try:
                    await asyncio.wait_for(self._proc.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    self._proc.kill()
                    await self._proc.wait()
            except ProcessLookupError:
                pass
        self._proc = None

    def _set_error(self, msg: str) -> None:
        self.status = RigStatus(
            state="error",
            hamlib_installed=hamlib_available(),
            error=msg,
            model_id=self.status.model_id,
            model_name=self.status.model_name,
            port=self.status.port,
            baud=self.status.baud,
        )
        log.warning("Rig error: %s", msg)

    async def _cmd_get_freq(self) -> int:
        assert self._writer is not None and self._reader is not None
        self._writer.write(b"f\n")
        await self._writer.drain()
        raw = await asyncio.wait_for(self._reader.readline(), timeout=4.0)
        if not raw:
            raise ConnectionError("rigctld closed the connection")
        line = raw.decode().strip()
        if line.startswith("RPRT"):
            raise RuntimeError(f"get_freq returned {line}")
        try:
            return int(line)
        except ValueError as e:
            raise RuntimeError(f"get_freq unparseable reply: {line!r}") from e

    async def _cmd_set_freq(self, freq_hz: int) -> None:
        assert self._writer is not None and self._reader is not None
        self._writer.write(f"F {freq_hz}\n".encode())
        await self._writer.drain()
        line = (await asyncio.wait_for(self._reader.readline(), timeout=4.0)).decode().strip()
        if not line.startswith("RPRT 0"):
            raise RuntimeError(f"set_freq: {line}")

    async def _cmd_get_mode(self) -> tuple[str, int]:
        assert self._writer is not None and self._reader is not None
        self._writer.write(b"m\n")
        await self._writer.drain()
        raw = await asyncio.wait_for(self._reader.readline(), timeout=4.0)
        if not raw:
            raise ConnectionError("rigctld closed the connection")
        mode_line = raw.decode().strip()
        if mode_line.startswith("RPRT"):
            raise RuntimeError(f"get_mode returned {mode_line}")
        pb_raw = await asyncio.wait_for(self._reader.readline(), timeout=4.0)
        pb_line = pb_raw.decode().strip() if pb_raw else ""
        try:
            pb = int(pb_line)
        except ValueError:
            pb = 0
        return mode_line, pb

    async def _cmd_set_mode(self, mode: str, passband: int = 0) -> None:
        assert self._writer is not None and self._reader is not None
        self._writer.write(f"M {mode} {passband}\n".encode())
        await self._writer.drain()
        line = (await asyncio.wait_for(self._reader.readline(), timeout=4.0)).decode().strip()
        if not line.startswith("RPRT 0"):
            raise RuntimeError(f"set_mode {mode}: {line}")


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------


def _lookup_model_name(model_id: int) -> str:
    for m in list_models():
        if m.model_id == model_id:
            return f"{m.mfg} {m.model}".strip()
    return f"Model #{model_id}"


async def _read_all(stream: asyncio.StreamReader | None) -> str:
    if stream is None:
        return ""
    try:
        data = await asyncio.wait_for(stream.read(4096), timeout=0.5)
        return data.decode("utf-8", errors="replace")
    except asyncio.TimeoutError:
        return ""
    except Exception:
        return ""
