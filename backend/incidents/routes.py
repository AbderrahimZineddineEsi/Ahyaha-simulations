from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from backend import db
from backend.checkins import service as checkins
from backend.deps import current_user, get_session, require
from backend.fleet import service as fleet
from backend.hospitals import service as hospitals
from backend.incidents import core, service, view
from backend.models import Incident

router = APIRouter(tags=["incidents"])
ERRORS = {"not_found": 404, "closed": 409, "bad_action": 422, "no_crash": 409, "bad_severity": 422, "unavailable": 409,
          "already_dispatched": 409, "nobody_left": 409, "not_accepted": 409, "bad_radius": 422, "no_network": 409, "already_sent": 409, "no_location": 409}
agent = require("dispatcher")


class Confirm(BaseModel):
    severity: str | None = None


class Dispatch(BaseModel):
    ambulance_id: str


class Helper(BaseModel):
    user_id: int


class CallHelp(BaseModel):
    radius_km: int | None = None


class Phone(BaseModel):
    who: Literal["user", "contact"]


class Warning(BaseModel):
    message: Literal["dont_go", "no_help"]


class Found(BaseModel):
    lat: float
    lon: float
    by: str = "A responder"


class Hospital(BaseModel):
    hospital_id: int


class Place(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class Sos(BaseModel):
    kind: Literal["silent", "voice"]


async def get_incident(incident_id, s):
    inc = await s.get(Incident, incident_id)
    if inc is None:
        raise HTTPException(404, "no such incident")
    return inc


def check(result):
    if result != "ok":
        raise HTTPException(ERRORS.get(result, 400), result)
    return {"status": "ok"}


@router.get("/incidents")
async def incidents(open_only: bool = True, user=Depends(agent), s=Depends(get_session)):
    q = select(Incident).order_by(Incident.id.desc())
    if open_only:
        q = q.where(Incident.status.not_in(core.CLOSED))
    return [await view.summary(s, i) for i in (await s.scalars(q)).all()]


@router.get("/incidents/{incident_id}")
async def incident(incident_id: int, user=Depends(agent), s=Depends(get_session)):
    return await view.detail(s, await get_incident(incident_id, s))


async def act(s, incident_id, fn, *args):
    async with core.lock:
        inc = await get_incident(incident_id, s)
        result = await fn(s, inc, *args)
        if result == "ok":
            await db.commit(s)
    return check(result)


@router.post("/incidents/{incident_id}/confirm")
async def confirm(incident_id: int, body: Confirm, user=Depends(agent), s=Depends(get_session)):
    return await act(s, incident_id, service.agent_confirm, body.severity)


@router.post("/incidents/{incident_id}/dispatch")
async def dispatch(incident_id: int, body: Dispatch, user=Depends(agent), s=Depends(get_session)):
    return await act(s, incident_id, fleet.dispatch, body.ambulance_id)


@router.post("/incidents/{incident_id}/call-helpers")
async def call_helpers(incident_id: int, body: CallHelp | None = None, user=Depends(agent), s=Depends(get_session)):
    return await act(s, incident_id, fleet.call_helpers, body.radius_km if body else None)


@router.post("/incidents/{incident_id}/call")
async def call_person(incident_id: int, body: Phone, user=Depends(agent), s=Depends(get_session)):
    return await act(s, incident_id, service.call_person, body.who)


@router.post("/incidents/{incident_id}/confirm-helper")
async def confirm_helper(incident_id: int, body: Helper, user=Depends(agent), s=Depends(get_session)):
    return await act(s, incident_id, fleet.choose_helper, body.user_id)


@router.post("/incidents/{incident_id}/location")
async def location(incident_id: int, body: Place, user=Depends(agent), s=Depends(get_session)):
    return await act(s, incident_id, service.set_location, body.lat, body.lon)


@router.post("/incidents/{incident_id}/warn")
async def warn(incident_id: int, body: Warning, user=Depends(agent), s=Depends(get_session)):
    return await act(s, incident_id, checkins.warn, body.message)


@router.post("/incidents/{incident_id}/acknowledge")
async def acknowledge(incident_id: int, user=Depends(agent), s=Depends(get_session)):
    return await act(s, incident_id, checkins.acknowledge)


@router.post("/incidents/{incident_id}/found")
async def found(incident_id: int, body: Found, user=Depends(agent), s=Depends(get_session)):
    return await act(s, incident_id, checkins.found, body.lat, body.lon, body.by)


@router.post("/incidents/{incident_id}/assign-hospital")
async def assign_hospital(incident_id: int, body: Hospital, user=Depends(agent), s=Depends(get_session)):
    return await act(s, incident_id, hospitals.assign, body.hospital_id)


@router.get("/incidents/{incident_id}/hospital-suggestions")
async def suggestions(incident_id: int, user=Depends(agent), s=Depends(get_session)):
    return await hospitals.suggest(s, await get_incident(incident_id, s))


@router.post("/incidents/{incident_id}/close")
async def close(incident_id: int, user=Depends(agent), s=Depends(get_session)):
    return await act(s, incident_id, service.close)


@router.post("/sos")
async def sos(body: Sos, user=Depends(current_user), s=Depends(get_session)):
    inc = await service.sos(s, user, body.kind)
    return {"status": "ok", "incident_id": inc.id}
