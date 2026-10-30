from sqlalchemy import select

from backend import db
from backend.incidents import service as incidents
from backend.models import Device, Incident, Notification

SUMMARY = {"dv_kmh": 30.0, "pdof_deg": 5, "rollover": 0, "multi_impact": 0, "peak_g": 5.0}
SPOT = (36.3739, 3.9141)


async def register_device(device_id, owner):
    async with db.session_scope() as s:
        s.add(Device(id=device_id, key_hash="x", owner_id=owner.id))


async def crash(device_id="bb-1", at=SPOT, level="crash", seq=8, p=0.99, summary=SUMMARY):
    """What the telemetry service tells the rules: a trigger, and 8 s later M2's answer."""
    await incidents.on_trigger(device_id, seq, 5.0, {"lat": at[0], "lon": at[1]} if at else None)
    await incidents.on_prediction({"device_id": device_id, "trigger_seq": seq, "level": level, "p_crash": p}, summary)


async def get_incident(n=None):
    async with db.session() as s:
        q = select(Incident).order_by(Incident.id.desc()) if n is None else select(Incident).where(Incident.id == n)
        return await s.scalar(q)


async def notes(user=None, kind=None, incident=None):
    async with db.session() as s:
        q = select(Notification).order_by(Notification.id)
        if user is not None:
            q = q.where(Notification.user_id == (user.id if hasattr(user, "id") else user))
        if kind:
            q = q.where(Notification.kind == kind)
        if incident:
            q = q.where(Notification.incident_id == incident)
        return list((await s.scalars(q)).all())
