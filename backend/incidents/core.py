"""What every part of the incident rules shares: the timeline, telling clients about a change, and which incidents are open."""
import asyncio

from sqlalchemy import select

from backend import bus, clock, db, realtime
from backend.models import Incident, IncidentEvent

CONFIDENCE = ("low", "medium", "high")
SEVERITIES = ("low", "medium", "high", "extreme")
ALARM = ("likely", "crash")
CLOSED = ("false_alarm", "closed", "turned_back", "returned")
WAITING = ("analyzing", "false_alarm", "dead_zone_watch", "in_zone", "turned_back", "returned", "closed")  # no help can be sent yet
FIRST_AID = "Scene safety first: hazard lights on, stay off the road, no smoking near fuel. Then check breathing. Do not move the neck."


# One change to an incident at a time. A rule checks, then may wait for a route, then writes: two agents pressing Dispatch
# together must not both pass the check. One process serves everything, so a lock in memory is enough.
lock = asyncio.Lock()


def pct(p):
    return "over 99.9%" if p >= 0.9995 else f"{p * 100:.1f}%"


def say(s, inc, text, kind="info", audience="agent"):
    """A line in the incident's timeline. audience 'dev' is the simulation's own log: dispatchers never get it."""
    s.add(IncidentEvent(incident_id=inc.id, audience=audience, kind=kind, text=text, at=clock.now()))


async def _announce(inc_id):
    from backend.incidents import view
    async with db.session() as s:
        inc = await s.get(Incident, inc_id)
        if inc is None:       # deleted in the meantime (a demo reset)
            return
        data = await view.summary(s, inc)
    await realtime.emit_dispatchers("incident.updated", data)
    await realtime.emit_incident(inc_id, "incident.updated", data)
    await bus.publish("incident.updated", incident_id=inc_id, status=data["status_code"], kind=data["kind"])


def publish(s, inc):
    """Tell dispatchers once the change is saved."""
    inc.updated_at = clock.now()
    db.after_commit(s, _announce, inc.id)


def lower_confidence(inc):
    inc.confidence = CONFIDENCE[max(0, CONFIDENCE.index(inc.confidence or "low") - 1)]


def raise_confidence(inc):
    inc.confidence = "high"


def is_open(inc):
    return inc.status not in CLOSED


async def open_incidents(s, kind=None):
    q = select(Incident).where(Incident.status.not_in(CLOSED)).order_by(Incident.id)
    if kind:
        q = q.where(Incident.kind == kind)
    return list((await s.scalars(q)).all())


def usable(inc):
    """Can help be sent? Not before M2 answers, and in a dead zone only once the driver is overdue."""
    return inc is not None and inc.status not in WAITING
