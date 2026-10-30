"""
Ambulances and first-aid helpers. Ambulances report where they are and the rules react: the ETA, the yellow-line
alert for drivers on the ambulance's road, and the arrival. Nothing here moves a vehicle: that is what the vehicle's
own app (or, in the demo, the simulation) does.
"""
from sqlalchemy import select

from backend import bus, clock, db, realtime
from backend.config import settings
from backend.geo import routing
from backend.geo.distance import haversine_m
from backend.incidents import core
from backend.incidents.core import FIRST_AID, publish, say
from backend.models import Ambulance, Centre, Dispatch, HelperRequest, Incident, Notification
from backend.notifications.service import notify
from backend.users import service as users


def ambulance_view(a, dispatch=None):
    return {"id": a.id, "name": a.name, "state": a.state, "note": a.note, "prep_s": a.prep_s, "centre_id": a.centre_id,
            "location": None if a.lat is None else {"lat": a.lat, "lon": a.lon, "at": clock.iso(a.located_at)},
            "incident_id": dispatch.incident_id if dispatch else None}


async def active_dispatch(s, ambulance_id=None, incident_id=None):
    q = select(Dispatch).where(Dispatch.state == "en_route")
    if ambulance_id:
        q = q.where(Dispatch.ambulance_id == ambulance_id)
    if incident_id:
        q = q.where(Dispatch.incident_id == incident_id)
    return await s.scalar(q.order_by(Dispatch.id.desc()))


async def dispatch(s, inc, ambulance_id):
    if not core.usable(inc):
        return "no_crash"
    if inc.lat is None:
        return "no_location"
    if await s.scalar(select(Dispatch.id).where(Dispatch.incident_id == inc.id, Dispatch.state != "returned")):
        return "already_dispatched"
    amb = await s.get(Ambulance, ambulance_id)
    if amb is None or amb.state != "available":
        return "unavailable"
    centre = await s.get(Centre, amb.centre_id)
    origin = (amb.lat, amb.lon) if amb.lat is not None else (centre.lat, centre.lon)
    target = (inc.lat, inc.lon)
    r = await routing.route(origin, target)
    eta = round(r["duration_s"] + amb.prep_s)
    amb.state = "dispatched"
    inc.status = "dispatched"
    s.add(Dispatch(incident_id=inc.id, ambulance_id=amb.id, eta_s=eta, start_m=haversine_m(origin, target), target_lat=target[0],
                   target_lon=target[1], route=r["geometry"]))
    say(s, inc, f"{amb.name} dispatched. ETA {round(eta / 60)} min.", kind="dispatch")
    publish(s, inc)
    return "ok"


async def report_ambulance(s, amb, lat, lon):
    """An ambulance's position. The rules react: ETA, yellow line, arrival."""
    amb.lat, amb.lon, amb.located_at = lat, lon, clock.now()
    d = await active_dispatch(s, ambulance_id=amb.id)
    data = {**ambulance_view(amb, d), "eta_remaining_s": None}
    if d:
        inc = await s.get(Incident, d.incident_id)
        left = haversine_m((lat, lon), (d.target_lat, d.target_lon))
        data["eta_remaining_s"] = round(d.eta_s * min(1.0, left / max(d.start_m, 1)))
        for u, _ in await users.nearby(s, (lat, lon), settings.yellow_line_m):
            if u.last_driving and u.id != inc.victim_id and not await s.scalar(
                    select(Notification.id).where(Notification.user_id == u.id, Notification.incident_id == inc.id, Notification.kind == "alert")):
                await notify(s, u.id, "alert", "Ambulance coming", "Ambulance coming. Clear the yellow line.", incident_id=inc.id)
                say(s, inc, f"Yellow-line alert sent to {u.name}.", kind="alert")
        if left <= settings.arrive_m and (inc.kind == "crash" or inc.status == "on_scene"):   # a dead-zone crash is found first
            d.state, d.arrived_at, amb.state = "on_scene", clock.now(), "on_scene"
            inc.status = "on_scene"
            say(s, inc, f"{amb.name} is on the scene.", kind="dispatch")
        publish(s, inc)
    db.after_commit(s, realtime.emit_dispatchers, "ambulance.moved", data)
    return data


async def set_state(s, amb, state):
    """A crew marks the ambulance free again, or busy. If it was on its way, the agent has to send another."""
    amb.state = state
    for d in (await s.scalars(select(Dispatch).where(Dispatch.ambulance_id == amb.id, Dispatch.state == "en_route"))).all():
        d.state = "returned"
        inc = await s.get(Incident, d.incident_id)
        if inc.status == "dispatched":
            inc.status = "overdue" if inc.kind == "dead_zone" else "agent_confirmed" if inc.agent_confirmed else "detected"
        say(s, inc, f"{amb.name} is no longer coming. Send another.", kind="dispatch")
        publish(s, inc)
    db.after_commit(s, realtime.emit_dispatchers, "ambulance.moved", ambulance_view(amb))


