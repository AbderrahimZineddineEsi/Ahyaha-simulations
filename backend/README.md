# SolveX backend

One FastAPI + Socket.IO app for the React Native app, the Protection Civile site and the hospitals. The API is in [API.md](API.md), the blackbox's telemetry messages in [PROTOCOL.md](PROTOCOL.md), the demo script in [DEMO.md](DEMO.md).

```bash
pip install -r requirements.txt
python download_models.py
python -m backend.main                  # port 8000: / (dashboard), /dev (demo), /docs (API), /health
python -m backend.main --port 9000
SOLVEX_DEMO=0 python -m backend.main    # production shape: no /dev, no /blackbox, no seed accounts
```

The first start creates `backend/solvex.db` (SQLite) and applies the migrations. In demo mode it also seeds the demo accounts (dispatcher `+213000000100`, password `demo`).

Only one command is needed from another PC to test a blackbox: `curl -N http://<server-ip>:8000/blackbox/blackbox-01/crash`. Port 8000 has to be open in the firewall.

## The main rule

**Simulations are clients of the backend, not part of it.** Everything about the demo lives in `sim/`: the seed data, the scenario scripts, the `/dev` and `/blackbox` routes. A scenario is seeded database rows plus a script that does what real clients do: a blackbox sends telemetry, ambulances and helpers report positions, phones answer notifications. The services never import `sim/`, and `tests/test_boundary.py` enforces it. The few places where the demo has to change what the services see are hooks the sim fills in: `routing.register()` (saved routes), `Severity.source` (the CISS case of a scenario), and the event bus.

## Layout

```
main.py            the app, startup, the scheduler's task
config.py          settings from the environment
db.py, models.py   SQLAlchemy async, alembic/ (migrations)
auth.py, deps.py   passwords, JWT, device keys, roles
realtime.py        Socket.IO: login on connect, rooms
scheduler.py       deadlines: notification expiry, push and SMS escalation, the dead-zone watch
clock.py, bus.py   the clock tests move, and the in-process event bus
telemetry/         WebSocket ingest, the trigger and 16 s windows (stream.py), M2 on each window
ml/                M2, M3 and M4 at inference, and the choice of what goes into M3
incidents/         the rules after M2 says crash (service.py), what clients see (view.py), the routes
notifications/     sending, deadlines, answers, push and SMS escalation
fleet/             ambulances: dispatch, positions, ETA, yellow line; helpers
geo/               distances and routes (OSRM, saved routes, straight line)
checkins/          the dead-zone check-ins
hospitals/         facilities, beds, destination, the hospital's view of a patient
users/             accounts, profiles, locations, devices
sim/               the demo (see above)
web/               the Protection Civile dashboard, the /dev pages and their static files
tests/             pytest; tests/parity/ drives a running server
```

## How an incident runs

1. A blackbox packet with 1 g on x-y is a **trigger**: an incident opens at once (`analyzing`), so the agent sees the impact. If the blackbox's owner is unknown the incident is flagged `unknown_victim`.
2. 8 s later **M2** answers. `none` or `possible` closes it as a false alarm. `likely` or `crash` runs **M3 and M4** on `crash_summary()` and the owner's profile (a default adult, flagged, when there is none), asks the victim "Are you OK?" (30 s, 15 s in the demo), and alerts citizens within 2.5 km who sent a location in the last 15 min. If nobody is near, confidence stays high whatever the victim answers, and they also get the SOS card.
3. The agent confirms, dispatches an ambulance, calls helpers (5 km, then 20 km), confirms one, and sends the patient to a hospital.
4. Ambulances report their position; the rules update the ETA, alert drivers on the ambulance's road, and mark the arrival within 60 m. Helpers' locations do the same for them.

Several incidents can be open at once, and reports of the same crash (same blackbox, or within 300 m, within 5 minutes) merge. A merged report that sees a crash takes the incident over if nothing confirmed it yet, and the other car's owner gets their own check-in, though the incident's severity and profile stay one person's.

## Tests

```bash
pytest                                  # about 10 s: rules with a fake clock, the API, real Socket.IO clients, migrations
python -m backend.tests.parity.scenarios all     # against a running demo server: the four scenarios, see tests/parity/README.md
```

The tests use real M2, M3 and M4 and a temporary SQLite file, never `backend/solvex.db`. Run the parity checks against a copy of the server on its own port and database (`DATABASE_URL=sqlite+aiosqlite:///... python -m backend.main --port 8002`): **Reset all** deletes incidents.

## Settings

| Variable | Default | |
|---|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///backend/solvex.db` | a Postgres URL (`postgresql+asyncpg://...`, with `asyncpg` installed) should be the only change; not tried yet |
| `JWT_SECRET` | a dev value | **set it** anywhere but your laptop |
| `SOLVEX_DEMO` | `1` | `0` removes `/dev`, `/blackbox`, `/examples` and the seed accounts |
| `ALLOW_UNKNOWN_DEVICES` | `1` | `0` refuses telemetry from blackboxes nobody registered |
| `OSRM` / `OSRM_URL` | on, off in the demo / the public test server | real road routes and ETAs, with a straight line at 50 km/h as the fallback. The demo uses saved routes. Production needs its own OSRM server. |
| `EXPO_PUSH` | off | real push needs an Expo project token |

Migrations: `alembic -c backend/alembic.ini revision --autogenerate -m "what changed"`. The app applies them at startup.

## Known limits

- One incident holds one person's severity and profile: a crash with several victims shows the others by name only (PROJECT.md S7).
- `crash_summary()` can underestimate ΔV (MODEL_SPEC.md), so severity from a real blackbox can come out low until that is fixed.
- Push, SMS, phone calls and the Gendarmerie alert are stubs that write to the log or the timeline.
- The demo's `Reset all` and the sim's seed data touch the real tables: demo mode is not for a database that matters.
- `backend/sim/tools/fetch_map.py` rebuilds the demo's saved routes and street data (needs internet, once).
