"""
The Protection Civile response to a crash, as rules over the database. What happens after a blackbox's trigger and
M2's answer: severity (M3, M4), the check-in with the victim, witnesses, the agent's decisions, and the answers of
phones. Dispatch and helpers live in fleet/, the dead-zone check-ins in checkins/.
"""
import logging
from datetime import timedelta

from sqlalchemy import select

from backend import clock, db, ml
from backend.config import settings
from backend.geo.distance import haversine_m
from backend.incidents import core
from backend.incidents.core import ALARM, SEVERITIES, pct, publish, say
from backend.models import Device, Incident, Notification, User
from backend.notifications import service as notifications
from backend.notifications.service import notify
from backend.users import service as users

log = logging.getLogger("solvex")


def name_of(user):
    return user.name if user else "an unknown person"


async def find_to_merge(s, device_id, point):
    """An open crash from the same device, or one close to this place, within the window: the same crash reported twice."""
    since = clock.now() - timedelta(seconds=settings.merge_window_s)
    for inc in await core.open_incidents(s, "crash"):
        if inc.created_at < since or inc.status == "false_alarm":
            continue
        if inc.device_id == device_id:
            return inc
        if point and inc.lat is not None and haversine_m(point, (inc.lat, inc.lon)) <= settings.merge_radius_m:
            return inc


async def on_trigger(device_id, seq, peak_g, location):
    """A blackbox's trigger fired: the agent can see an impact right away, before M2 answers 8 s later."""
    async with db.session_scope() as s:
        device = await s.get(Device, device_id)
        victim = await s.get(User, device.owner_id) if device and device.owner_id else None
        point = (location["lat"], location["lon"]) if location else (victim.last_lat, victim.last_lon) if victim and victim.last_lat is not None else None
        same = await find_to_merge(s, device_id, point)
        if same:
            flags = {**same.flags, "reports": same.flags.get("reports", 0) + 1, "report_keys": same.flags.get("report_keys", []) + [[device_id, seq]]}
            also = f" {victim.name} was driving it, so they may be hurt too." if victim and victim.id != same.victim_id else ""
            if also:
                flags["others"] = flags.get("others", []) + [victim.id]
            same.flags = flags
            say(s, same, f"Another report of this crash: {device_id} triggered at {peak_g:.1f} g. Merged into this incident.{also}", kind="trigger")
            publish(s, same)
            return
        flags = {} if victim else {"unknown_victim": True}
        inc = Incident(kind="crash", status="analyzing", lat=point and point[0], lon=point and point[1], device_id=device_id,
                       victim_id=victim and victim.id, trigger_seq=seq, peak_g=round(peak_g, 2), flags=flags)
        s.add(inc)
        await s.flush()
        say(s, inc, f"Trigger on {name_of(victim)}'s car ({device_id}): impact of {peak_g:.1f} g. Checking the last 16 seconds.", kind="trigger")
        if not victim:
            say(s, inc, "No person is registered for this blackbox: no check-in and no medical profile.", kind="notification")
        publish(s, inc)


async def alert_witnesses(s, inc):
    """The citizens close to the crash, with a recent location. Returns how many were asked."""
    if inc.lat is None:
        return 0
    found = await users.nearby(s, (inc.lat, inc.lon), settings.witness_radius_m, exclude={inc.victim_id, *inc.flags.get("others", [])})
    for u, d in found:
        await notify(s, u.id, "witness", "Accident nearby", f"A crash may have happened about {round(d, -1):.0f} m from you. Can you confirm it?",
                     [{"id": "confirm", "label": "Confirm"}, {"id": "deny", "label": "Deny"}], incident_id=inc.id)
    if found:
        say(s, inc, f"Accident alert sent to the {len(found)} people nearby.", kind="notification")
    return len(found)


async def withdraw_witnesses(s, inc, except_id=None):
    """The crash has an answer (the victim, a witness, an SOS or the agent), so the other people nearby are no longer asked."""
    n = await notifications.expire_open(s, inc.id, ("witness",), state="withdrawn", except_id=except_id)
    if n:
        say(s, inc, f"Alert withdrawn from {n} {'person' if n == 1 else 'people'} nearby: it has an answer.", kind="notification")


async def sos_card(s, inc):
    await notify(s, inc.victim_id, "sos", "Can't talk or move?", "Use this at any time, even after the check above ends.",
                 [{"id": "silent", "label": "Silent SOS"}, {"id": "voice", "label": "Voice SOS"}], incident_id=inc.id)


async def ask_others(s, inc):
    """Everyone else in the same crash gets their own "Are you OK?". The incident's severity is the first person's."""
    for uid in inc.flags.get("others", []):
        asked = await s.scalar(select(Notification.id).where(Notification.incident_id == inc.id, Notification.user_id == uid, Notification.kind == "check"))
        if not asked:
            await notify(s, uid, "check", "Are you OK?", "We detected a crash. Are you OK?",
                         [{"id": "ok", "label": "I'm OK"}, {"id": "help", "label": "I need help"}], settings.checkin_s, incident_id=inc.id)


