"""What clients see of an incident: a short summary for lists and events, and the full detail for the agent."""
from sqlalchemy import select

from backend import clock
from backend.config import settings
from backend.geo import routing
from backend.geo.distance import haversine_m
from backend.incidents import core
from backend.models import (Ambulance, DeadZoneCheckin, Dispatch, HelperRequest, IncidentEvent, Notification, PatientTransfer,
                            User)
from backend.notifications.service import view as note_view
from backend.users.service import profile_view

DZ_LABELS = {"dead_zone_watch": "Heading into a no-coverage zone", "in_zone": "In the no-coverage zone", "turned_back": "Turned back",
             "returned": "Back and reachable", "overdue": "Not back: last known position", "dispatched": "Help going to the last known position"}


def point(inc):
    return None if inc.lat is None else [inc.lat, inc.lon]


def answered(notes, kind, answer):
    return [n.user_id for n in notes if n.kind == kind and n.answer == answer]


def status_label(inc, notes, found=False):
    if inc.status == "closed":
        return "Closed"
    if inc.kind == "dead_zone":
        return "Found: it was a crash" if found else DZ_LABELS.get(inc.status, inc.status)
    if inc.status == "analyzing":
        return "Analyzing the trigger"
    if inc.status == "false_alarm":
        return "No crash"
    if inc.agent_confirmed:
        return "Confirmed by Protection Civile"
    parts = [f"{(inc.confidence or 'low').title()} confidence"]
    if inc.nobody_around:
        parts.append("nobody nearby")
    parts += {"not_responding": ["user not responding"], "ok": ["user says OK"], "needs_help": ["user asks for help"],
              "silent_sos": ["silent SOS"], "voice_sos": ["voice SOS"]}.get(inc.user_state, [])
    if answered(notes, "witness", "confirm"):
        parts.append("confirmed by witness")
    if inc.flags.get("reports", 0) > 0:
        parts.append(f"{inc.flags['reports'] + 1} reports")
    return ", ".join(parts)


async def summary(s, inc):
    notes = list((await s.scalars(select(Notification).where(Notification.incident_id == inc.id))).all())
    checkin = await s.scalar(select(DeadZoneCheckin).where(DeadZoneCheckin.incident_id == inc.id))
    found = bool(checkin and checkin.state == "found")
    return {"id": inc.id, "kind": inc.kind, "status_code": inc.status, "stage": inc.status, "status": status_label(inc, notes, found),
            "confidence": inc.confidence, "location": None if inc.lat is None else {"lat": inc.lat, "lon": inc.lon},
            "victim_id": inc.victim_id, "victim_name": inc.victim.name if inc.victim else None, "device_id": inc.device_id,
            "severity": inc.severity and inc.severity["shown"], "created_at": clock.iso(inc.created_at),
            "updated_at": clock.iso(inc.updated_at), "flags": inc.flags}


async def helper_ranges(s, inc):
    """For each range of the call for help: how many helpers are in it, and how many haven't been asked yet."""
    if inc.lat is None:
        return []
    asked = {h.user_id: h for h in (await s.scalars(select(HelperRequest).where(HelperRequest.incident_id == inc.id))).all()}
    helpers = (await s.scalars(select(User).where(User.is_helper.is_(True), User.role == "citizen", User.last_lat.is_not(None)))).all()
    fresh = clock.now().timestamp() - settings.location_fresh_s
    out = []
    for km in settings.helper_ranges_km:
        count = available = 0
        for u in helpers:
            if u.id in asked:
                d = asked[u.id].distance_m
            elif u.last_seen_at and u.last_seen_at.timestamp() >= fresh:
                d = haversine_m(point(inc), (u.last_lat, u.last_lon))
            else:
                continue
            if u.id == inc.victim_id:
                continue
            if d <= km * 1000:
                count += 1
                available += u.id not in asked
        out.append({"km": km, "count": count, "available": available})
    return out


def person(u, inc, kind, extra=None):
    d = None if inc.lat is None or u.last_lat is None else round(haversine_m(point(inc), (u.last_lat, u.last_lon)) / 1000, 1)
    return {"id": u.id, "name": u.name, "kind": kind, "phone": u.phone, "driving": u.last_driving, "helper": u.is_helper,
            "skill": u.skills, "pos": None if u.last_lat is None else [u.last_lat, u.last_lon], "distance_km": d, **(extra or {})}


