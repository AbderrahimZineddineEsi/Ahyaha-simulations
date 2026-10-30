"""Step 7: facilities, beds, destination, the patient's view for the hospital they are going to."""
from backend import db
from backend.models import Incident
from backend.tests.helpers import SPOT, crash, register_device


async def staff(make, world):
    return (await make("H1", "hospital", hospital_id=world["h1"]), await make("H2", "hospital", hospital_id=world["h2"]))


async def test_beds_belong_to_their_own_hospital(world, make):
    h1, h2 = await staff(make, world)
    assert (await h1.patch(f"/hospitals/{world['h1']}/beds", {"beds_free": 2})).json()["beds_free"] == 2
    assert (await h1.patch(f"/hospitals/{world['h2']}/beds", {"beds_free": 2})).status_code == 403
    assert (await h1.patch(f"/hospitals/{world['h1']}/beds", {"beds_free": 99})).status_code == 422
    citizen = await make("C")
    assert (await citizen.get("/hospitals")).status_code == 403
    assert len((await h2.get("/hospitals")).json()) == 2


async def test_suggestion_follows_injuries_beds_and_distance(world, make):
    agent = await make("Agent", "dispatcher")
    await crash("bb-1", SPOT)
    async with db.session_scope() as s:       # a likely head injury
        inc = await s.get(Incident, 1)
        inc.severity = {**inc.severity, "m4": [{"region": "head", "p": 0.5, "state": "likely"}]}
    ranked = (await agent.get("/incidents/1/hospital-suggestions")).json()
    assert ranked[0]["name"] == "CHU Alger" and ranked[1]["missing_specialties"] == ["neurosurgery"]
    async with db.session_scope() as s:       # no head injury: the near one wins
        inc = await s.get(Incident, 1)
        inc.severity = {**inc.severity, "m4": [{"region": "legs", "p": 0.5, "state": "likely"}]}
    assert (await agent.get("/incidents/1/hospital-suggestions")).json()[0]["name"] == "Hopital Bouira"
    await db.dispose()
    h1 = await make("Full", "hospital", hospital_id=world["h1"])
    await h1.patch(f"/hospitals/{world['h1']}/beds", {"beds_free": 0})
    assert (await agent.get("/incidents/1/hospital-suggestions")).json()[0]["name"] == "CHU Alger"


async def test_a_hospital_sees_only_its_own_patients(world, make):
    agent = await make("Agent", "dispatcher")
    h1, h2 = await staff(make, world)
    karim = await make("Karim", at=SPOT, age=45, sex="male", blood_type="O+", conditions="Hypertension")
    await register_device("bb-1", karim)
    await crash()
    assert (await h1.get(f"/hospitals/{world['h1']}/incoming")).json() == []
    assert (await agent.post("/incidents/1/assign-hospital", {"hospital_id": world["h1"]})).status_code == 200
    mine = (await h1.get(f"/hospitals/{world['h1']}/incoming")).json()
    assert len(mine) == 1 and mine[0]["profile"]["blood_type"] == "O+" and mine[0]["severity"]
    assert (await h2.get(f"/hospitals/{world['h2']}/incoming")).json() == []
    assert (await h2.get(f"/hospitals/{world['h1']}/incoming")).status_code == 403
    assert (await h1.get("/incidents/1")).status_code == 403          # and no access to the incident itself
    # Sent somewhere else: the first hospital no longer sees the patient
    await agent.post("/incidents/1/assign-hospital", {"hospital_id": world["h2"]})
    assert (await h1.get(f"/hospitals/{world['h1']}/incoming")).json() == []
    assert len((await h2.get(f"/hospitals/{world['h2']}/incoming")).json()) == 1