async def on_prediction(reply, summary):
    """M2's answer for a trigger. Crash or likely: estimate severity, ask the person, alert whoever is close."""
    async with db.session_scope() as s:
        key = [reply["device_id"], reply["trigger_seq"]]
        inc = await s.scalar(select(Incident).where(Incident.device_id == key[0], Incident.trigger_seq == key[1], Incident.m2_level.is_(None)))
        if inc is None:
            return await merged_report(s, reply, summary, key)
        if reply["level"] not in ALARM:
            inc.m2_level, inc.m2_p, inc.status = reply["level"], reply["p_crash"], "false_alarm"
            say(s, inc, f"No crash: M2 gives {pct(reply['p_crash'])}.", kind="m2")
            publish(s, inc)
            return
        await detect(s, inc, reply, summary)


async def detect(s, inc, reply, summary):
    """A crash. Estimate how bad it is, and start asking people."""
    level = reply["level"]
    inc.m2_level, inc.m2_p = level, reply["p_crash"]
    victim = await s.get(User, inc.victim_id) if inc.victim_id else None
    inc.confidence = "high" if level == "crash" else "medium"
    inc.status = "detected"
    say(s, inc, f"Crash detected by M2: {level}, {pct(reply['p_crash'])}. Confidence {inc.confidence}.", kind="m2")

    device = await s.get(Device, inc.device_id)
    sev = ml.severity_model().for_incident(summary, inc.device_id, victim, device.body_type if device else "car")
    m3, regions = sev["m3"], sev["m4"]
    inc.m3_level, inc.m3_p = m3["level"], m3["p"]
    inc.severity = {**sev, "shown": m3["level"]}   # the predicted level stands until a person says otherwise
    inc.flags = {**inc.flags, **sev["flags"]}
    measured = (f"The blackbox measured: dv {summary['dv_kmh']} km/h, direction {summary['pdof_deg']} deg, rollover {summary['rollover']}, "
                f"multiple impacts {summary['multi_impact']}.")
    say(s, inc, measured + (f" Not used for severity: {sev['case_note']}" if sev["case_note"] else ""), kind="blackbox", audience="dev")
    flagged = [r["region"] for r in regions if r["state"] != "unlikely"][:3]
    extra = " No medical profile: a default adult was assumed." if sev["flags"].get("default_profile") else ""
    say(s, inc, f"Severity estimate: {m3['level']} ({pct(m3['p'])}). Check first: {', '.join(flagged) if flagged else 'no region stands out'}.{extra}", kind="severity")

    if victim:
        inc.user_state = "pending"
        await notify(s, victim.id, "check", "Are you OK?", "We detected a crash. Are you OK?",
                     [{"id": "ok", "label": "I'm OK"}, {"id": "help", "label": "I need help"}], settings.checkin_s, incident_id=inc.id)
        say(s, inc, f"'Are you OK?' sent to {victim.name}. {settings.checkin_s} s to answer.", kind="notification")
    await ask_others(s, inc)
    if inc.lat is None:
        say(s, inc, "The crash has no location: click the map to place it once you know where it is.", kind="notification")
        if victim:
            await sos_card(s, inc)
    elif not await alert_witnesses(s, inc):
        await nobody_around(s, inc)
    publish(s, inc)


async def nobody_around(s, inc):
    """Nobody can confirm it, so the crash stays at high confidence whatever the driver answers."""
    inc.nobody_around, inc.confidence = True, "high"
    km = settings.witness_radius_m / 1000
    say(s, inc, f"Nobody within {km:g} km, so no witness can confirm. Call the driver to check the severity.", kind="notification")
    if inc.victim_id:
        await sos_card(s, inc)


async def merged_report(s, reply, summary, key):
    """M2's answer for a report merged into another incident. The first alarm is what counts: a merged report that sees a
    crash takes the incident over if nothing has confirmed it yet, even if the first report turned out to be nothing."""
    since = clock.now() - timedelta(seconds=settings.merge_window_s * 2)
    rows = (await s.scalars(select(Incident).where(Incident.kind == "crash", Incident.created_at >= since).order_by(Incident.id.desc()))).all()
    inc = next((i for i in rows if key in i.flags.get("report_keys", [])), None)
    if inc is None:
        return
    if reply["level"] not in ALARM:
        say(s, inc, f"{key[0]} saw no crash: M2 gives {pct(reply['p_crash'])}.", kind="m2")
    elif inc.m2_level in ALARM:
        core.raise_confidence(inc)
        await ask_others(s, inc)
        say(s, inc, f"{key[0]} confirms: {reply['level']}, {pct(reply['p_crash'])}. Confidence high.", kind="m2")
    else:
        await take_over(s, inc, key, reply, summary)
        return
    publish(s, inc)


