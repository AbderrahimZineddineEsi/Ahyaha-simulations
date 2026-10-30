"""Step 1: blackbox telemetry reaches M2 and is stored; step 3's entry: a crash opens an incident."""
import json
from datetime import datetime, timezone
from itertools import chain, cycle, islice
from pathlib import Path

import pytest
from sqlalchemy import func, select

from backend import db, ml
from backend.models import Incident, Prediction, Telemetry
from backend.telemetry.service import service

EX = Path(__file__).resolve().parent.parent / "sim" / "examples"
NORMAL, CRASH, BRAKING = (json.loads((EX / f"{n}.json").read_text()) for n in ("normal", "crash", "trigger_no_crash"))
SPOT = {"lat": 36.3739, "lon": 3.9141}


class Replies:
    def __init__(self):
        self.messages = []

    async def send_json(self, m):
        self.messages.append(m)


def packets(device_id, scenario, lead=10, location=SPOT, key=None):
    driving = (("normal", p) for p in cycle(NORMAL))
    body = {"crash": CRASH, "braking": BRAKING}[scenario]
    seq = chain(islice(driving, lead), ((scenario, p) for p in body), islice(driving, 3))
    for i, (_, p) in enumerate(seq):
        out = {**p, "device_id": device_id, "seq": i, "recorded_at": datetime.now(timezone.utc).isoformat()}
        if location:
            out["location"] = location
        if key:
            out["device_key"] = key
        yield json.dumps(out)


@pytest.fixture
async def pipeline(env):
    ml.m2_model()  # loaded once
    service.cars.clear()
    service.incidents = __import__("backend.incidents.service", fromlist=["x"])
    return service


async def feed(raws, ws=None, trusted=True):
    ws = ws or Replies()
    for raw in raws:
        await service.handle(raw, ws, trusted=trusted)
    return ws


async def test_crash_is_stored_scored_and_becomes_an_incident(pipeline):
    ws = await feed(packets("bb-unknown", "crash"))
    async with db.session() as s:
        assert await s.scalar(select(func.count()).select_from(Telemetry)) == 29
        pred = await s.scalar(select(Prediction))
        inc = await s.scalar(select(Incident))
    assert pred.level == "crash" and pred.p_crash > 0.9
    assert [m["level"] for m in ws.messages if m["type"] == "prediction"] == ["crash"]
    # An unknown blackbox: an incident with an unknown person, flagged, with a default adult for the severity
    assert inc.flags["unknown_victim"] and inc.flags["default_profile"] and inc.victim_id is None
    assert inc.status == "detected" and inc.confidence == "high" and inc.m3_level
    assert (inc.lat, inc.lon) == (SPOT["lat"], SPOT["lon"])
    assert inc.nobody_around    # no one has a location in this empty database


async def test_braking_is_a_false_alarm(pipeline):
    await feed(packets("bb-brake", "braking"))
    async with db.session() as s:
        inc = await s.scalar(select(Incident))
    assert inc.status == "false_alarm" and inc.m2_level == "none"


async def test_registered_device_needs_its_key(pipeline, make):
    owner = await make("Owner")
    key = (await owner.post("/devices", {"device_id": "bb-mine"})).json()["device_key"]
    ws = await feed(list(packets("bb-mine", "crash", lead=2))[:3], trusted=False)
    assert {m["code"] for m in ws.messages} == {"unauthorized"}
    async with db.session() as s:
        assert await s.scalar(select(func.count()).select_from(Telemetry)) == 0
    ws = await feed(list(packets("bb-mine", "crash", lead=2, key=key))[:3], trusted=False)
    assert ws.messages == []
    async with db.session() as s:
        assert await s.scalar(select(func.count()).select_from(Telemetry)) == 3


async def test_the_owner_is_the_victim(pipeline, make):
    owner = await make("Karim", age=45, sex="male", at=(36.3739, 3.9141))
    await owner.post("/devices", {"device_id": "bb-karim"})
    await feed(packets("bb-karim", "crash"))
    async with db.session() as s:
        inc = await s.scalar(select(Incident))
    assert inc.victim_id == owner.id and not inc.flags.get("unknown_victim") and not inc.flags.get("default_profile")
    assert inc.user_state == "pending"


async def test_bad_messages_are_refused_with_a_reason(pipeline):
    ws = await feed(["not json", json.dumps({"type": "telemetry", "device_id": "x", "seq": 1})])
    assert [m["code"] for m in ws.messages] == ["invalid_json", "invalid_telemetry"]


async def test_unknown_blackboxes_can_be_refused(pipeline):
    from backend.config import settings
    settings.allow_unknown_devices = False
    ws = await feed(list(packets("bb-stranger", "crash"))[:2], trusted=False)
    assert {m["code"] for m in ws.messages} == {"unauthorized"}
    async with db.session() as s:
        assert await s.scalar(select(func.count()).select_from(Telemetry)) == 0
