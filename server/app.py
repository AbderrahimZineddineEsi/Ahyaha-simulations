"""
SolveX server, phase 1: simulated blackboxes stream telemetry over WebSocket. Every second is saved to the
database and checked for a trigger, and when one fires M2 scores the 16 s around it. The request and response
are documented in PROTOCOL.md.

    python server/app.py               # listens on all interfaces, port 8000
    python server/app.py --port 9000
"""
import argparse
import asyncio
import json
import logging
import socket
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
import uvicorn
from annotated_types import Len
from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from database import Database
from m2_model import M2
from stream import AFTER_S, BEFORE_S, HZ, DeviceStream

MAX_MESSAGE_BYTES = 200_000  # a full-precision second is about 15 kB
MAX_ERRORS_SHOWN = 3
STREAM_TIMEOUT_S = 3  # silence that means a car's stream has stopped

log = logging.getLogger("solvex")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

Vector3 = Annotated[list[float], Len(3, 3)]


class Location(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class Telemetry(BaseModel):
    model_config = ConfigDict(strict=True, allow_inf_nan=False)

    type: Literal["telemetry"]
    device_id: str = Field(min_length=1, max_length=128)
    seq: int = Field(ge=0)
    recorded_at: datetime | None = None
    location: Location | None = None
    acc: Annotated[list[Vector3], Len(HZ, HZ)]
    gyro: Annotated[list[Vector3], Len(HZ, HZ)]
    gps_speed_kmh: Annotated[float, Field(ge=0, le=300)] | None

    @field_validator("acc")
    @classmethod
    def acc_is_in_g(cls, acc):
        # Crashes and tilted devices move z a lot, so only catch the unit mistake: 9.81 at rest means m/s²
        az = float(np.median(np.abs(np.array(acc)[:, 2])))
        if az > 5:
            raise ValueError(f"median |az| is {az:.2f}: acc must be in g (about +1 on z at rest), not m/s²")
        return acc


@dataclass
class Car:
    stream: DeviceStream
    ws: WebSocket | None = None
    last_seen: float = 0.0
    status: dict | None = None
    prediction: dict | None = None


cars: dict[str, Car] = {}
monitors: set[WebSocket] = set()
db = Database()
m2 = M2()


def error(code, message, device_id=None, seq=None):
    return {"type": "error", "code": code, "device_id": device_id, "seq": seq, "message": message}


def describe(e):
    """A short message: pydantic reports one error per bad sample, and echoes the values."""
    errors = e.errors(include_input=False, include_url=False, include_context=False)
    shown = [f"{'.'.join(map(str, err['loc'])) or 'message'}: {err['msg']}" for err in errors[:MAX_ERRORS_SHOWN]]
    more = len(errors) - len(shown)
    return "; ".join(shown) + (f" (and {more} more)" if more else "")


def read_ids(raw):
    """Best effort, so that the error of a rejected message can still name it."""
    try:
        message = json.loads(raw)
        return message.get("device_id"), message.get("seq")
    except (ValueError, AttributeError):
        return None, None


async def send(ws, message):
    """A car that just crashed may be gone: its result is already in the database."""
    if ws is None:
        return
    try:
        await ws.send_json(message)
    except Exception as e:
        log.warning("couldn't send %s: %s", message["type"], e)


async def tell_monitors(message):
    await asyncio.gather(*(send(ws, message) for ws in list(monitors)))


async def publish_status(device_id, car, peak_g):
    """What the monitor page shows for this second."""
    stream = car.stream
    car.status = {"type": "status", "device_id": device_id, "seq": stream.last_seq, "peak_g": round(peak_g, 2),
                  "state": "watching"}
    if stream.collecting:
        before_s, remaining_s = stream.progress()
        car.status.update(state="collecting", trigger_seq=stream.trigger_second.seq,
                          before_s=before_s, remaining_s=remaining_s)
        log.info("%s seq=%d collecting: %d s before held, %d s left", device_id, stream.last_seq, before_s, remaining_s)
    else:
        log.info("%s seq=%d no trigger (peak %.2f g)", device_id, stream.last_seq, peak_g)
    await tell_monitors(car.status)


async def predict(device_id, car, w):
    result = m2.predict(w.acc, w.gyro, w.speed)
    location = w.trigger.location
    reply = {
        "type": "prediction",
        "device_id": device_id,
        "model": "m2",
        "trigger_seq": w.trigger.seq,
        "trigger_recorded_at": w.trigger.recorded_at.isoformat() if w.trigger.recorded_at else None,
        "location": location.model_dump() if location else None,
        **result,
        "filled_s": w.filled_s,
        "stream_ended": w.stream_ended,
    }
    reply["prediction_id"] = db.save_prediction(reply)
    log.info("%s PREDICTION level=%s p=%.4f trigger_seq=%s filled=%.2f s (%.1f ms)", device_id, result["level"],
             result["p_crash"], w.trigger.seq, w.filled_s, result["latency_ms"])
    car.prediction = reply
    await send(car.ws, reply)
    await tell_monitors(reply)


async def handle(raw, ws):
    try:
        t = Telemetry.model_validate_json(raw)
    except ValidationError as e:
        code = "invalid_json" if e.errors()[0]["type"] == "json_invalid" else "invalid_telemetry"
        await send(ws, error(code, describe(e), *read_ids(raw)))
        return

    acc, gyro = np.array(t.acc, np.float32), np.array(t.gyro, np.float32)
    db.save_telemetry(t, acc, gyro)

    car = cars.setdefault(t.device_id, Car(DeviceStream()))
    car.ws, car.last_seen = ws, time.monotonic()
    was_collecting = car.stream.collecting
    speed = np.nan if t.gps_speed_kmh is None else t.gps_speed_kmh
    windows = car.stream.push(t.seq, acc, gyro, speed, t.recorded_at, t.location)
    if car.stream.collecting and not was_collecting:
        log.info("%s TRIGGER at seq=%d, collecting the next %d s", t.device_id, t.seq, AFTER_S)
    await publish_status(t.device_id, car, float(np.hypot(acc[:, 0], acc[:, 1]).max()))
    for w in windows:
        await predict(t.device_id, car, w)


async def stop_stream(device_id, car):
    """The stream stopped mid-collection, which is what a real crash can do: score what arrived."""
    log.warning("%s stream stopped while collecting a window", device_id)
    for w in car.stream.finish():
        await predict(device_id, car, w)


async def watch_for_silence():
    while True:
        await asyncio.sleep(1)
        now = time.monotonic()
        for device_id, car in list(cars.items()):
            if car.stream.collecting and now - car.last_seen > STREAM_TIMEOUT_S:
                await stop_stream(device_id, car)


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(watch_for_silence())
    yield
    task.cancel()


app = FastAPI(title="SolveX server", lifespan=lifespan)


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
            await handle(raw, ws)
        except Exception:
            log.exception("failed on a message from %s", peer)
            await send(ws, error("internal_error", "the server failed on this message, see its log"))
    log.info("%s disconnected", peer)
    for device_id, car in list(cars.items()):
        if car.ws is ws:
            car.ws = None
            if car.stream.collecting:
                await stop_stream(device_id, car)


@app.websocket("/ws/monitor")
async def monitor(ws: WebSocket):
    """Live feed for the monitor page. It only listens: nothing it sends is used."""
    await ws.accept()
    for car in cars.values():
        for message in (car.prediction, car.status):
            if message:
                await send(ws, message)
    monitors.add(ws)
    try:
        while (await ws.receive())["type"] != "websocket.disconnect":
            pass
    finally:
        monitors.discard(ws)


@app.get("/")
def monitor_page():
    return FileResponse(Path(__file__).parent / "monitor.html")


@app.get("/health")
def health():
    return {"status": "ok", "model": "m2", "cars_seen": len(cars)}


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
    log.info("Trigger window: %d s before, %d s after", BEFORE_S, AFTER_S)
    log.info("Connect to ws://%s:%d/ws/telemetry", lan_ip(), args.port)
    log.info("Watch it live at http://localhost:%d/", args.port)
    uvicorn.run(app, host=args.host, port=args.port, ws_max_size=MAX_MESSAGE_BYTES)
