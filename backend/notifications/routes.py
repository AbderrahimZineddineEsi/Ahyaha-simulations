from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from backend import db
from backend.deps import current_user, get_session
from backend.models import Notification
from backend.notifications import service

router = APIRouter(tags=["notifications"])


class Answer(BaseModel):
    action: str


@router.get("/notifications")
async def mine(open_only: bool = False, user=Depends(current_user), s=Depends(get_session)):
    q = select(Notification).where(Notification.user_id == user.id).order_by(Notification.id)
    if open_only:
        q = q.where(Notification.state == "open")
    return [service.view(n) for n in (await s.scalars(q)).all()]


@router.post("/notifications/{note_id}/ack")
async def ack(note_id: int, user=Depends(current_user), s=Depends(get_session)):
    n = await s.get(Notification, note_id)
    if n is None or n.user_id != user.id:
        raise HTTPException(404, "not_found")
    service.mark_acked(n)
    await db.commit(s)
    return {"status": "ok"}


@router.post("/notifications/{note_id}/respond")
async def respond(note_id: int, body: Answer, user=Depends(current_user), s=Depends(get_session)):
    from backend.incidents import core, service as incidents
    async with core.lock:
        n = await s.get(Notification, note_id)
        if n is None or n.user_id != user.id:
            raise HTTPException(404, "not_found")
        await s.refresh(n)
        result = await incidents.respond(s, n, body.action)
    if result != "ok":
        raise HTTPException({"closed": 409, "bad_action": 422}.get(result, 400), result)
    await db.commit(s)
    return {"status": "ok"}
