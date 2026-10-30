"""The main rule: simulations are clients of the backend. Nothing outside sim/ may depend on it, and without the demo it doesn't exist."""
import re
from pathlib import Path

import httpx
from sqlalchemy import func, select

from backend import db
from backend.models import User

ROOT = Path(__file__).resolve().parent.parent
USES_SIM = re.compile(r"^\s*(from backend\.sim\b|import backend\.sim\b|from backend import .*\bsim\b)", re.M)


def test_nothing_outside_sim_imports_sim():
    # main.py is where the demo is plugged in, and only when it is on (the next test). Tests may look at the sim.
    outside = [p for p in ROOT.rglob("*.py") if p.relative_to(ROOT).parts[0] not in ("sim", "tests") and p.name != "main.py"]
    assert outside and [p.relative_to(ROOT).as_posix() for p in outside if USES_SIM.search(p.read_text())] == []


def test_main_only_loads_sim_when_the_demo_is_on():
    text = (ROOT / "main.py").read_text()
    for match in re.finditer(r"from backend import sim", text):
        before = text[:match.start()].splitlines()[-2:]
        assert any("settings.demo" in line for line in before), "sim loaded without checking settings.demo"


async def test_without_the_demo_there_is_no_dev_blackbox_or_seed(env):
    from backend.main import create_app
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for path in ("/dev", "/dev/state", "/dev/monitor", "/blackbox/x/crash", "/examples/crash.json"):
            assert (await client.get(path)).status_code == 404, path
        assert (await client.post("/dev/reset")).status_code in (404, 405)
        assert (await client.get("/health")).json()["demo"] is False
        assert (await client.get("/docs")).status_code == 200
    async with db.session() as s:
        assert await s.scalar(select(func.count()).select_from(User)) == 0


async def test_the_demo_routes_exist_only_with_the_demo(env):
    from backend.config import settings
    from backend.main import create_app
    settings.demo = True
    paths = set(create_app().openapi()["paths"])
    assert {"/dev", "/dev/start/{key}", "/blackbox/{device_id}/{scenario}"} <= paths
    settings.demo = False
