"""Step 2: sign-up, login, roles, profiles, locations, device keys."""
from backend import db
from backend.models import Device


async def test_register_login_and_profile(api):
    r = await api.post("/api/v1/auth/register", json={"phone": "+213550000001", "password": "secret1", "name": "Karim", "age": 45, "sex": "male",
                                                     "blood_type": "O+", "conditions": "Hypertension"})
    assert r.status_code == 201 and r.json()["user"]["role"] == "citizen"
    assert (await api.post("/api/v1/auth/register", json={"phone": "+213550000001", "password": "secret1", "name": "X"})).status_code == 409
    bad = await api.post("/api/v1/auth/login", json={"phone": "+213550000001", "password": "nope"})
    assert bad.status_code == 401
    token = (await api.post("/api/v1/auth/login", json={"phone": "+213550000001", "password": "secret1"})).json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    me = (await api.get("/api/v1/me", headers=h)).json()
    assert me["blood_type"] == "O+" and me["age"] == 45
    await api.patch("/api/v1/me", json={"conditions": "None", "is_helper": True}, headers=h)
    assert (await api.get("/api/v1/me", headers=h)).json()["is_helper"] is True


async def test_nobody_logged_out(api):
    assert (await api.get("/api/v1/me")).status_code == 401
    assert (await api.get("/api/v1/me", headers={"Authorization": "Bearer junk"})).status_code == 401


async def test_register_cannot_choose_role(api):
    r = await api.post("/api/v1/auth/register", json={"phone": "+213550000002", "password": "secret1", "name": "Sneaky", "role": "admin"})
    assert r.json()["user"]["role"] == "citizen"


async def test_roles_guard_the_agent_api(make):
    citizen, dispatcher, hospital, admin = await make("C"), await make("D", "dispatcher"), await make("H", "hospital"), await make("A", "admin")
    assert (await citizen.get("/incidents")).status_code == 403
    assert (await hospital.get("/incidents")).status_code == 403
    assert (await dispatcher.get("/incidents")).status_code == 200
    assert (await admin.get("/incidents")).status_code == 200      # admins pass wherever dispatchers do
    assert (await dispatcher.get("/admin/users")).status_code == 403
    assert (await admin.get("/admin/users")).status_code == 200
    assert (await citizen.get("/ambulances")).status_code == 403


async def test_only_admin_creates_staff(make):
    admin, citizen = await make("A", "admin"), await make("C")
    body = {"phone": "+213660000001", "password": "secret1", "name": "Agent", "role": "dispatcher"}
    assert (await citizen.post("/admin/users", body)).status_code == 403
    assert (await admin.post("/admin/users", body)).status_code == 201


async def test_location_is_saved_and_validated(make):
    u = await make("Yacine")
    assert (await u.post("/me/location", {"lat": 36.37, "lon": 3.9, "driving": True})).status_code == 200
    loc = (await u.get("/me")).json()["location"]
    assert loc["lat"] == 36.37
    assert (await u.post("/me/location", {"lat": 99, "lon": 3.9})).status_code == 422


async def test_device_keys(make):
    owner, other = await make("Owner"), await make("Other")
    r = await owner.post("/devices", {"device_id": "bb-1"})
    assert r.status_code == 201 and len(r.json()["device_key"]) > 20
    assert (await owner.post("/devices", {"device_id": "bb-1"})).status_code == 409
    assert (await other.post("/devices", {"device_id": "bb-2", "owner_id": owner.id})).status_code == 403
    async with db.session() as s:
        device = await s.get(Device, "bb-1")
    assert device.owner_id == owner.id and device.key_hash != r.json()["device_key"]   # only the hash is stored
