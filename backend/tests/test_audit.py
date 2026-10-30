"""Regressions from the audit of the first version: each of these was a real hole."""
import logging

from sqlalchemy import select

from backend import auth, clock, db, scheduler
from backend.incidents import service as incidents
from backend.models import DeadZoneCheckin, Incident
from backend.tests.helpers import SPOT, crash, get_incident, notes, register_device

NEAR = (SPOT[0] + 0.0009, SPOT[1])
ZONE = {"center": [36.2800, 3.8600], "radius_m": 1500}
ENTRY = [36.2900, 3.8600]
OUTSIDE = (36.3000, 3.8600)


async def test_a_real_crash_is_not_lost_when_merged_into_a_false_alarm(make, env):
    """Car A brakes hard, car B crashes 100 m away before A's M2 answers. A turns out to be nothing."""
    await incidents.on_trigger("bb-a", 8, 2.0, {"lat": SPOT[0], "lon": SPOT[1]})
    await incidents.on_trigger("bb-b", 8, 5.0, {"lat": NEAR[0], "lon": NEAR[1]})
    await incidents.on_prediction({"device_id": "bb-a", "trigger_seq": 8, "level": "none", "p_crash": 0.01}, None)
    await incidents.on_prediction({"device_id": "bb-b", "trigger_seq": 8, "level": "crash", "p_crash": 0.99},
                                  {"dv_kmh": 30.0, "pdof_deg": 5, "rollover": 0, "multi_impact": 0})
    async with db.session() as s:
        alive = [i for i in (await s.scalars(select(Incident))).all() if i.status == "detected"]
    assert len(alive) == 1 and alive[0].m3_level


async def test_the_second_victim_of_a_merged_crash_is_not_forgotten(make, env):
    a = await make("Aicha", at=SPOT, age=30, sex="female")
    b = await make("Bilal", at=NEAR, age=30, sex="male")
    await register_device("bb-a", a)
    await register_device("bb-b", b)
    await crash("bb-a", SPOT)
    await crash("bb-b", NEAR)
    assert len(await notes(b, "check")) == 1            # Bilal was in the crash: he is asked how he is, not whether he saw it
    agent = await make("Agent", "dispatcher")
    assert any("Bilal" in e["text"] for e in (await agent.get("/incidents/1")).json()["events"])


async def test_no_location_is_not_the_same_as_nobody_around(make, env):
    karim = await make("Karim")
    await register_device("bb-1", karim)
    await crash(at=None)
    inc = await get_incident()
    assert inc.lat is None and not inc.nobody_around


async def test_the_are_you_ok_reaches_an_offline_phone_before_its_deadline(make, env, caplog):
    caplog.set_level(logging.INFO, logger="solvex")
    karim = await make("Karim", at=SPOT)
    await make("Yacine", at=NEAR)
    await register_device("bb-1", karim)
    await karim.post("/me/push-token", {"token": "ExponentPushToken[abc]"})
    await crash()
    await scheduler.tick()                    # no socket: push at once, not after the deadline has passed
    assert any("push stub" in r.message and "Are you OK?" in r.message for r in caplog.records)


async def test_a_token_expires_on_the_rules_clock(make, env):
    u = await make("Karim")
    assert auth.read_token(auth.make_token(u.user))
    token = auth.make_token(u.user)
    env.advance(25 * 3600)
    assert auth.read_token(token) is None


async def test_a_long_password_is_refused_not_a_crash(api):
    r = await api.post("/api/v1/auth/register", json={"phone": "+213550000009", "password": "x" * 90, "name": "Long"})
    assert r.status_code == 422


async def test_a_driver_who_goes_silent_outside_the_mapped_zone_still_raises_the_alarm(make, env):
    """The whole point of the check-in: coverage ends where it ends, not where our circle says."""
    karim = await make("Karim", at=OUTSIDE, driving=True)
    await karim.post("/checkins", {"road": "RN 8B", "zone": ZONE, "entry": ENTRY, "back_in_min": 60})
    env.advance(61 * 60)
    await scheduler.tick()
    await scheduler.tick()
    assert (await get_incident()).status == "overdue"


async def test_a_driver_still_reporting_is_not_overdue(make, env):
    karim = await make("Karim", at=OUTSIDE, driving=True)
    await karim.post("/checkins", {"road": "RN 8B", "zone": ZONE, "entry": ENTRY, "back_in_min": 60})
    env.advance(61 * 60)
    await karim.post("/me/location", {"lat": OUTSIDE[0], "lon": OUTSIDE[1]})
    await scheduler.tick()
    assert (await get_incident()).status == "dead_zone_watch"


async def test_closing_a_dead_zone_incident_ends_the_check_in(make, env):
    agent = await make("Agent", "dispatcher")
    karim = await make("Karim", at=OUTSIDE, driving=True)
    await karim.post("/checkins", {"road": "RN 8B", "zone": ZONE, "entry": ENTRY, "back_in_min": 60})
    await agent.post("/incidents/1/close")
    env.advance(61 * 60)
    await scheduler.tick()
    assert (await get_incident()).status == "closed"
    assert (await karim.post("/checkins", {"road": "RN 8B", "zone": ZONE, "entry": ENTRY, "back_in_min": 60})).status_code == 201


