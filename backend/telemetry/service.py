"""
Live telemetry: one packet per second per blackbox. Every second is stored and checked for a trigger, and when one
fires M2 scores the 16 s around it. Triggers and predictions go to the incident rules; the status of each second
goes to whoever listens (the monitor pages in the demo).
"""
import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Annotated, Literal

import numpy as np
from annotated_types import Len
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from backend import auth, db, ml
from backend.config import settings
from backend.models import Device, Prediction, Telemetry
from backend.telemetry.stream import AFTER_S, HZ, DeviceStream

MAX_MESSAGE_BYTES = 200_000  # a full-precision second is about 15 kB
MAX_ERRORS_SHOWN = 3
STREAM_TIMEOUT_S = 3  # silence that means a car's stream has stopped
CAR_GONE_S = 30       # silence that takes a car off the monitor, unless it crashed
ALARM_LEVELS = ("crash", "likely")

log = logging.getLogger("solvex")
Vector3 = Annotated[list[float], Len(3, 3)]


class Location(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class Packet(BaseModel):
    model_config = ConfigDict(strict=True, allow_inf_nan=False)

    type: Literal["telemetry"]
    device_id: str = Field(min_length=1, max_length=128)
    device_key: str | None = Field(None, max_length=200)
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


class Car:
    def __init__(self):
        self.stream = DeviceStream()
        self.ws = None
        self.last_seen = 0.0
        self.status = None
        self.prediction = None
        self.location = None   # the last one received: GPS often drops out in the crash itself


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


class TelemetryService:
    def __init__(self):
        self.cars: dict[str, Car] = {}
        self.listeners = []   # async fn(message): the status and prediction of every car
        self.incidents = None  # set by main: the incident rules

    async def tell(self, message):
        for listener in list(self.listeners):
            try:
                await listener(message)
            except Exception:
                log.exception("a telemetry listener failed")

    async def refusal(self, packet, trusted):
        """Why this packet is refused, or None. A registered blackbox must show its key; an unknown one counts as an unknown person's."""
        if trusted:
            return None
        async with db.session() as s:
            device = await s.get(Device, packet.device_id)
        if device is None:
            return None if settings.allow_unknown_devices else "unknown device"
        return None if auth.check_key(packet.device_key, device.key_hash) else "this device needs its device_key"

    async def guarded(self, what, device_id, coro):
        """Storing and bookkeeping must never stop a crash from being scored: M2's answer comes first."""
        try:
            return await coro
        except Exception:
            log.exception("%s failed for %s", what, device_id)

    async def save(self, t, acc, gyro):
        async with db.session_scope() as s:
            s.add(Telemetry(device_id=t.device_id, seq=t.seq, recorded_at=t.recorded_at.isoformat() if t.recorded_at else None,
                            lat=t.location.lat if t.location else None, lon=t.location.lon if t.location else None,
                            speed_kmh=t.gps_speed_kmh, acc=acc.tobytes(), gyro=gyro.tobytes()))

    async def publish_status(self, device_id, car, peak_g):
        """What the monitor page shows for this second."""
        stream = car.stream
        car.status = {"type": "status", "device_id": device_id, "seq": stream.last_seq, "peak_g": round(peak_g, 2), "state": "watching"}
        if stream.collecting:
            before_s, remaining_s = stream.progress()
            car.status.update(state="collecting", trigger_seq=stream.trigger_second.seq, before_s=before_s, remaining_s=remaining_s)
            log.info("%s seq=%d collecting: %d s before held, %d s left", device_id, stream.last_seq, before_s, remaining_s)
        await self.tell(car.status)

    async def save_prediction(self, device_id, w, result, reply):
        async with db.session_scope() as s:
            row = Prediction(device_id=device_id, trigger_seq=w.trigger.seq, level=result["level"], p_crash=result["p_crash"], result=reply)
            s.add(row)
            await s.flush()
            return row.id

    async def predict(self, device_id, car, w):
        m2 = ml.m2_model()
        result = m2.predict(w.acc, w.gyro, w.speed)
        location = w.trigger.location or car.location
        reply = {
            "type": "prediction", "device_id": device_id, "model": "m2", "trigger_seq": w.trigger.seq,
            "trigger_recorded_at": w.trigger.recorded_at.isoformat() if w.trigger.recorded_at else None,
            "location": location.model_dump() if location else None,
            **result, "filled_s": w.filled_s, "stream_ended": w.stream_ended,
        }
        reply["prediction_id"] = await self.guarded("saving a prediction", device_id, self.save_prediction(device_id, w, result, reply))
        log.info("%s PREDICTION level=%s p=%.4f trigger_seq=%s filled=%.2f s (%.1f ms)", device_id, result["level"],
                 result["p_crash"], w.trigger.seq, w.filled_s, result["latency_ms"])
        car.prediction = reply
        await send(car.ws, reply)
        await self.tell(reply)
        if self.incidents:
            summary = m2.crash_summary(w.acc, w.gyro, w.speed) if result["level"] in ALARM_LEVELS else None
            await self.guarded("the incident rules", device_id, self.incidents.on_prediction(reply, summary))

    async def handle(self, raw, ws, trusted=False):
        try:
            t = Packet.model_validate_json(raw)
        except ValidationError as e:
            code = "invalid_json" if e.errors()[0]["type"] == "json_invalid" else "invalid_telemetry"
            await send(ws, error(code, describe(e), *read_ids(raw)))
            return

        why = await self.refusal(t, trusted)
        if why:
            await send(ws, error("unauthorized", why, t.device_id, t.seq))
            return
        acc, gyro = np.array(t.acc, np.float32), np.array(t.gyro, np.float32)
        await self.guarded("saving telemetry", t.device_id, self.save(t, acc, gyro))

        car = self.cars.setdefault(t.device_id, Car())
        car.ws, car.last_seen = ws, time.monotonic()
        car.location = t.location or car.location
        was_collecting = car.stream.collecting
        speed = np.nan if t.gps_speed_kmh is None else t.gps_speed_kmh
        windows = car.stream.push(t.seq, acc, gyro, speed, t.recorded_at, t.location)
        peak_g = float(np.hypot(acc[:, 0], acc[:, 1]).max())
        if car.stream.collecting and not was_collecting:
            log.info("%s TRIGGER at seq=%d, collecting the next %d s", t.device_id, t.seq, AFTER_S)
            if self.incidents:
                where = t.location or car.location
                await self.guarded("the incident rules", t.device_id,
                                   self.incidents.on_trigger(t.device_id, t.seq, peak_g, where.model_dump() if where else None))
        await self.publish_status(t.device_id, car, peak_g)
        for w in windows:
            await self.predict(t.device_id, car, w)

    async def stop_stream(self, device_id, car):
        """The stream stopped mid-collection, which is what a real crash can do: score what arrived."""
        log.warning("%s stream stopped while collecting a window", device_id)
        for w in car.stream.finish():
            await self.predict(device_id, car, w)

    async def disconnected(self, ws):
        for device_id, car in list(self.cars.items()):
            if car.ws is ws:
                car.ws = None
                if car.stream.collecting:
                    await self.stop_stream(device_id, car)

    async def watch_for_silence(self):
        while True:
            await asyncio.sleep(1)
            now = time.monotonic()
            for device_id, car in list(self.cars.items()):
                silent = now - car.last_seen
                if car.stream.collecting and silent > STREAM_TIMEOUT_S:
                    await self.stop_stream(device_id, car)
                elif silent > CAR_GONE_S and not (car.prediction and car.prediction["level"] in ALARM_LEVELS):
                    del self.cars[device_id]
                    await self.tell({"type": "car_removed", "device_id": device_id})

    async def forget_all(self):
        self.cars.clear()
        await self.tell({"type": "reset"})


service = TelemetryService()
