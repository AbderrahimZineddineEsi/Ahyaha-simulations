import asyncio
import dataclasses

import httpx
import pytest

from backend import auth, bus, clock, db, ml, realtime
from backend.config import settings
from backend.geo import routing
from backend.incidents import core
from backend.models import Ambulance, Centre, Hospital
from backend.users import service as users

CENTRE = (36.378, 3.859)
CRASH = (36.3739, 3.9141)


@pytest.fixture(scope="session")
def severity():
    return ml.severity_model()


@pytest.fixture
async def env(tmp_path, severity):
    """A fresh database with every migration applied, a clock the test moves, and no network."""
    from backend import main
    saved = dataclasses.replace(settings)
    settings.database_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    settings.bcrypt_rounds = 4
    settings.demo = False
    settings.osrm_enabled = False
    settings.expo_push = False
    await db.dispose()
    await db.migrate()
    fake = clock.FakeClock()
    clock.set_clock(fake)
    bus.clear()
    routing.clear()
    realtime.tap = []
    ml.use(severity=severity)
    ml._loaded["severity"].source = None
    core.lock = asyncio.Lock()     # each test has its own event loop
    main.wire()
    yield fake
    realtime.tap = None
    bus.clear()
    routing.clear()
    clock.set_clock(clock.Clock())
    await db.dispose()
    for f in dataclasses.fields(saved):
        setattr(settings, f.name, getattr(saved, f.name))


@pytest.fixture
async def api(env):
    from backend.main import fastapi_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fastapi_app), base_url="http://test") as client:
        yield client


class Who:
    """A user and a way to call the API as them."""
    def __init__(self, api, user):
        self.api, self.user, self.id = api, user, user.id
        self.headers = {"Authorization": f"Bearer {auth.make_token(user)}"}

    async def get(self, path, **kw):
        return await self.api.get("/api/v1" + path, headers=self.headers, **kw)

    async def post(self, path, json=None, **kw):
        return await self.api.post("/api/v1" + path, json=json, headers=self.headers, **kw)

    async def patch(self, path, json=None, **kw):
        return await self.api.patch("/api/v1" + path, json=json, headers=self.headers, **kw)


@pytest.fixture
def make(api):
    """make(name, role=..., at=(lat, lon), **profile) -> Who"""
    counter = iter(range(1, 1000))

    async def make(name, role="citizen", at=None, driving=False, **fields):
        async with db.session_scope() as s:
            user = await users.create_user(s, f"+2135{next(counter):08d}", "secret1", name, role, **fields)
            if at:
                await users.update_location(s, user, at[0], at[1], driving)
        return Who(api, user)
    return make


@pytest.fixture
async def world(env):
    """One centre, three ambulances, two hospitals."""
    async with db.session_scope() as s:
        centre = Centre(name="Protection Civile Bouira", lat=CENTRE[0], lon=CENTRE[1])
        s.add(centre)
        await s.flush()
        s.add_all([Ambulance(id="amb-1", centre_id=centre.id, name="Ambulance 1", lat=CENTRE[0], lon=CENTRE[1]),
                   Ambulance(id="amb-2", centre_id=centre.id, name="Ambulance 2", prep_s=60, lat=CENTRE[0], lon=CENTRE[1]),
                   Ambulance(id="amb-3", centre_id=centre.id, name="Ambulance 3", state="busy", lat=CENTRE[0], lon=CENTRE[1])])
        h1 = Hospital(name="Hopital Bouira", lat=36.37, lon=3.90, specialties=["trauma", "orthopedics"], beds_total=20, beds_free=5)
        h2 = Hospital(name="CHU Alger", lat=36.75, lon=3.06, specialties=["trauma", "neurosurgery", "thoracic", "orthopedics"], beds_total=100, beds_free=10)
        s.add_all([h1, h2])
        await s.flush()
        return {"centre": centre.id, "h1": h1.id, "h2": h2.id}