async def detail(s, inc, include_dev=False):
    base = await summary(s, inc)
    events = (await s.scalars(select(IncidentEvent).where(IncidentEvent.incident_id == inc.id).order_by(IncidentEvent.id))).all()
    notes = list((await s.scalars(select(Notification).where(Notification.incident_id == inc.id).order_by(Notification.id))).all())
    requests = list((await s.scalars(select(HelperRequest).where(HelperRequest.incident_id == inc.id))).all())
    dispatch = await s.scalar(select(Dispatch).where(Dispatch.incident_id == inc.id, Dispatch.state != "returned").order_by(Dispatch.id.desc()))
    checkin = await s.scalar(select(DeadZoneCheckin).where(DeadZoneCheckin.incident_id == inc.id))
    transfer = await s.scalar(select(PatientTransfer).where(PatientTransfer.incident_id == inc.id, PatientTransfer.state != "cancelled"))
    victim = inc.victim

    d = None
    if dispatch:
        total = max(dispatch.start_m, 1)
        amb = await s.get(Ambulance, dispatch.ambulance_id)
        left = 0 if dispatch.state == "on_scene" or amb.lat is None else min(1.0, haversine_m((amb.lat, amb.lon), (dispatch.target_lat, dispatch.target_lon)) / total)
        d = {"ambulance_id": dispatch.ambulance_id, "eta_s": dispatch.eta_s, "state": dispatch.state, "eta_remaining_s": round(dispatch.eta_s * left),
             "progress": 1 - left, "route": dispatch.route}

    people = {}
    if victim:
        people[victim.id] = person(victim, inc, "victim")
    for n in notes:
        if n.user_id != inc.victim_id and n.kind in ("witness", "alert", "helper", "check") and (n.kind == "check" or n.user_id not in people):
            u = await s.get(User, n.user_id)
            people[u.id] = person(u, inc, {"witness": "witness", "alert": "driver", "helper": "helper", "check": "also_in_crash"}[n.kind])
    helpers = {}
    for h in requests:
        people[h.user_id] = person(h.user, inc, "helper", {"eta_s": h.eta_s, "route": h.route})
        helpers[h.user_id] = h.state

    ambulances = []
    for a in (await s.scalars(select(Ambulance).order_by(Ambulance.id))).all():
        eta = None
        if a.state == "available" and not dispatch and core.usable(inc) and inc.lat is not None and a.lat is not None:
            eta = round((await routing.cached_route((a.lat, a.lon), (inc.lat, inc.lon)))["duration_s"] + a.prep_s)
        ambulances.append({"id": a.id, "name": a.name, "state": a.state, "note": a.note, "prep_s": a.prep_s, "eta_s": eta,
                           "pos": None if a.lat is None else [a.lat, a.lon]})

    dz = None
    if checkin:
        dz = {"phase": checkin.state, "road": checkin.road, "back_by": clock.iso(checkin.back_by), "back_defaulted": checkin.defaulted,
              "last_seen": None if checkin.last_lat is None else [checkin.last_lat, checkin.last_lon], "last_seen_at": clock.iso(checkin.last_seen_at),
              "zone": checkin.zone, "warned": checkin.warned, "told_no_help": checkin.told_no_help, "acknowledged": checkin.acknowledged,
              "found": checkin.state == "found", "overdue_at": clock.iso(checkin.overdue_at)}

    protected = inc.status not in core.CLOSED
    return {**base,
            "trigger": None if inc.trigger_seq is None else {"seq": inc.trigger_seq, "peak_g": inc.peak_g},
            "user_state": inc.user_state, "nobody_around": inc.nobody_around, "help_radius_km": inc.help_radius_km,
            "m2": inc.m2_level and {"level": inc.m2_level, "p_crash": inc.m2_p}, "severity": inc.severity,
            "witness": {"confirmed_by": answered(notes, "witness", "confirm"), "denied_by": answered(notes, "witness", "deny")},
            "agent": {"confirmed": inc.agent_confirmed, "severity": inc.agent_severity},
            "helpers": helpers, "dispatch": d, "helper_ranges": await helper_ranges(s, inc), "dead_zone": dz,
            "profile": profile_view(victim) if protected else None,
            "people": list(people.values()), "ambulances": ambulances,
            "hospital": transfer and {"hospital_id": transfer.hospital_id, "state": transfer.state, "eta_s": transfer.eta_s},
            "events": [{"t": clock.epoch(e.at), "at": clock.iso(e.at), "who": e.audience, "kind": e.kind, "text": e.text}
                       for e in events if include_dev or e.audience == "agent"],
            "notifications": [note_view(n) for n in notes], "server_time": clock.epoch(clock.now())}
