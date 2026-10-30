"""Step 4: real clients on a real Socket.IO server. Each one gets only the events meant for it."""
import asyncio
import logging
from collections import defaultdict

import pytest
import socketio
import uvicorn

from backend import auth, realtime, scheduler
from backend.config import settings
from backend.tests.helpers import SPOT, crash, register_device

NEAR = (SPOT[0] + 0.0009, SPOT[1])


@pytest.fixture
async def url(env):
    from backend.main import app
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    yield f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    server.should_exit = True
    await task


@pytest.fixture
async def connect(url):
    clients = []

    async def connect(who):
        got = defaultdict(list)
        c = socketio.AsyncClient()
        for event in ("notification", "incident.updated", "ambulance.moved", "hospital.incoming"):
            c.on(event, lambda data, event=event: got[event].append(data))
        await c.connect(url, auth={"token": auth.make_token(who.user)}, socketio_path="socket.io")
        clients.append(c)
        return c, got

    yield connect
    for c in clients:
        await c.disconnect()


async def settle():
    await asyncio.sleep(0.2)


async def test_a_token_is_needed_to_connect(url):
    for bad in ({}, {"token": "junk"}):
        c = socketio.AsyncClient()
        with pytest.raises(socketio.exceptions.ConnectionError):
            await c.connect(url, auth=bad, socketio_path="socket.io")


async def test_each_client_gets_only_its_own_events(make, world, connect):
    karim = await make("Karim", at=SPOT)
    yacine = await make("Yacine", at=NEAR)
    agent = await make("Agent", "dispatcher")
    citizen = await make("Other citizen")
    h1 = await make("H1", "hospital", hospital_id=world["h1"])
    h2 = await make("H2", "hospital", hospital_id=world["h2"])
    await register_device("bb-1", karim)
    _, k = await connect(karim)
    _, y = await connect(yacine)
    _, a = await connect(agent)
    _, o = await connect(citizen)
    _, hospital1 = await connect(h1)
    _, hospital2 = await connect(h2)

    await crash()
    await settle()
    assert [n["kind"] for n in k["notification"]] == ["check"] and k["notification"][0]["remaining_s"] > 0
    assert [n["kind"] for n in y["notification"]] == ["witness"]
    assert o["notification"] == [] and a["notification"] == []
    assert a["incident.updated"] and a["incident.updated"][-1]["id"] == 1
    for who in (k, y, o):
        assert who["incident.updated"] == []        # incidents go to dispatchers only

    await agent.post("/incidents/1/assign-hospital", {"hospital_id": world["h1"]})
    await settle()
    assert len(hospital1["hospital.incoming"]) == 1 and hospital1["hospital.incoming"][0]["profile"]["name"] == "Karim"
    assert hospital2["hospital.incoming"] == [] and k["hospital.incoming"] == []

    await agent.post("/ambulances/amb-1/location", {"lat": 36.3, "lon": 3.9})
    await settle()
    assert a["ambulance.moved"] and k["ambulance.moved"] == [] and hospital1["ambulance.moved"] == []


async def test_answers_reach_the_phone_that_answered(make, env, connect):
    karim = await make("Karim", at=SPOT)
    await register_device("bb-1", karim)
    _, k = await connect(karim)
    await crash()
    await settle()
    note = k["notification"][0]
    await karim.post(f"/notifications/{note['id']}/respond", {"action": "help"})
    await settle()
    assert k["notification"][-1]["state"] == "answered" and k["notification"][-1]["answer"] == "help"


async def test_only_dispatchers_can_watch_an_incident(make, env, connect):
    agent, citizen = await make("Agent", "dispatcher"), await make("C")
    ca, a = await connect(agent)
    cc, _ = await connect(citizen)
    await crash()
    assert (await ca.call("incident.watch", {"id": 1}))["ok"] is True
    assert (await cc.call("incident.watch", {"id": 1}))["ok"] is False


def stubbed(caplog, kind):
    return [r.message for r in caplog.records if f"{kind} stub" in r.message and "We detected a crash" in r.message]


async def people_around_a_crash(make):
    """Karim, and a witness so that the check-in is his only notification."""
    karim = await make("Karim", at=SPOT)
    await make("Yacine", at=NEAR)
    await register_device("bb-1", karim)
    await karim.post("/me/push-token", {"token": "ExponentPushToken[abc]"})
    return karim


async def test_an_offline_phone_gets_a_push_at_once_then_an_sms_before_the_deadline(make, env, caplog):
    caplog.set_level(logging.INFO, logger="solvex")
    await people_around_a_crash(make)
    await crash()
    assert len(stubbed(caplog, "push")) == 1
    assert stubbed(caplog, "sms") == []
    env.advance(settings.sms_after_s + 1)
    assert settings.sms_after_s < settings.checkin_s
    await scheduler.tick()
    assert len(stubbed(caplog, "sms")) == 1
    env.advance(5)
    await scheduler.tick()
    assert len(stubbed(caplog, "push")) == 1 and len(stubbed(caplog, "sms")) == 1    # once each


async def test_a_connected_phone_that_stays_silent_gets_a_push(make, env, caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="solvex")
    await people_around_a_crash(make)
    monkeypatch.setattr(realtime, "is_online", lambda uid: True)
    await crash()
    assert stubbed(caplog, "push") == []
    env.advance(settings.push_after_s + 1)
    await scheduler.tick()
    assert len(stubbed(caplog, "push")) == 1


async def test_an_acknowledged_notification_goes_no_further(make, env, caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="solvex")
    karim = await people_around_a_crash(make)
    monkeypatch.setattr(realtime, "is_online", lambda uid: True)
    await crash()
    note = (await karim.get("/notifications")).json()[0]
    await karim.post(f"/notifications/{note['id']}/ack")
    env.advance(settings.sms_after_s + 1)
    await scheduler.tick()
    assert stubbed(caplog, "push") == [] and stubbed(caplog, "sms") == []
