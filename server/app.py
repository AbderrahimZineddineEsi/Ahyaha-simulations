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
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from blackbox_sim import LEAD_IN_S, packet_line, reply_line, scenario_packets, stamp
from database import Database
from incident import Engine
from m2_model import M2, bb
from ops import make_router
from severity import Severity
from stream import AFTER_S, BEFORE_S, HZ, DeviceStream

MAX_MESSAGE_BYTES = 200_000  # a full-precision second is about 15 kB
MAX_ERRORS_SHOWN = 3
STREAM_TIMEOUT_S = 3  # silence that means a car's stream has stopped
CAR_GONE_S = 30       # silence that takes a car off the monitor, unless it crashed
ALARM_LEVELS = ("crash", "likely")

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
class Blackbox:
    scenario: str
    started: float
    stopped: bool = False
    lead_in_s: int = LEAD_IN_S

    def started_message(self, device_id):
        return {"type": "blackbox_started", "device_id": device_id, "scenario": self.scenario, "lead_in_s": self.lead_in_s,
                "elapsed_s": time.monotonic() - self.started}


@dataclass
class Car:
    stream: DeviceStream
    ws: WebSocket | None = None
    last_seen: float = 0.0
    status: dict | None = None
    prediction: dict | None = None


cars: dict[str, Car] = {}
monitors: set[WebSocket] = set()
blackboxes: dict[str, "Blackbox"] = {}  # the ones running on the server
db = Database()
m2 = M2()
engine = Engine(Severity())
background: set[asyncio.Task] = set()


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
    if device_id == engine.device_id:  # a blackbox the demo scenarios started
        await engine.on_prediction(reply, bb.crash_summary(w.acc, w.gyro, w.speed))


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
    peak_g = float(np.hypot(acc[:, 0], acc[:, 1]).max())
    if car.stream.collecting and not was_collecting:
        log.info("%s TRIGGER at seq=%d, collecting the next %d s", t.device_id, t.seq, AFTER_S)
        await engine.on_trigger(t.device_id, t.seq, peak_g, t.location)
    await publish_status(t.device_id, car, peak_g)
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
            silent = now - car.last_seen
            if car.stream.collecting and silent > STREAM_TIMEOUT_S:
                await stop_stream(device_id, car)
            elif silent > CAR_GONE_S and not (car.prediction and car.prediction["level"] in ALARM_LEVELS):
                del cars[device_id]
                await tell_monitors({"type": "car_removed", "device_id": device_id})


@asynccontextmanager
async def lifespan(app):
    tasks = [asyncio.create_task(watch_for_silence()), asyncio.create_task(engine.run())]
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(title="SolveX server", lifespan=lifespan)
app.include_router(make_router(engine))


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
    for device_id, blackbox in blackboxes.items():
        await send(ws, blackbox.started_message(device_id))
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


PAGES = Path(__file__).parent


@app.get("/")
def dashboard_page():
    return FileResponse(PAGES / "dashboard.html")


@app.get("/dev")
def dev_page():
    return FileResponse(PAGES / "dev.html")


@app.get("/dev/monitor")
def monitor_page():
    return FileResponse(PAGES / "monitor.html")


@app.get("/dev/live")
def live_page():
    return FileResponse(PAGES / "live.html")


@app.get("/dev/simulator")
def simulator_page():
    return FileResponse(PAGES / "simulator.html")


app.mount("/examples", StaticFiles(directory=Path(__file__).parent / "examples"), name="examples")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


class Replies:
    """Stands in for a blackbox's WebSocket: collects what the server answers."""
    def __init__(self):
        self.messages = []

    async def send_json(self, message):
        self.messages.append(message)


async def run_blackbox(device_id, blackbox):
    """Sends one packet per second through the same path as a real blackbox, and prints each one."""
    replies = Replies()
    next_at = blackbox.started
    try:
        await tell_monitors(blackbox.started_message(device_id))
        yield f"{device_id}: {blackbox.scenario}. Press Ctrl+C to stop.\n\n"
        for seq, (stage, packet) in enumerate(scenario_packets(blackbox.scenario, blackbox.lead_in_s)):
            # A second is sent once it is complete, so the prediction comes 8 s after the impact
            next_at += 1
            await asyncio.sleep(max(0, next_at - time.monotonic()))
            if blackbox.stopped:
                yield "\nStopped by a reset on the server.\n"
                return
            packet = stamp(packet, device_id, seq)
            raw = json.dumps(packet, separators=(",", ":"))
            await handle(raw, replies)
            yield packet_line(stage, packet, len(raw))
            for message in replies.messages:
                yield reply_line(message)
            replies.messages.clear()
    finally:
        del blackboxes[device_id]
        asyncio.create_task(tell_monitors({"type": "blackbox_stopped", "device_id": device_id}))


@app.get("/blackbox/{device_id}/{scenario}")
async def start_blackbox(device_id: str, scenario: Literal["normal", "crash", "braking"]):
    if device_id in blackboxes:
        return PlainTextResponse(f"{device_id} is already running. Stop it first, or use another name.\n", 409)
    blackboxes[device_id] = Blackbox(scenario, time.monotonic())
    return StreamingResponse(run_blackbox(device_id, blackboxes[device_id]), media_type="text/plain")


async def reset_everything():
    """Clears every car, crashes included, the response in progress, and the blackboxes running here. The database stays."""
    cars.clear()
    for blackbox in blackboxes.values():
        blackbox.stopped = True
    await tell_monitors({"type": "reset"})
    await engine.reset()
    log.info("everything reset")


@app.post("/api/reset")
async def reset():
    await reset_everything()
    return {"status": "ok"}


async def begin_scenario(key):
    """Starts the crash of user 0 for scenario a or b: the blackbox's data goes through the real pipeline."""
    await reset_everything()
    device_id = await engine.start_scenario(key)
    blackboxes[device_id] = Blackbox("crash", time.monotonic(), lead_in_s=0)
    return device_id


async def consume(generator):
    async for _ in generator:
        pass


@app.post("/dev/start/{key}")
async def dev_start(key: Literal["a", "b", "2"]):
    device_id = await begin_scenario(key)
    task = asyncio.create_task(consume(run_blackbox(device_id, blackboxes[device_id])))
    background.add(task)
    task.add_done_callback(background.discard)
    return {"device_id": device_id}


@app.get("/dev/run/{key}")
async def dev_run(key: Literal["a", "b", "2"], request: Request):
    """The same as the /dev button, from a terminal: `curl -N`, and the packets print as they are sent."""
    device_id = await begin_scenario(key)
    host = request.headers.get("host", "localhost:8000")

    async def stream():
        yield f"Scenario {key.upper()} started. Watch it on http://{host}/ and http://{host}/dev\n\n"
        async for chunk in run_blackbox(device_id, blackboxes[device_id]):
            yield chunk

    return StreamingResponse(stream(), media_type="text/plain")


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
    log.info("Dashboard: http://localhost:%d/", args.port)
    log.info("Dev:       http://localhost:%d/dev", args.port)
    uvicorn.run(app, host=args.host, port=args.port, ws_max_size=MAX_MESSAGE_BYTES)
