"""
Dead-zone check-ins (PROJECT.md, "No coverage"). Before the driver enters a stretch with no network, the app asks when
they expect to be back. If the server then goes quiet inside the zone and the time passes, the contact and the
Gendarmerie are warned and help goes to the last known position. Deadlines live in the database, so a restart keeps them.
"""
import logging
from datetime import timedelta

from sqlalchemy import select

from backend import bus, clock
from backend.config import settings
from backend.geo import routing
from backend.geo.distance import haversine_m
from backend.incidents.core import publish, say
from backend.models import Ambulance, DeadZoneCheckin, Dispatch, Incident, User
from backend.notifications import service as notifications
from backend.notifications.service import notify

log = logging.getLogger("solvex")
ACTIVE = ("driving", "in_zone", "overdue")


async def active_for(s, user_id):
    return await s.scalar(select(DeadZoneCheckin).where(DeadZoneCheckin.user_id == user_id, DeadZoneCheckin.state.in_(ACTIVE)))


async def create(s, user, road, zone, entry, back_in_min, defaulted=False):
    """The driver's promise: back in so many minutes. Opens a dead-zone incident for the agent to watch."""
    if await active_for(s, user.id):
        return None
    back_by = clock.in_seconds(back_in_min * 60)
    inc = Incident(kind="dead_zone", status="dead_zone_watch", lat=entry[0], lon=entry[1], victim_id=user.id, flags={})
    s.add(inc)
    await s.flush()
    checkin = DeadZoneCheckin(user_id=user.id, incident_id=inc.id, road=road, zone=zone, entry_lat=entry[0], entry_lon=entry[1],
                              back_by=back_by, overdue_at=back_by, defaulted=defaulted)
    s.add(checkin)
    how = "they expect to be back" if not defaulted else "no answer, so the app used the route's ETA: back"
    say(s, inc, f"{user.name} is heading into a no-coverage zone on {road}: {how} by {clock.local_hhmm(back_by)}.", kind="trigger")
    publish(s, inc)
    await s.flush()
    return checkin


async def on_answer(s, note, action, user):
    if note.kind == "dz_back":
        minutes = note.data["back"][action][1]
        await create(s, user, note.data["road"], note.data["zone"], note.data["entry"], minutes)
        return
    checkin = await active_for(s, user.id)
    inc = await s.get(Incident, note.incident_id) if note.incident_id else None
    if checkin is None or inc is None:
        return
    if action == "turn_back" and checkin.state == "driving":
        checkin.state, inc.status, inc.closed_at = "turned_back", "turned_back", clock.now()
        say(s, inc, f"{user.name} turned back.", kind="agent")
    elif action == "continue":
        say(s, inc, f"{user.name} is going in anyway.", kind="agent")
    else:
        say(s, inc, f"{user.name} has read the warning.", kind="agent")
    publish(s, inc)


async def on_back_expired(s, note):
    """No answer to "when will you be back?": the app uses the route's ETA."""
    user = await s.get(User, note.user_id)
    option = note.data["default"]
    await create(s, user, note.data["road"], note.data["zone"], note.data["entry"], note.data["back"][option][1], defaulted=True)


async def checkin_of(s, inc):
    return await s.scalar(select(DeadZoneCheckin).where(DeadZoneCheckin.incident_id == inc.id))


async def minutes_away(s, inc):
    """How long the closest ambulance would take to get there."""
    etas = []
    for a in (await s.scalars(select(Ambulance).where(Ambulance.state == "available", Ambulance.lat.is_not(None)))).all():
        etas.append((await routing.cached_route((a.lat, a.lon), (inc.lat, inc.lon)))["duration_s"] + a.prep_s)
    return round(min(etas, default=3600) / 60)


async def warn(s, inc, message):
    """The agent's two messages, for as long as the driver still has network."""
    checkin = await checkin_of(s, inc)
    if checkin is None:
        return "no_crash"
    if checkin.state != "driving":
        return "no_network"
    user = inc.victim
    if message == "dont_go" and not checkin.warned:
        checkin.warned = True
        await notify(s, user.id, "dz_warning", "Protection Civile: don't go", f"We advise you not to enter this zone on {checkin.road} now.",
                     [{"id": "turn_back", "label": "Turn back"}, {"id": "continue", "label": "Continue anyway"}], incident_id=inc.id)
        say(s, inc, f"You warned {user.name} not to go.", kind="agent")
    elif message == "no_help" and not checkin.told_no_help:
        checkin.told_no_help = True
        await notify(s, user.id, "dz_warning", "Help can't reach you there",
                     f"With no network nobody will know if something happens, and an ambulance needs {await minutes_away(s, inc)} min or more.",
                     [{"id": "ok", "label": "Understood"}], incident_id=inc.id)
        say(s, inc, f"You told {user.name} help can't reach them there.", kind="agent")
    else:
        return "already_sent"
    publish(s, inc)
    return "ok"


