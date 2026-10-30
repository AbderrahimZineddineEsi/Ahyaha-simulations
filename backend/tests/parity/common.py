import os
import time

import httpx

BASE = os.environ.get("SOLVEX_URL", "http://localhost:8000")
c = httpx.Client(base_url=BASE, timeout=30)
_token = c.post("/api/v1/auth/login", json={"phone": "+213000000100", "password": "demo"}).json()["token"]
H = {"Authorization": f"Bearer {_token}"}


def get(path):
    return c.get("/api/v1" + path, headers=H).json()


def post(path, body=None):
    r = c.post("/api/v1" + path, headers=H, json=body or {})
    return r.status_code


def current():
    """The detail of the newest open incident, or None."""
    open_ones = get("/incidents")
    return get(f"/incidents/{open_ones[0]['id']}") if open_ones else None


def wait(cond, limit=60, step=2):
    """Polls the newest incident until cond(detail) holds. Fails loudly after `limit` seconds."""
    start, d = time.time(), None
    while time.time() - start < limit:
        d = current()
        if d and cond(d):
            return d
        time.sleep(step)
    raise SystemExit(f"timed out after {limit} s; the incident was: {d and d['status']}")


def phone(number):
    """A demo user's own session: (headers, user). The demo seeds users 0 to 5."""
    user = c.get("/dev/state").json()["users"][number]
    return {"Authorization": f"Bearer {user['token']}"}, user


def reset(key, auto=False):
    c.post("/dev/reset")
    c.post("/dev/auto", json={"on": auto})
    return c.post(f"/dev/start/{key}").json()
