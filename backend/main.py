"""
SolveX backend: one FastAPI + Socket.IO app for the React Native app, the Protection Civile site and the hospitals.

    python -m backend.main               # all interfaces, port 8000
    python -m backend.main --port 9000
    SOLVEX_DEMO=0 python -m backend.main # no /dev, no /blackbox, no seed data
"""
import argparse
import asyncio
import logging
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import socketio
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

if __name__ == "__main__":  # python backend/main.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import db, ml, scheduler  # noqa: E402
from backend.checkins import routes as checkin_routes  # noqa: E402
from backend.checkins import service as checkins  # noqa: E402
from backend.config import settings  # noqa: E402
from backend.fleet import routes as fleet_routes  # noqa: E402
from backend.fleet import service as fleet  # noqa: E402
from backend.hospitals import routes as hospital_routes  # noqa: E402
from backend.incidents import routes as incident_routes  # noqa: E402
from backend.incidents import service as incidents  # noqa: E402
from backend.notifications import routes as notification_routes  # noqa: E402
from backend.realtime import sio  # noqa: E402
from backend.telemetry import routes as telemetry_routes  # noqa: E402
from backend.telemetry.service import MAX_MESSAGE_BYTES, service as telemetry  # noqa: E402
from backend.telemetry.stream import AFTER_S, BEFORE_S  # noqa: E402
from backend.users import routes as user_routes  # noqa: E402

log = logging.getLogger("solvex")
WEB = Path(__file__).resolve().parent / "web"
API = "/api/v1"


def wire():
    """Connects the parts that must not import each other."""
    telemetry.incidents = incidents
    fleet.register()
    checkins.register()


@asynccontextmanager
async def lifespan(app):
    await db.migrate()
    wire()
    if not settings.demo and settings.jwt_secret.startswith("dev-secret"):
        log.warning("JWT_SECRET is the development value: anyone can forge a login. Set JWT_SECRET.")
    if settings.demo:
        from backend import sim
        await sim.start(app)
    await asyncio.to_thread(lambda: (ml.m2_model(), ml.severity_model()))
    tasks = [asyncio.create_task(scheduler.run()), asyncio.create_task(telemetry.watch_for_silence())]
    yield
    for task in tasks:
        task.cancel()
    if settings.demo:
        from backend import sim
        await sim.stop()
    await db.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="SolveX backend", lifespan=lifespan)
    for module in (user_routes, notification_routes, incident_routes, fleet_routes, hospital_routes, checkin_routes):
        app.include_router(module.router, prefix=API)
    app.include_router(telemetry_routes.router)

    @app.get("/health")
    def health():
        return {"status": "ok", "model": "m2", "cars_seen": len(telemetry.cars), "demo": settings.demo}

    @app.get("/")
    def dashboard():
        return FileResponse(WEB / "dashboard.html")

    if settings.demo:
        from backend import sim
        sim.install(app)
    app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")
    return app


fastapi_app = create_app()
app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app, socketio_path="socket.io")


def lan_ip():
    """The address other PCs reach this one on. A UDP connect sends nothing, it only picks the route."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    log.info("Trigger window: %d s before, %d s after", BEFORE_S, AFTER_S)
    log.info("Telemetry: ws://%s:%d/ws/telemetry", lan_ip(), args.port)
    log.info("Dashboard: http://localhost:%d/   API docs: http://localhost:%d/docs", args.port, args.port)
    if settings.demo:
        log.info("Demo is on: http://localhost:%d/dev", args.port)
    uvicorn.run(app, host=args.host, port=args.port, ws_max_size=MAX_MESSAGE_BYTES)
