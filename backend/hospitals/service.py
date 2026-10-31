"""
Hospitals: facilities and free beds, the choice of destination, and the patient's view for the hospital they are
going to. A hospital sees a patient only once the agent has sent them there (PROJECT.md S13).
"""
from sqlalchemy import select

from backend import clock, db, realtime
from backend.geo import routing
from backend.geo.distance import haversine_m
from backend.incidents import core
from backend.incidents.core import publish, say
from backend.models import Hospital, Incident, PatientTransfer
from backend.users.service import profile_view

# Which specialty an injured region calls for (PROJECT.md S2)
REGION_SPECIALTY = {"head": "neurosurgery", "face": "maxillofacial", "thorax": "thoracic", "abdomen": "trauma", "spine": "neurosurgery",
                    "arms": "orthopedics", "legs": "orthopedics"}
NO_BEDS_KM = 200   # a hospital without a free bed counts as this much further away
MISSING_SPECIALTY_KM = 100   # so does each specialty the injury calls for that it lacks: a head injury goes to neurosurgery


def view(h):
    return {"id": h.id, "name": h.name, "lat": h.lat, "lon": h.lon, "specialties": h.specialties, "beds_total": h.beds_total,
            "beds_free": h.beds_free, "updated_at": clock.iso(h.updated_at)}


def needed_specialties(inc):
    regions = (inc.severity or {}).get("m4") or []
    return [REGION_SPECIALTY[r["region"]] for r in regions if r["state"] == "likely" and r["region"] in REGION_SPECIALTY][:2]


async def suggest(s, inc):
    """Hospitals ranked by a simple score in km: distance, plus a penalty for no free bed or a missing specialty. Lowest first."""
    if inc.lat is None:
        return []
    needs = needed_specialties(inc)
    out = []
    for h in (await s.scalars(select(Hospital))).all():
        km = haversine_m((inc.lat, inc.lon), (h.lat, h.lon)) / 1000
        missing = [n for n in needs if n not in h.specialties]
        score = km + (NO_BEDS_KM if h.beds_free <= 0 else 0) + MISSING_SPECIALTY_KM * len(missing)
        out.append({**view(h), "distance_km": round(km, 1), "missing_specialties": missing, "score": round(score, 1)})
    return sorted(out, key=lambda h: h["score"])


def patient(inc, transfer):
    """What a hospital sees of a patient on the way: the profile and the injury estimate."""
    sev = inc.severity or {}
    return {"transfer_id": transfer.id, "incident_id": inc.id, "state": transfer.state, "eta_s": transfer.eta_s,
            "severity": sev.get("shown"), "regions": [r for r in sev.get("m4", []) if r["state"] != "unlikely"],
            "profile": profile_view(inc.victim), "confidence": inc.confidence, "created_at": clock.iso(transfer.created_at)}


async def assign(s, inc, hospital_id):
    if not core.usable(inc):
        return "no_crash"
    if inc.status in ("transporting", "at_hospital"):
        return "already_sent"
    h = await s.get(Hospital, hospital_id)
    if h is None:
        return "not_found"
    for old in (await s.scalars(select(PatientTransfer).where(PatientTransfer.incident_id == inc.id, PatientTransfer.state == "incoming"))).all():
        old.state = "cancelled"
        db.after_commit(s, realtime.emit_hospital, old.hospital_id, "hospital.incoming", {"incident_id": inc.id, "cancelled": True})
    eta = None
    if inc.lat is not None:
        r = await routing.route((inc.lat, inc.lon), (h.lat, h.lon))
        eta = round(r["duration_s"])
    transfer = PatientTransfer(incident_id=inc.id, hospital_id=h.id, eta_s=eta)
    s.add(transfer)
    await s.flush()
    say(s, inc, f"You sent the patient to {h.name}" + (f" (about {round(eta / 60)} min away)." if eta else "."), kind="agent")
    publish(s, inc)
    db.after_commit(s, realtime.emit_hospital, h.id, "hospital.incoming", patient(inc, transfer))
    return "ok"


async def arrived(s, hospital_id, transfer_id):
    """The patient reached the hospital: they leave the incoming list and take a bed."""
    t = await s.get(PatientTransfer, transfer_id)
    if t is None or t.hospital_id != hospital_id or t.state != "incoming":
        return None
    h = await s.get(Hospital, hospital_id)
    inc = await s.get(Incident, t.incident_id)
    t.state, h.beds_free, h.updated_at = "arrived", max(0, h.beds_free - 1), clock.now()
    say(s, inc, f"The patient reached {h.name}.", kind="agent")
    publish(s, inc)
    return t


async def incoming(s, hospital_id):
    rows = (await s.execute(select(PatientTransfer, Incident).join(Incident, Incident.id == PatientTransfer.incident_id)
                            .where(PatientTransfer.hospital_id == hospital_id, PatientTransfer.state == "incoming"))).all()
    return [patient(inc, t) for t, inc in rows]
