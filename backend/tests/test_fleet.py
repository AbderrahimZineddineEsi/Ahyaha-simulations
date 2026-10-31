"""Step 5: dispatch, ambulance positions, ETA, the yellow line, helper ranges."""
from sqlalchemy import select

from backend import db
from backend.config import settings
from backend.geo.distance import Path
from backend.models import Hospital, PoliceStation, PoliceUnit
from backend.tests.helpers import SPOT, crash, get_incident, notes, register_device
from backend.tests.conftest import CENTRE

NEAR = (SPOT[0] + 0.0009, SPOT[1])


async def confirmed_crash(world, make):
    agent = await make("Agent", "dispatcher")
    karim = await make("Karim", at=SPOT, age=45, sex="male")
    await register_device("bb-1", karim)
    await crash()
    await agent.post("/incidents/1/confirm", {})
    return agent, karim


async def test_dispatch_rules(world, make):
    agent, _ = await confirmed_crash(world, make)
    assert (await agent.post("/incidents/1/dispatch", {"ambulance_id": "amb-3"})).status_code == 409       # busy
    assert (await agent.post("/incidents/1/dispatch", {"ambulance_id": "nope"})).status_code == 409
    assert (await agent.post("/incidents/1/dispatch", {"ambulance_id": "amb-1"})).status_code == 200
    assert (await agent.post("/incidents/1/dispatch", {"ambulance_id": "amb-2"})).status_code == 409       # already sent one
    d = (await agent.get("/incidents/1")).json()
    assert d["stage"] == "dispatched" and d["dispatch"]["ambulance_id"] == "amb-1" and d["dispatch"]["eta_s"] > 0
    ambulances = {a["id"]: a for a in (await agent.get("/ambulances")).json()}
    assert ambulances["amb-1"]["state"] == "dispatched" and ambulances["amb-1"]["incident_id"] == 1


async def test_dispatch_waits_for_m2(world, make):
    agent = await make("Agent", "dispatcher")
    await crash(level="none")
    assert (await agent.post("/incidents/1/dispatch", {"ambulance_id": "amb-1"})).status_code == 409


async def test_eta_counts_prep_and_traffic(world, make):
    agent = await make("Agent", "dispatcher")
    await crash("bb-1", SPOT)
    await crash("bb-2", (SPOT[0] + 0.2, SPOT[1]), seq=3)
    await agent.post("/incidents/1/dispatch", {"ambulance_id": "amb-1"})
    base = (await agent.get("/incidents/1")).json()["dispatch"]["eta_s"]
    settings.traffic_factor = 2.5
    await agent.post("/incidents/2/dispatch", {"ambulance_id": "amb-2"})     # a different, further crash
    slow = (await agent.get("/incidents/2")).json()["dispatch"]["eta_s"]
    assert slow >= 60 + base * 2.5               # further, 2.5x traffic, and a minute of preparation


async def test_ambulance_moves_and_the_rules_react(world, make):
    agent, karim = await confirmed_crash(world, make)
    lina = await make("Lina", at=(36.3760, 3.9000), driving=True)       # on the ambulance's road
    walker = await make("Walker", at=(36.3760, 3.9000), driving=False)
    await agent.post("/incidents/1/dispatch", {"ambulance_id": "amb-1"})
    road = Path([CENTRE, SPOT])
    etas = []
    for f in (0.2, 0.5, 0.8):
        r = await agent.post("/ambulances/amb-1/location", {"lat": road.at(f)[0], "lon": road.at(f)[1]})
        assert r.status_code == 200
        etas.append(r.json()["eta_remaining_s"])
    assert etas[0] > etas[1] > etas[2] > 0
    # A driver on the ambulance's road is told to clear the way, once; someone on foot is not
    await lina.post("/me/location", {"lat": road.at(0.85)[0], "lon": road.at(0.85)[1], "driving": True})
    await agent.post("/ambulances/amb-1/location", {"lat": road.at(0.86)[0], "lon": road.at(0.86)[1]})
    await agent.post("/ambulances/amb-1/location", {"lat": road.at(0.87)[0], "lon": road.at(0.87)[1]})
    alerts = await notes(lina, "alert")
    assert len(alerts) == 1 and "yellow line" in alerts[0].text
    assert await notes(walker, "alert") == [] and await notes(karim, "alert") == []
    inc = await get_incident()
    assert inc.status == "dispatched"
    await agent.post("/ambulances/amb-1/location", {"lat": SPOT[0], "lon": SPOT[1]})
    inc = await get_incident()
    detail = (await agent.get("/incidents/1")).json()
    assert inc.status == "on_scene" and detail["dispatch"]["state"] == "on_scene"
    assert any("Ambulance 1 is on the scene" in e["text"] for e in detail["events"])
    assert any("Yellow-line alert sent to Lina" in e["text"] for e in detail["events"])