async def take_over(s, inc, key, reply, summary):
    still_waiting = inc.m2_level is None     # the first report may yet confirm it too
    device = await s.get(Device, key[0])
    owner = device.owner_id if device else None
    others = [u for u in inc.flags.get("others", []) if u != owner]
    if still_waiting and inc.victim_id and inc.victim_id != owner:
        others.append(inc.victim_id)
    keys = [k for k in inc.flags["report_keys"] if k != key] + ([[inc.device_id, inc.trigger_seq]] if still_waiting else [])
    flags = {**inc.flags, "report_keys": keys, "others": others}
    flags.pop("unknown_victim", None)
    inc.flags = flags if owner else {**flags, "unknown_victim": True}
    inc.device_id, inc.trigger_seq, inc.victim_id, inc.status = key[0], key[1], owner, "analyzing"
    if reply.get("location"):
        inc.lat, inc.lon = reply["location"]["lat"], reply["location"]["lon"]
    say(s, inc, f"{key[0]} saw the crash this incident was waiting on.", kind="trigger")
    await detect(s, inc, reply, summary)


async def set_location(s, inc, lat, lon):
    """The agent placed the crash, from what a caller told them. People nearby can now be alerted."""
    first = inc.lat is None
    inc.lat, inc.lon = lat, lon
    say(s, inc, "You placed the crash on the map.", kind="agent")
    if first and core.usable(inc) and not await alert_witnesses(s, inc):
        await nobody_around(s, inc)
    publish(s, inc)
    return "ok"


# ------------------------------------------------------------ answers from phones

async def respond(s, note, action):
    if note.state != "open":
        return "closed"
    if action not in [a["id"] for a in note.actions]:
        return "bad_action"
    from backend.checkins import service as checkins
    from backend.fleet import service as fleet
    note.state, note.answer, note.answered_at = "answered", action, clock.now()
    note.acked_at = note.acked_at or clock.now()
    notifications.changed(s, note)
    inc = await s.get(Incident, note.incident_id) if note.incident_id else None
    user = await s.get(User, note.user_id)
    who, kind = user.name, note.kind
    if kind in ("dz_back", "dz_warning"):
        await checkins.on_answer(s, note, action, user)
    elif inc:
        if kind == "check" and note.user_id != inc.victim_id:
            if action == "help":
                inc.confidence = "high"
            say(s, inc, f"{who}, also in this crash, says " + ("they need help." if action == "help" else "they are OK."), kind="user")
        elif kind == "check" and action == "ok" and inc.nobody_around:
            inc.user_state = "ok"
            say(s, inc, f"{who} says they are OK, but nobody nearby can confirm. Confidence stays high: call them.", kind="user")
        elif kind == "check" and action == "ok":
            inc.user_state = "ok"
            core.lower_confidence(inc)
            say(s, inc, f"{who} says they are OK. Confidence lowered to {inc.confidence}. You decide.", kind="user")
        elif kind == "check":
            inc.user_state, inc.confidence = "needs_help", "high"
            say(s, inc, f"{who} says they need help.", kind="user")
        elif kind == "sos":
            await apply_sos(s, inc, user, action)
        elif kind == "witness" and action == "confirm":
            core.raise_confidence(inc)
            say(s, inc, f"Witness confirmed: {who}.", kind="witness")
            await withdraw_witnesses(s, inc, except_id=note.id)
        elif kind == "witness":
            confirmed = await s.scalar(select(Notification.id).where(Notification.incident_id == inc.id, Notification.kind == "witness",
                                                                    Notification.answer == "confirm"))
            if not confirmed:
                core.lower_confidence(inc)
            say(s, inc, f"{who} says there was no accident near them. Confidence {inc.confidence}.", kind="witness")
        elif kind == "helper":
            await fleet.on_helper_answer(s, inc, note, user, action)
        if kind == "check" and note.user_id == inc.victim_id:
            await withdraw_witnesses(s, inc)
        publish(s, inc)
    await db.commit(s)
    return "ok"


async def apply_sos(s, inc, user, action):
    inc.user_state, inc.confidence = ("silent_sos" if action == "silent" else "voice_sos"), "high"
    for other in (await s.scalars(select(Notification).where(Notification.incident_id == inc.id, Notification.kind == "check",
                                                              Notification.user_id == user.id, Notification.state == "open"))).all():
        other.state = "expired"   # the SOS answers the check-in too
        notifications.changed(s, other)
    if action == "silent":
        sev = dict(inc.severity or {})
        if SEVERITIES.index(sev.get("shown", "low")) < SEVERITIES.index("high"):
            inc.severity = {**sev, "shown": "high"}   # a silent SOS counts as high severity
        say(s, inc, f"Silent SOS from {user.name}: the crash is verified. They can't talk. Severity: at least high.", kind="sos")
    else:
        say(s, inc, f"Voice SOS from {user.name}: the crash is verified and they are on the line. Ask how they are.", kind="sos")
    await withdraw_witnesses(s, inc)


