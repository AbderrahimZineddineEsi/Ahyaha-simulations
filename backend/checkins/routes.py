from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend import db
from backend.checkins import service
from backend.deps import current_user, get_session

router = APIRouter(tags=["check-ins"])


class Zone(BaseModel):
    center: list[float] = Field(min_length=2, max_length=2)
    radius_m: float = Field(gt=0)


class Checkin(BaseModel):
    road: str | None = None
    zone: Zone
    entry: list[float] = Field(min_length=2, max_length=2)   # where the signal is expected to go
    back_in_min: int = Field(gt=0, le=24 * 60)


@router.post("/checkins", status_code=201)
async def create(body: Checkin, user=Depends(current_user), s=Depends(get_session)):
    checkin = await service.create(s, user, body.road, body.zone.model_dump(), body.entry, body.back_in_min)
    if checkin is None:
        raise HTTPException(409, "you already have a check-in")
    await db.commit(s)
    return {"id": checkin.id, "incident_id": checkin.incident_id, "back_by": checkin.back_by.isoformat() + "Z"}


@router.post("/checkins/{checkin_id}/return")
async def back(checkin_id: int, user=Depends(current_user), s=Depends(get_session)):
    from backend.models import DeadZoneCheckin
    checkin = await s.get(DeadZoneCheckin, checkin_id)
    if checkin is None or checkin.user_id != user.id:
        raise HTTPException(404, "not_found")
    if await service.returned(s, user, checkin) != "ok":
        raise HTTPException(409, "this check-in is over")
    await db.commit(s)
    return {"status": "ok"}