async def acknowledge(s, inc):
    checkin = await checkin_of(s, inc)
    if checkin is None:
        return "no_crash"
    checkin.acknowledged = True
    publish(s, inc)
    return "ok"


async def found(s, inc, lat, lon, by):
    """Help reached the driver, past the last known position: the agent learns what happened, and where."""
    checkin = await checkin_of(s, inc)
    if checkin is None or checkin.state == "found":
        return "no_crash"
    checkin.state = "found"
    inc.lat, inc.lon, inc.status = lat, lon, "on_scene"
    for d in (await s.scalars(select(Dispatch).where(Dispatch.incident_id == inc.id, Dispatch.state == "en_route"))).all():
        d.target_lat, d.target_lon = lat, lon     # the ambulances now know where they are going
    say(s, inc, f"{by} found {inc.victim.name} further along {checkin.road}, past the last known position: it was a crash. Its data arrives now.", kind="trigger")
    publish(s, inc)
    return "ok"


async def end(s, inc):
    """The agent closed the incident: the watch stops, and the driver can check in again."""
    checkin = await checkin_of(s, inc)
    if checkin and checkin.state in ACTIVE:
        checkin.state = "closed"


async def returned(s, user, checkin=None):
    """The driver is back (a location outside the zone), or says so."""
    checkin = checkin or await active_for(s, user.id)
    if checkin is None or checkin.state not in ACTIVE:
        return "no_checkin"
    inc = await s.get(Incident, checkin.incident_id)
    checkin.state, inc.status, inc.closed_at = "returned", "returned", clock.now()
    say(s, inc, f"{user.name} is back and reachable.", kind="agent")
    publish(s, inc)
    return "ok"


async def step(s):
    """The scheduler's part: a silent driver inside the zone is in it, and a driver who is late is overdue."""
    now = clock.now()
    for checkin in (await s.scalars(select(DeadZoneCheckin).where(DeadZoneCheckin.state.in_(("driving", "in_zone"))))).all():
        user = await s.get(User, checkin.user_id)
        inc = await s.get(Incident, checkin.incident_id)
        if checkin.state == "driving":
            known = user.last_lat is not None and user.last_seen_at is not None
            silent = not known or now - user.last_seen_at >= timedelta(seconds=settings.zone_silence_s)
            inside = known and haversine_m(checkin.zone["center"], (user.last_lat, user.last_lon)) <= checkin.zone["radius_m"]
            # Coverage ends where it ends, not where our circle says: past the promised time, silence alone is enough
            if silent and (inside or now >= checkin.overdue_at):
                checkin.state, checkin.acknowledged = "in_zone", True
                last = (user.last_lat, user.last_lon) if known else (checkin.entry_lat, checkin.entry_lon)
                checkin.last_lat, checkin.last_lon, checkin.last_seen_at = last[0], last[1], user.last_seen_at or checkin.created_at
                inc.status, inc.lat, inc.lon = "in_zone", last[0], last[1]
                await notifications.expire_open(s, inc.id, ("dz_warning",))
                say(s, inc, f"{user.name} entered the zone. The signal is gone: last known position saved at {clock.local_hhmm(checkin.last_seen_at)}.", kind="dead_zone")
                publish(s, inc)
        if checkin.state == "in_zone" and now >= checkin.overdue_at:
            checkin.state, checkin.acknowledged, inc.status = "overdue", False, "overdue"
            say(s, inc, f"{user.name} is not back by {clock.local_hhmm(checkin.back_by)}. Send help to the last known position.", kind="trigger")
            say(s, inc, f"Emergency contact warned first: {user.emergency_contact_name or 'not set'} {user.emergency_contact_phone or ''}".strip() + ".", kind="notification")
            say(s, inc, "Gendarmerie alerted with the last position, the route and the car's description.", kind="notification")
            publish(s, inc)


async def on_user_located(s, user):
    """A location from a driver we thought was in the zone: they are back."""
    checkin = await s.scalar(select(DeadZoneCheckin).where(DeadZoneCheckin.user_id == user.id, DeadZoneCheckin.state.in_(("in_zone", "overdue"))))
    if checkin and haversine_m(checkin.zone["center"], (user.last_lat, user.last_lon)) > checkin.zone["radius_m"]:
        await returned(s, user)


def register():
    bus.subscribe("user.located", on_user_located)