async def test_return_acts_on_the_check_in_it_names(make, env):
    karim = await make("Karim", at=OUTSIDE)
    first = (await karim.post("/checkins", {"zone": ZONE, "entry": ENTRY, "back_in_min": 60})).json()
    await karim.post(f"/checkins/{first['id']}/return")
    second = (await karim.post("/checkins", {"zone": ZONE, "entry": ENTRY, "back_in_min": 60})).json()
    assert (await karim.post(f"/checkins/{first['id']}/return")).status_code == 409
    async with db.session() as s:
        assert (await s.get(DeadZoneCheckin, second["id"])).state == "driving"


async def test_a_helper_arriving_does_not_block_or_hide_the_ambulance(world, make):
    agent = await make("Agent", "dispatcher")
    helper = await make("Amel", at=NEAR, is_helper=True, skills="nurse")
    await crash()
    await agent.post("/incidents/1/call-helpers", {"radius_km": 5})
    note = (await notes(helper, "helper"))[0]
    await helper.post(f"/notifications/{note.id}/respond", {"action": "accept"})
    await helper.post("/me/location", {"lat": SPOT[0], "lon": SPOT[1]})
    d = (await agent.get("/incidents/1")).json()
    assert d["helpers"][str(helper.id)] == "on_scene" and d["stage"] == "detected"     # still needs an ambulance
    assert (await agent.post("/incidents/1/dispatch", {"ambulance_id": "amb-1"})).status_code == 200


async def test_closing_frees_the_ambulance_and_ends_what_was_waiting(world, make):
    agent = await make("Agent", "dispatcher")
    witness = await make("Yacine", at=NEAR)
    await crash()
    await agent.post("/incidents/1/dispatch", {"ambulance_id": "amb-1"})
    await agent.post("/incidents/1/close")
    ambulances = {a["id"]: a for a in (await agent.get("/ambulances")).json()}
    assert ambulances["amb-1"]["state"] == "available" and ambulances["amb-1"]["incident_id"] is None
    assert (await notes(witness, "witness"))[0].state == "expired"


async def test_a_crew_that_turns_back_lets_the_agent_send_another_ambulance(world, make):
    agent = await make("Agent", "dispatcher")
    await crash()
    await agent.post("/incidents/1/dispatch", {"ambulance_id": "amb-1"})
    await agent.post("/ambulances/amb-1/state", {"state": "busy"})       # broke down
    assert (await agent.get("/incidents/1")).json()["stage"] == "detected"
    assert (await agent.post("/incidents/1/dispatch", {"ambulance_id": "amb-2"})).status_code == 200


async def test_the_sos_tells_the_phone_its_check_in_is_over(make, env):
    karim = await make("Karim", at=SPOT)
    await register_device("bb-1", karim)
    await crash()
    from backend import realtime
    sos = (await notes(karim, "sos"))[0]
    realtime.tap.clear()
    await karim.post(f"/notifications/{sos.id}/respond", {"action": "silent"})
    changed = [d for room, event, d in realtime.tap if event == "notification" and d["kind"] == "check"]
    assert changed and changed[0]["state"] == "expired"


async def test_a_device_registered_after_its_first_packet_needs_its_key(env, make):
    import json

    from backend.telemetry.service import service
    from backend.tests.test_telemetry import Replies, packets
    service.cars.clear()
    owner = await make("Owner")
    raws = list(packets("bb-late", "crash", lead=3))
    await service.handle(raws[0], Replies())                              # unknown: accepted
    await owner.post("/devices", {"device_id": "bb-late"})
    clock_now = clock.now()
    ws = Replies()
    await service.handle(raws[1], ws)
    assert [m["code"] for m in ws.messages] == ["unauthorized"], (clock_now, json.dumps(ws.messages))


async def test_a_refused_packet_leaves_no_car_behind(env):
    from backend.config import settings
    from backend.telemetry.service import service
    from backend.tests.test_telemetry import Replies, packets
    service.cars.clear()
    settings.allow_unknown_devices = False
    await service.handle(next(packets("bb-stranger", "crash")), Replies())
    assert service.cars == {}


async def test_a_database_error_does_not_lose_the_crash_window(env, monkeypatch):
    """M2's answer matters more than the bookkeeping around it."""
    from backend import ml
    from backend.telemetry.service import service
    from backend.tests.test_telemetry import Replies, packets
    ml.m2_model()
    service.cars.clear()
    service.incidents = incidents

    async def broken(*a):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(incidents, "on_trigger", broken)
    ws = Replies()
    for raw in packets("bb-1", "crash"):
        await service.handle(raw, ws, trusted=True)
    assert [m["level"] for m in ws.messages if m["type"] == "prediction"] == ["crash"]


async def test_a_closed_incident_shows_no_personal_details_of_bystanders(make, env):
    agent = await make("Agent", "dispatcher")
    await make("Yacine", at=NEAR, age=28)
    await crash()
    witness = next(p for p in (await agent.get("/incidents/1")).json()["people"] if p["kind"] == "witness")
    assert "age" not in witness


