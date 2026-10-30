from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from backend import db
from backend.deps import get_session, require
from backend.fleet import service
from backend.models import Ambulance, Centre

router = APIRouter(tags=["fleet"])


class Position(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class State(BaseModel):
    state: str


@router.get("/centres")
async def centres(user=Depends(require("dispatcher")), s=Depends(get_session)):
    return [{"id": c.id, "name": c.name, "lat": c.lat, "lon": c.lon} for c in (await s.scalars(select(Centre))).all()]


@router.get("/ambulances")
async def ambulances(user=Depends(require("dispatcher")), s=Depends(get_session)):
    out = []
    for a in (await s.scalars(select(Ambulance).order_by(Ambulance.id))).all():
        out.append(service.ambulance_view(a, await service.active_dispatch(s, ambulance_id=a.id)))
    return out


@router.post("/ambulances/{ambulance_id}/location")
async def location(ambulance_id: str, body: Position, user=Depends(require("dispatcher")), s=Depends(get_session)):
    amb = await s.get(Ambulance, ambulance_id)
    if amb is None:
        raise HTTPException(404, "not_found")
    data = await service.report_ambulance(s, amb, body.lat, body.lon)
    await db.commit(s)
    return data


@router.post("/ambulances/{ambulance_id}/state")
async def state(ambulance_id: str, body: State, user=Depends(require("dispatcher")), s=Depends(get_session)):
    amb = await s.get(Ambulance, ambulance_id)
    if amb is None:
        raise HTTPException(404, "not_found")
    if body.state not in ("available", "busy"):
        raise HTTPException(422, "state must be available or busy")
    await service.set_state(s, amb, body.state)
    await db.commit(s)
    return service.ambulance_view(amb)