async def release(s, inc):
    """The incident is closed: its ambulances are free, and helpers who set off can stop."""
    for d in (await s.scalars(select(Dispatch).where(Dispatch.incident_id == inc.id, Dispatch.state != "returned"))).all():
        amb = await s.get(Ambulance, d.ambulance_id)
        if d.state == "en_route":
            d.state = "returned"
        amb.state = "available"
        db.after_commit(s, realtime.emit_dispatchers, "ambulance.moved", ambulance_view(amb))
    for req in (await s.scalars(select(HelperRequest).where(HelperRequest.incident_id == inc.id, HelperRequest.state.in_(("requested", "accepted", "confirmed"))))).all():
        if req.state != "requested":
            await notify(s, req.user_id, "info", "Stand down", "Thank you. Protection Civile no longer needs you at this crash.", incident_id=inc.id)
        req.state = "cancelled"


# ------------------------------------------------------------ helpers

async def call_helpers(s, inc, radius_km=None):
    radius_km = radius_km or settings.helper_ranges_km[0]
    if not core.usable(inc):
        return "no_crash"
    if inc.lat is None:
        return "no_location"
    if radius_km not in settings.helper_ranges_km:
        return "bad_radius"
    asked_ids = set((await s.scalars(select(HelperRequest.user_id).where(HelperRequest.incident_id == inc.id))).all())
    found = await users.nearby(s, (inc.lat, inc.lon), radius_km * 1000, exclude=asked_ids | {inc.victim_id}, helpers_only=True)
    if not found:
        return "nobody_left"
    inc.help_radius_km = radius_km
    for u, d in found:
        r = await routing.route((u.last_lat, u.last_lon), (inc.lat, inc.lon))
        req = HelperRequest(incident_id=inc.id, user_id=u.id, distance_m=d, eta_s=round(r["duration_s"]), route=r["geometry"],
                            start_lat=u.last_lat, start_lon=u.last_lon)
        s.add(req)
        await notify(s, u.id, "helper", "Someone needs first aid", f"A crash victim needs help about {d / 1000:.1f} km from you. Can you go?",
                     [{"id": "accept", "label": "Yes, I'm going"}, {"id": "decline", "label": "I can't"}], incident_id=inc.id)
    say(s, inc, f"Call for help within {radius_km} km: {len(found)} helpers alerted.", kind="helper")
    publish(s, inc)
    return "ok"


async def helper_request(s, inc, user_id):
    return await s.scalar(select(HelperRequest).where(HelperRequest.incident_id == inc.id, HelperRequest.user_id == user_id))


async def on_helper_answer(s, inc, note, user, action):
    req = await helper_request(s, inc, user.id)
    if req is None:
        return
    if action == "accept":
        req.state = "accepted"
        say(s, inc, f"{user.name} accepted ({user.skills or 'helper'}) and is on the way.", kind="helper")
    else:
        req.state = "declined"
        say(s, inc, f"{user.name} can't help.", kind="helper")


async def choose_helper(s, inc, user_id):
    req = await helper_request(s, inc, user_id)
    if not core.usable(inc) or req is None or req.state != "accepted":
        return "not_accepted"
    req.state = "confirmed"
    await notify(s, user_id, "info", "Protection Civile confirms you", f"Go to the crash. {FIRST_AID}", incident_id=inc.id)
    say(s, inc, f"You confirmed {req.user.name} is going to the scene.", kind="agent")
    publish(s, inc)
    return "ok"


async def on_user_located(s, user):
    """A helper on the way reaches the crash."""
    reqs = (await s.scalars(select(HelperRequest).where(HelperRequest.user_id == user.id, HelperRequest.state.in_(("accepted", "confirmed"))))).all()
    for req in reqs:
        inc = await s.get(Incident, req.incident_id)
        if inc.lat is None or (inc.kind != "crash" and inc.status != "on_scene") or not core.is_open(inc):
            continue
        if haversine_m((user.last_lat, user.last_lon), (inc.lat, inc.lon)) <= settings.arrive_m:
            req.state, req.arrived_at = "on_scene", clock.now()   # the incident still needs its ambulance: its status stays
            say(s, inc, f"{user.name} is on the scene.", kind="helper")
            publish(s, inc)


def register():
    bus.subscribe("user.located", on_user_located)
