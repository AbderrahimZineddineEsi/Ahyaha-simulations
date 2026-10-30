"""Step 6: dead-zone check-ins. The overdue path survives a restart."""
from backend import db, scheduler
from backend.tests.helpers import get_incident, notes
from backend.notifications.service import notify

ENTRY = (36.2900, 3.8600)
ZONE = {"center": [36.2800, 3.8600], "radius_m": 1500}
INSIDE = (36.2810, 3.8600)
BACK = (36.3500, 3.8600)


async def start(make, minutes=60):
    agent = await make("Agent", "dispatcher")
    karim = await make("Karim", at=(36.34, 3.86), driving=True, emergency_contact_name="Samira", emergency_contact_phone="+213000")
    r = await karim.post("/checkins", {"road": "RN 8B", "zone": ZONE, "entry": list(ENTRY), "back_in_min": minutes})
    assert r.status_code == 201
    return agent, karim, r.json()


async def test_the_agent_watches_a_driver_heading_in(make, env):
    agent, karim, c = await start(make)
    d = (await agent.get(f"/incidents/{c['incident_id']}")).json()
    assert d["kind"] == "dead_zone" and d["status"] == "Heading into a no-coverage zone" and d["dead_zone"]["road"] == "RN 8B"
    assert (await karim.post("/checkins", {"zone": ZONE, "entry": list(ENTRY), "back_in_min": 5})).status_code == 409
    assert (await agent.post(f"/incidents/{c['incident_id']}/dispatch", {"ambulance_id": "amb-1"})).status_code == 409   # too early


async def test_warn_and_turn_back(make, env):
    agent, karim, c = await start(make)
    i = c["incident_id"]
    assert (await agent.post(f"/incidents/{i}/warn", {"message": "dont_go"})).status_code == 200
    assert (await agent.post(f"/incidents/{i}/warn", {"message": "dont_go"})).status_code == 409
    warning = (await notes(karim, "dz_warning"))[0]
    await karim.post(f"/notifications/{warning.id}/respond", {"action": "turn_back"})
    inc = await get_incident()
    assert inc.status == "turned_back" and inc.closed_at
    assert (await agent.get("/incidents")).json() == []        # no longer open


async def test_silence_in_the_zone_then_overdue_then_help(make, env, world):
    agent, karim, c = await start(make)
    i = c["incident_id"]
    await karim.post("/me/location", {"lat": INSIDE[0], "lon": INSIDE[1], "driving": True})
    await scheduler.tick()
    assert (await get_incident()).status == "dead_zone_watch"      # still reporting: not silent yet
    env.advance(11)
    await scheduler.tick()
    d = (await agent.get(f"/incidents/{i}")).json()
    assert d["stage"] == "in_zone" and d["dead_zone"]["last_seen"] == [INSIDE[0], INSIDE[1]]
    assert (await agent.post(f"/incidents/{i}/warn", {"message": "no_help"})).status_code == 409   # no network now
    assert (await agent.post(f"/incidents/{i}/call", {"who": "user"})).status_code == 200
    assert "no network" in (await agent.get(f"/incidents/{i}")).json()["events"][-1]["text"]
    env.advance(59 * 60)
    await scheduler.tick()
    assert (await get_incident()).status == "in_zone"
    await db.dispose()                       # the server restarts while the driver is out of reach
    env.advance(2 * 60)
    await scheduler.tick()
    d = (await agent.get(f"/incidents/{i}")).json()
    assert d["stage"] == "overdue" and d["status"] == "Not back: last known position"
    texts = [e["text"] for e in d["events"]]
    assert any("Emergency contact warned first: Samira" in t for t in texts) and any("Gendarmerie" in t for t in texts)
    # Now help can go to the last known position
    assert (await agent.post(f"/incidents/{i}/dispatch", {"ambulance_id": "amb-1"})).status_code == 200
    assert (await agent.post(f"/incidents/{i}/found", {"lat": 36.25, "lon": 3.86, "by": "Ambulance 1"})).status_code == 200
    d = (await agent.get(f"/incidents/{i}")).json()
    assert d["status"] == "Found: it was a crash" and d["location"]["lat"] == 36.25
    # The ambulance now knows where to go, and arrives there
    await agent.post("/ambulances/amb-1/location", {"lat": 36.25, "lon": 3.86})
    d = (await agent.get(f"/incidents/{i}")).json()
    assert d["dispatch"]["state"] == "on_scene" and any(a["state"] == "on_scene" for a in d["ambulances"])


async def test_a_driver_who_comes_back_closes_it(make, env):
    agent, karim, c = await start(make)
    await karim.post("/me/location", {"lat": INSIDE[0], "lon": INSIDE[1]})
    env.advance(11)
    await scheduler.tick()
    await karim.post("/me/location", {"lat": BACK[0], "lon": BACK[1]})
    inc = await get_incident()
    assert inc.status == "returned" and inc.closed_at


async def test_the_apps_question_and_its_default(make, env):
    agent = await make("Agent", "dispatcher")
    karim = await make("Karim", at=(36.34, 3.86))
    data = {"road": "RN 8B", "zone": ZONE, "entry": list(ENTRY), "default": "eta30",
            "back": {"1h": ["In 1 hour", 60], "eta30": ["ETA + 30 min", 55]}}
    actions = [{"id": k, "label": v[0]} for k, v in data["back"].items()]
    async with db.session_scope() as s:
        await notify(s, karim.id, "dz_back", "No network ahead", "When will you be back?", actions, 15, data=data)
    note = (await notes(karim, "dz_back"))[0]
    await karim.post(f"/notifications/{note.id}/respond", {"action": "1h"})
    assert (await agent.get("/incidents")).json()[0]["kind"] == "dead_zone"
    # Second driver: no answer, so the route's ETA is used
    lina = await make("Lina", at=(36.34, 3.86))
    async with db.session_scope() as s:
        await notify(s, lina.id, "dz_back", "No network ahead", "When will you be back?", actions, 15, data=data)
    env.advance(16)
    await scheduler.tick()
    incs = (await agent.get("/incidents")).json()
    assert len(incs) == 2
    d = (await agent.get(f"/incidents/{incs[0]['id']}")).json()
    assert d["dead_zone"]["back_defaulted"] and "no answer" in d["events"][0]["text"]
