"""
The endpoints of the Protection Civile side: the live feed for the dashboard and /dev, what phones answer, and what
the agent does. The mobile app will use /ws/user/{id} and /api/notifications/{id}/respond.
"""
from typing import Literal

from fastapi import APIRouter, HTTPException, WebSocket
from pydantic import BaseModel

from demo_data import PLACES

ERRORS = {"not_found": 404, "closed": 409, "bad_action": 422, "no_crash": 409, "bad_severity": 422, "unavailable": 409,
          "already_dispatched": 409, "nobody_left": 409, "not_accepted": 409, "bad_radius": 422,
          "no_network": 409, "already_sent": 409}


class Answer(BaseModel):
    action: str


class Confirm(BaseModel):
    severity: str | None = None


class Dispatch(BaseModel):
    ambulance_id: str


class Helper(BaseModel):
    user_id: int


class CallHelp(BaseModel):
    radius_km: int


class Phone(BaseModel):
    who: Literal["user", "contact"]


class Warning(BaseModel):
    message: Literal["dont_go", "no_help"]


class Auto(BaseModel):
    on: bool


def make_router(engine):
    router = APIRouter()

    def check(result):
        if result != "ok":
            raise HTTPException(ERRORS.get(result, 400), result)
        return {"status": "ok"}

    def current(incident_id):
        if engine.incident is None or engine.incident["id"] != incident_id:
            raise HTTPException(404, "no such incident")

    @router.websocket("/ws/ops")
    async def ops_feed(ws: WebSocket):
        """The live feed: the full state now, and again on every change. It only listens."""
        await ws.accept()
        await ws.send_json(engine.snapshot())
        engine.ops.add(ws)
        try:
            while (await ws.receive())["type"] != "websocket.disconnect":
                pass
        finally:
            engine.ops.discard(ws)

    @router.websocket("/ws/user/{user_id}")
    async def user_feed(ws: WebSocket, user_id: int):
        """The notifications of one user: what the mobile app will listen to."""
        await ws.accept()
        for note in engine.notifications:
            if note["user_id"] == user_id and note["state"] == "open":
                await ws.send_json({"type": "notification", **engine.public_note(note)})
        engine.user_sockets.setdefault(user_id, set()).add(ws)
        try:
            while (await ws.receive())["type"] != "websocket.disconnect":
                pass
        finally:
            engine.user_sockets[user_id].discard(ws)

    @router.get("/api/state")
    def state():
        return engine.snapshot()

    @router.get("/api/map")
    def map_data():
        return PLACES

    @router.post("/api/notifications/{note_id}/respond")
    async def respond(note_id: int, body: Answer):
        return check(await engine.respond(note_id, body.action))

    @router.post("/api/incidents/{incident_id}/confirm")
    async def confirm(incident_id: int, body: Confirm):
        current(incident_id)
        return check(await engine.agent_confirm(body.severity))

    @router.post("/api/incidents/{incident_id}/dispatch")
    async def dispatch(incident_id: int, body: Dispatch):
        current(incident_id)
        return check(await engine.dispatch(body.ambulance_id))

    @router.post("/api/incidents/{incident_id}/call-helpers")
    async def call_helpers(incident_id: int, body: CallHelp | None = None):
        current(incident_id)
        return check(await engine.call_helpers(body.radius_km) if body else await engine.call_helpers())

    @router.post("/api/incidents/{incident_id}/call")
    async def call_person(incident_id: int, body: Phone):
        current(incident_id)
        return check(await engine.call_person(body.who))

    @router.post("/api/incidents/{incident_id}/warn")
    async def warn(incident_id: int, body: Warning):
        current(incident_id)
        return check(await engine.warn_driver(body.message))

    @router.post("/api/incidents/{incident_id}/acknowledge")
    async def acknowledge(incident_id: int):
        current(incident_id)
        return check(await engine.acknowledge())

    @router.post("/api/incidents/{incident_id}/choose-helper")
    async def choose_helper(incident_id: int, body: Helper):
        current(incident_id)
        return check(await engine.choose_helper(body.user_id))

    @router.post("/dev/auto")
    async def auto(body: Auto):
        engine.auto = body.on
        await engine.broadcast()
        return {"auto": engine.auto}

    return router