async def test_the_agent_can_place_a_crash_with_no_gps(world, make):
    agent = await make("Agent", "dispatcher")
    witness = await make("Yacine", at=NEAR)
    await crash(at=None)
    r = await agent.post("/incidents/1/dispatch", {"ambulance_id": "amb-1"})
    assert r.status_code == 409 and r.json()["detail"] == "no_location"
    assert (await agent.post("/incidents/1/location", {"lat": SPOT[0], "lon": SPOT[1]})).status_code == 200
    assert len(await notes(witness, "witness")) == 1
    assert (await agent.post("/incidents/1/dispatch", {"ambulance_id": "amb-1"})).status_code == 200


async def test_gps_lost_in_the_crash_second_keeps_the_last_position(env):
    import json

    from backend.telemetry.service import service
    from backend.tests.test_telemetry import Replies, packets
    service.cars.clear()
    service.incidents = incidents
    ws = Replies()
    for i, raw in enumerate(packets("bb-1", "crash")):
        p = json.loads(raw)
        if i >= 5:
            p.pop("location")               # the GPS dies well before the impact
        await service.handle(json.dumps(p), ws, trusted=True)
    inc = await get_incident()
    assert inc.lat is not None


async def test_a_hospital_marks_the_patient_arrived(world, make):
    agent = await make("Agent", "dispatcher")
    staff = await make("H1", "hospital", hospital_id=world["h1"])
    await crash()
    await agent.post("/incidents/1/assign-hospital", {"hospital_id": world["h1"]})
    transfer = (await staff.get(f"/hospitals/{world['h1']}/incoming")).json()[0]["transfer_id"]
    other = await make("H2", "hospital", hospital_id=world["h2"])
    assert (await other.post(f"/hospitals/{world['h1']}/incoming/{transfer}/arrived")).status_code == 403
    assert (await staff.post(f"/hospitals/{world['h1']}/incoming/{transfer}/arrived")).status_code == 200
    assert (await staff.get(f"/hospitals/{world['h1']}/incoming")).json() == []
    assert (await staff.get("/hospitals")).json()[0]["beds_free"] == 4


async def test_the_first_report_can_confirm_after_a_merged_one_took_over(make, env):
    a = await make("Aicha", at=SPOT, age=30, sex="female")
    b = await make("Bilal", at=NEAR, age=30, sex="male")
    await register_device("bb-a", a)
    await register_device("bb-b", b)
    summary = {"dv_kmh": 30.0, "pdof_deg": 5, "rollover": 0, "multi_impact": 0}
    await incidents.on_trigger("bb-a", 8, 5.0, {"lat": SPOT[0], "lon": SPOT[1]})
    await incidents.on_trigger("bb-b", 8, 5.0, {"lat": NEAR[0], "lon": NEAR[1]})
    await incidents.on_prediction({"device_id": "bb-b", "trigger_seq": 8, "level": "crash", "p_crash": 0.99}, summary)   # B answers first
    await incidents.on_prediction({"device_id": "bb-a", "trigger_seq": 8, "level": "crash", "p_crash": 0.99}, summary)
    async with db.session() as s:
        rows = (await s.scalars(select(Incident))).all()
    assert len(rows) == 1 and rows[0].victim_id == b.id and rows[0].flags["others"] == [a.id]
    assert len(await notes(a, "check")) == 1 and len(await notes(b, "check")) == 1


async def test_two_agents_pressing_dispatch_together_send_one_ambulance(world, make):
    import asyncio
    first, second = await make("A1", "dispatcher"), await make("A2", "dispatcher")
    await crash()
    results = await asyncio.gather(first.post("/incidents/1/dispatch", {"ambulance_id": "amb-1"}),
                                   second.post("/incidents/1/dispatch", {"ambulance_id": "amb-2"}))
    assert sorted(r.status_code for r in results) == [200, 409]
    states = {a["id"]: a["state"] for a in (await first.get("/ambulances")).json()}
    assert sorted((states["amb-1"], states["amb-2"])) == ["available", "dispatched"]


async def test_two_answers_at_once_count_once(make, env):
    import asyncio
    karim = await make("Karim", at=SPOT)
    await make("Yacine", at=NEAR)
    await register_device("bb-1", karim)
    await crash()
    note = (await notes(karim, "check"))[0]
    results = await asyncio.gather(*(karim.post(f"/notifications/{note.id}/respond", {"action": "ok"}) for _ in range(2)))
    assert sorted(r.status_code for r in results) == [200, 409]
    assert (await get_incident()).confidence == "medium"       # lowered once, not twice


async def test_a_client_that_cannot_be_told_does_not_fail_the_request(world, make, monkeypatch):
    from backend import realtime
    agent = await make("Agent", "dispatcher")
    await crash()

    async def broken(*a):
        raise RuntimeError("socket gone")

    monkeypatch.setattr(realtime, "emit_dispatchers", broken)
    assert (await agent.post("/incidents/1/confirm", {})).status_code == 200