async def test_only_staff_report_positions(world, make):
    citizen = await make("C")
    assert (await citizen.post("/ambulances/amb-1/location", {"lat": 36.0, "lon": 3.0})).status_code == 403


async def test_helper_ranges_and_arrival(world, make):
    agent, karim = await confirmed_crash(world, make)
    near = await make("Amel", at=NEAR, is_helper=True, skills="nurse")
    middle = await make("Redouane", at=(SPOT[0] + 0.09, SPOT[1]), is_helper=True, skills="Red Crescent volunteer")   # about 10 km
    nonhelper = await make("Walker", at=NEAR)
    ranges = (await agent.get("/incidents/1")).json()["helper_ranges"]
    assert [(r["km"], r["count"], r["available"]) for r in ranges] == [(5, 1, 1), (20, 2, 2)]
    assert (await agent.post("/incidents/1/call-helpers", {"radius_km": 7})).status_code == 422
    assert (await agent.post("/incidents/1/call-helpers", {"radius_km": 5})).status_code == 200
    assert len(await notes(near, "helper")) == 1 and await notes(middle, "helper") == [] and await notes(nonhelper, "helper") == []
    assert (await agent.post("/incidents/1/call-helpers", {"radius_km": 5})).status_code == 409           # nobody left in 5 km
    assert (await agent.post("/incidents/1/call-helpers", {"radius_km": 20})).status_code == 200
    ranges = (await agent.get("/incidents/1")).json()["helper_ranges"]
    assert [(r["km"], r["count"], r["available"]) for r in ranges] == [(5, 1, 0), (20, 2, 0)]

    assert (await agent.post("/incidents/1/confirm-helper", {"user_id": near.id})).status_code == 409      # not accepted yet
    note = (await notes(near, "helper"))[0]
    await near.post(f"/notifications/{note.id}/respond", {"action": "accept"})
    assert (await agent.post("/incidents/1/confirm-helper", {"user_id": near.id})).status_code == 200
    assert "Go to the crash" in (await notes(near, "info"))[0].text
    decline = (await notes(middle, "helper"))[0]
    await middle.post(f"/notifications/{decline.id}/respond", {"action": "decline"})
    d = (await agent.get("/incidents/1")).json()
    assert d["helpers"] == {str(near.id): "confirmed", str(middle.id): "declined"}
    # She drives to the crash and reports her position: the rules see her arrive
    await near.post("/me/location", {"lat": SPOT[0] + 0.0001, "lon": SPOT[1], "driving": True})
    d = (await agent.get("/incidents/1")).json()
    assert d["helpers"][str(near.id)] == "on_scene" and any("Amel is on the scene" in e["text"] for e in d["events"])