async def sos(s, user, action):
    """The SOS button, at any time. It joins the user's open incident, or raises one if the blackbox saw nothing."""
    if action not in ("silent", "voice"):
        return None
    q = select(Incident).where(Incident.victim_id == user.id, Incident.kind == "crash", Incident.status.not_in(core.CLOSED)).order_by(Incident.id.desc())
    inc = await s.scalar(q)
    if inc is None:
        inc = Incident(kind="crash", status="detected", confidence="high", victim_id=user.id, lat=user.last_lat, lon=user.last_lon,
                       severity={"m3": None, "m4": [], "case": None, "case_note": None, "flags": {}, "shown": "medium"}, flags={"sos_only": True})
        s.add(inc)
        await s.flush()
        say(s, inc, f"{user.name} pressed SOS: no blackbox signal, the person raised the alarm.", kind="trigger")
        await alert_witnesses(s, inc)
    await apply_sos(s, inc, user, action)
    publish(s, inc)
    await db.commit(s)
    return inc


async def on_note_expired(s, note):
    """A notification ran out of time. Check-ins that nobody answered change what the agent is told."""
    from backend.checkins import service as checkins
    if note.kind == "dz_back":
        await checkins.on_back_expired(s, note)
    elif note.kind == "check" and note.incident_id:
        inc = await s.get(Incident, note.incident_id)
        if inc and note.user_id != inc.victim_id:
            user = await s.get(User, note.user_id)
            say(s, inc, f"No answer from {user.name}, also in this crash.", kind="user")
            publish(s, inc)
        elif inc and inc.user_state == "pending":
            inc.user_state = "not_responding"
            from backend.incidents.view import status_label
            notes = (await s.scalars(select(Notification).where(Notification.incident_id == inc.id))).all()
            say(s, inc, f"No answer from {name_of(inc.victim)} after {settings.checkin_s} s. {status_label(inc, notes)}.", kind="user")
            publish(s, inc)


# ------------------------------------------------------------ the agent's actions

async def agent_confirm(s, inc, severity=None):
    if not core.usable(inc):
        return "no_crash"
    if severity is not None and severity not in SEVERITIES:
        return "bad_severity"
    level = severity or inc.agent_severity or (inc.severity or {}).get("shown") or "high"
    inc.agent_confirmed, inc.agent_severity = True, level
    if inc.status == "detected":
        inc.status = "agent_confirmed"
    say(s, inc, f"You confirmed the crash. Severity: {level}.", kind="agent")
    await withdraw_witnesses(s, inc)
    publish(s, inc)
    return "ok"


async def call_person(s, inc, who):
    """The agent phones someone. There is no phone line yet: the answer depends on what we know of them."""
    from backend.models import DeadZoneCheckin
    victim = inc.victim
    if inc.status in ("analyzing", "false_alarm") or victim is None:
        return "no_crash"
    if who == "contact":
        say(s, inc, f"You called the emergency contact: {victim.emergency_contact_name or 'not set'} {victim.emergency_contact_phone or ''}".strip() + ".", kind="agent")
    else:
        checkin = await s.scalar(select(DeadZoneCheckin).where(DeadZoneCheckin.incident_id == inc.id))
        if inc.kind == "dead_zone" and checkin and checkin.state == "driving":
            say(s, inc, f"You called {victim.name}: they answered. You can warn them before the signal goes.", kind="agent")
        elif inc.kind == "dead_zone":
            say(s, inc, f"You called {victim.name}: no network, the call can't connect.", kind="agent")
        elif inc.user_state in ("ok", "needs_help", "voice_sos"):
            say(s, inc, f"You called {victim.name}: they answered. Set the severity from what they say, then confirm.", kind="agent")
        else:
            say(s, inc, f"You called {victim.name}: no answer.", kind="agent")
    publish(s, inc)
    return "ok"


async def close(s, inc):
    """The incident is over: nothing keeps waiting for it. The ambulance is free, and anyone still heading there can stop."""
    from backend.checkins import service as checkins
    from backend.fleet import service as fleet
    if not core.is_open(inc):
        return "closed"
    inc.status, inc.closed_at = "closed", clock.now()
    await notifications.expire_open(s, inc.id)
    await fleet.release(s, inc)
    await checkins.end(s, inc)
    say(s, inc, "You closed the incident.", kind="agent")
    publish(s, inc)
    return "ok"
