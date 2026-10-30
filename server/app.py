"""
SolveX server, phase 1: a simulated blackbox connects over WebSocket, sends a 16 s telemetry window, and M2
scores it. The request and response are documented in PROTOCOL.md.

    python server/app.py               # listens on all interfaces, port 8000
    python server/app.py --port 9000
"""
import argparse
import json
import logging
import socket
from datetime import datetime
from typing import Annotated, Literal

import numpy as np
import uvicorn
from annotated_types import Len
from fastapi import FastAPI, WebSocket
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from m2_model import M2

SAMPLES, GPS_SAMPLES = 1600, 16
MAX_MESSAGE_BYTES = 2_000_000  # a full-precision window is about 200 kB
MAX_ERRORS_SHOWN = 3

log = logging.getLogger("solvex")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

Vector3 = Annotated[list[float], Len(3, 3)]
GpsSample = Annotated[float, Field(ge=0, le=300)] | None


class Location(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class TelemetryWindow(BaseModel):
    model_config = ConfigDict(strict=True, allow_inf_nan=False)

    type: Literal["telemetry_window"]
    event_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)
    recorded_at: datetime | None = None
    location: Location | None = None
    acc: Annotated[list[Vector3], Len(SAMPLES, SAMPLES)]
    gyro: Annotated[list[Vector3], Len(SAMPLES, SAMPLES)]
    gps_speed_kmh: Annotated[list[GpsSample], Len(GPS_SAMPLES, GPS_SAMPLES)]

    @field_validator("acc")
    @classmethod
    def acc_is_in_g(cls, acc):
        # Real windows are tilted or flipped, so only catch the unit mistake: 9.81 at rest means m/s²
        az = float(np.median(np.abs(np.array(acc)[:, 2])))
        if az > 2:
            raise ValueError(f"median |az| is {az:.2f}: acc must be in g (about +1 on z at rest), not m/s²")
        return acc


app = FastAPI(title="SolveX server")
m2 = M2()


def error(code, message, event_id=None):
    return {"type": "error", "code": code, "event_id": event_id, "message": message}


def describe(e):
    """A short message: pydantic reports one error per bad sample, and echoes the values."""
    errors = e.errors(include_input=False, include_url=False, include_context=False)
    shown = [f"{'.'.join(map(str, err['loc'])) or 'message'}: {err['msg']}" for err in errors[:MAX_ERRORS_SHOWN]]
    more = len(errors) - len(shown)
    return "; ".join(shown) + (f" (and {more} more)" if more else "")


def read_event_id(raw):
    """Best effort, so that the error of a rejected request can still name its event."""
    try:
        event_id = json.loads(raw).get("event_id")
    except (ValueError, AttributeError):
        return None
    return event_id if isinstance(event_id, str) else None


def handle(raw):
    try:
        w = TelemetryWindow.model_validate_json(raw)
    except ValidationError as e:
        code = "invalid_json" if e.errors()[0]["type"] == "json_invalid" else "invalid_window"
        return error(code, describe(e), read_event_id(raw))

    speed = np.array([np.nan if v is None else v for v in w.gps_speed_kmh], np.float32)
    result = m2.predict(np.array(w.acc, np.float32), np.array(w.gyro, np.float32), speed)
    log.info("%s event=%s level=%s p=%.4f (%.1f ms)",
             w.device_id, w.event_id, result["level"], result["p_crash"], result["latency_ms"])
    return {"type": "prediction", "event_id": w.event_id, "device_id": w.device_id, "model": "m2", **result}


@app.websocket("/ws/telemetry")
async def telemetry(ws: WebSocket):
    await ws.accept()
    peer = f"{ws.client.host}:{ws.client.port}"
    log.info("%s connected", peer)
    while True:
        message = await ws.receive()
        if message["type"] == "websocket.disconnect":
            break
        raw = message.get("text") or message.get("bytes") or ""
        try:
            await ws.send_json(handle(raw))
        except Exception:
            log.exception("failed on a message from %s", peer)
            await ws.send_json(error("internal_error", "the server failed on this request, see its log"))
    log.info("%s disconnected", peer)


@app.get("/health")
def health():
    return {"status": "ok", "model": "m2"}


def lan_ip():
    """The address other PCs reach this one on. A UDP connect sends nothing, it only picks the route."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    log.info("Connect to ws://%s:%d/ws/telemetry", lan_ip(), args.port)
    uvicorn.run(app, host=args.host, port=args.port, ws_max_size=MAX_MESSAGE_BYTES)