async def police_world():
    """One unit 1 km from the crash, one about 11 km away, and one already busy on another job."""
    async with db.session_scope() as s:
        station = PoliceStation(name="Commissariat", force="police", lat=CENTRE[0], lon=CENTRE[1])
        s.add(station)
        await s.flush()
        s.add_all([PoliceUnit(id="pol-1", station_id=station.id, name="Police 1", force="police", lat=SPOT[0] + 0.009, lon=SPOT[1]),
                   PoliceUnit(id="gnd-1", station_id=station.id, name="Gendarmerie 1", force="gendarmerie", lat=SPOT[0] + 0.1, lon=SPOT[1]),
                   PoliceUnit(id="pol-2", station_id=station.id, name="Police 2", force="police", state="on_scene", lat=SPOT[0], lon=SPOT[1])])


async def test_police_go_at_once_and_arrive(world, make):
    agent, _ = await confirmed_crash(world, make)
    await police_world()
    ranges = (await agent.get("/incidents/1")).json()["police_ranges"]
    assert ranges == [{"km": 5, "count": 1, "available": 1}, {"km": 20, "count": 2, "available": 2}]

    assert (await agent.post("/incidents/1/call-police", {"radius_km": 7})).status_code == 422
    assert (await agent.post("/incidents/1/call-police", {"radius_km": 5})).status_code == 200
    d = (await agent.get("/incidents/1")).json()
    assert [(u["id"], u["state"]) for u in d["police"]] == [("pol-1", "en_route")]       # nobody had to accept
    assert d["police_ranges"][0] == {"km": 5, "count": 1, "available": 0}
    assert (await agent.post("/incidents/1/call-police", {"radius_km": 5})).status_code == 409

    await agent.post("/police/units/pol-1/location", {"lat": SPOT[0], "lon": SPOT[1]})
    assert (await agent.get("/incidents/1")).json()["police"][0]["state"] == "on_scene"
    units = {u["id"]: u for u in (await agent.get("/police/units")).json()}
    assert units["pol-1"]["state"] == "on_scene"

    await agent.post("/incidents/1/close")
    units = {u["id"]: u for u in (await agent.get("/police/units")).json()}
    assert units["pol-1"]["state"] == "available"


async def test_ambulance_takes_the_patient_to_the_hospital(world, make):
    agent, _ = await confirmed_crash(world, make)
    await agent.post("/incidents/1/dispatch", {"ambulance_id": "amb-1"})
    assert (await agent.post("/ambulances/amb-1/leave-scene")).status_code == 409        # not there yet
    await agent.post("/ambulances/amb-1/location", {"lat": SPOT[0], "lon": SPOT[1]})
    assert (await agent.post("/ambulances/amb-1/leave-scene")).json().get("detail") == "no_hospital"

    async with db.session() as s:
        h = await s.scalar(select(Hospital).where(Hospital.id == world["h1"]))
        beds, target = h.beds_free, (h.lat, h.lon)
    await agent.post("/incidents/1/assign-hospital", {"hospital_id": world["h1"]})
    assert (await agent.post("/ambulances/amb-1/leave-scene")).status_code == 200
    d = (await agent.get("/incidents/1")).json()
    assert d["stage"] == "transporting" and d["status"] == "Patient on the way to hospital"
    assert d["dispatch"]["state"] == "transport" and d["dispatch"]["route"][-1] == list(target) and d["dispatch"]["eta_remaining_s"] > 0
    assert (await agent.post("/incidents/1/assign-hospital", {"hospital_id": world["h2"]})).status_code == 409

    await agent.post("/ambulances/amb-1/location", {"lat": target[0], "lon": target[1]})
    d = (await agent.get("/incidents/1")).json()
    assert d["stage"] == "at_hospital" and d["hospital"]["state"] == "arrived" and d["dispatch"]["eta_remaining_s"] == 0
    ambulances = {a["id"]: a for a in (await agent.get("/ambulances")).json()}
    assert ambulances["amb-1"]["state"] == "available"
    async with db.session() as s:
        assert (await s.get(Hospital, world["h1"])).beds_free == beds - 1
