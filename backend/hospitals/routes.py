from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from backend import clock, db
from backend.deps import current_user, get_session
from backend.hospitals import service
from backend.models import Hospital

router = APIRouter(tags=["hospitals"])


class Beds(BaseModel):
    beds_free: int = Field(ge=0)
    beds_total: int | None = Field(None, ge=0)


def staff_of(user, hospital_id):
    """A hospital's own staff, or an admin."""
    if user.role != "admin" and not (user.role == "hospital" and user.hospital_id == hospital_id):
        raise HTTPException(403, "this is another hospital's page")


@router.get("/hospitals")
async def hospitals(user=Depends(current_user), s=Depends(get_session)):
    if user.role == "citizen":
        raise HTTPException(403, "no access")
    return [service.view(h) for h in (await s.scalars(select(Hospital).order_by(Hospital.id))).all()]


@router.patch("/hospitals/{hospital_id}/beds")
async def beds(hospital_id: int, body: Beds, user=Depends(current_user), s=Depends(get_session)):
    staff_of(user, hospital_id)
    h = await s.get(Hospital, hospital_id)
    if h is None:
        raise HTTPException(404, "not_found")
    h.beds_free = body.beds_free
    if body.beds_total is not None:
        h.beds_total = body.beds_total
    if h.beds_free > h.beds_total:
        raise HTTPException(422, "more free beds than beds")
    h.updated_at = clock.now()
    await db.commit(s)
    return service.view(h)


@router.post("/hospitals/{hospital_id}/incoming/{transfer_id}/arrived")
async def arrived(hospital_id: int, transfer_id: int, user=Depends(current_user), s=Depends(get_session)):
    staff_of(user, hospital_id)
    if await service.arrived(s, hospital_id, transfer_id) is None:
        raise HTTPException(404, "not_found")
    await db.commit(s)
    return {"status": "ok"}


@router.get("/hospitals/{hospital_id}/incoming")
async def incoming(hospital_id: int, user=Depends(current_user), s=Depends(get_session)):
    staff_of(user, hospital_id)
    return await service.incoming(s, hospital_id)
