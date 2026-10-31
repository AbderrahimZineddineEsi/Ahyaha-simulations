"""
The demo world and its scripted clients. A scenario is rows in the database plus a script that does what real clients
do: a blackbox sends telemetry, ambulances and helpers report where they are, phones answer notifications. Everything
goes through the services; nothing here is a rule.
"""
import asyncio
import json
import logging
import time
from datetime import timedelta

from sqlalchemy import delete, select, update

from backend import auth, clock, db, ml, realtime
from backend.checkins import service as checkins
from backend.config import settings
from backend.fleet import service as fleet
from backend.geo import routing
from backend.geo.distance import Path, Trip, haversine_m
from backend.incidents import service as incidents
from backend.models import (Ambulance, Centre, DeadZoneCheckin, Device, Dispatch, HelperRequest, Hospital, Incident, IncidentEvent,
                            Notification, PatientTransfer, PoliceRequest, PoliceStation, PoliceUnit, User)
from backend.notifications.service import notify
from backend.sim import blackbox as bbx
from backend.sim.data import (AGENT, AMBULANCES, AUTO, CHECKIN_S, DEAD_ZONE, DEMO_PASSWORD, HOSPITALS, PARKED_SPREAD, PLACES, POLICE_STATIONS, POLICE_UNITS,
                              REPLAY, ROLES, SCENARIOS, STAFF, TRANSPORT, USERS)
from backend.telemetry.service import service as telemetry
from backend.users import service as users

log = logging.getLogger("solvex")
CENTRE_NAME = "Protection Civile, demo centre"
TICK_S = 0.5
UNKNOWN_SPOT_OFFSET = (0.02, 0.02)   # where /blackbox puts a car it knows nothing about: away from the scenarios, so nothing merges


class Replies:
    """Stands in for a blackbox's WebSocket: collects what the server answers."""
    def __init__(self):
        self.messages = []

    async def send_json(self, message):
        self.messages.append(message)


class Blackbox:
    def __init__(self, scenario, lead_in_s=bbx.LEAD_IN_S, location=None, fast=False):
        self.scenario, self.lead_in_s, self.location = scenario, lead_in_s, location   # location: fn() -> (lat, lon) or a tuple
        self.fast = fast
        self.started = time.monotonic()
        self.stopped, self.stopped_by = False, "a reset on the server"

    def started_message(self, device_id):
        return {"type": "blackbox_started", "device_id": device_id, "scenario": self.scenario, "lead_in_s": self.lead_in_s,
                "elapsed_s": time.monotonic() - self.started}

    def gap(self, seq):
        """Seconds to wait before sending packet `seq`. Real time, except in the demo's scenarios (see REPLAY)."""
        if not self.fast:
            return 1
        if seq < REPLAY["skip_s"]:
            return 0.02
        return 1 / REPLAY["after_x"] if bbx.IMPACT_SEQ < seq < bbx.IMPACT_SEQ + 8 else 1

    def where(self):
        return self.location() if callable(self.location) else self.location


class Sim:
    def __init__(self):
        self.ids = {}              # the demo's user numbers (0 to 5) -> database ids
        self.agent_id = None
        self.hospital_ids = {}
        self.scenario = self.device_id = self.run_started = None
        self.run_no = 0
        self.auto = False
        self.place = "town"
        self.log = []              # lines of the dev log that no incident holds yet
        self.blackboxes = {}
        self.monitors = set()
        self.trips = {}
        self.finished = set()
        self.at_scene = {}         # dispatch id -> when its crew reached the patient
        self.dz = None
        self.task = None
        self.background = set()

    # ------------------------------------------------------------ setup

    async def start(self, app):
        settings.zone_silence_s = DEAD_ZONE["silence_s"]
        settings.checkin_s = CHECKIN_S
        settings.location_fresh_s = 86400      # the demo's people stand still, and don't report
        routing.register(self.saved_route)
        severity = ml.severity_model()
        severity.source = self.severity_source
        for key, s in SCENARIOS.items():     # the demo only makes sense if each case gets its level
            if "case" in s:
                level = severity.assess(s["case"], USERS[0]["age"], int(USERS[0]["sex"] == "male"))["m3"]["level"]
                if level != s["expect"]:
                    raise SystemExit(f"Scenario {key.upper()}'s case should be {s['expect']} for M3 but is {level}: change the case in sim/data.py.")
        async with db.session_scope() as s:
            await self.seed(s)
            if await s.scalar(select(User.last_lat).where(User.id == self.ids[0])) is None:    # a new database: put everyone in place
                await self.place_world(s, self.place)
        telemetry.listeners.append(self.tell_monitors)
        self.task = asyncio.create_task(self.run())

    async def stop(self):
        if self.task:
            self.task.cancel()
        for t in list(self.background):
            t.cancel()

    async def seed(self, s):
        """Creates the demo accounts, centre, ambulances and hospitals that are missing. Safe to run again."""
        centre = await s.scalar(select(Centre).where(Centre.name == CENTRE_NAME))
        if centre is None:
            centre = Centre(name=CENTRE_NAME, lat=PLACES["town"]["centre"][0], lon=PLACES["town"]["centre"][1])
            s.add(centre)
            await s.flush()
        for a in AMBULANCES:
            if await s.get(Ambulance, a["id"]) is None:
                s.add(Ambulance(id=a["id"], centre_id=centre.id, name=a["name"], state=a["state"], prep_s=a["prep_s"], note=a.get("note"),
                                lat=centre.lat, lon=centre.lon))
        for h in HOSPITALS:
            row = await s.scalar(select(Hospital).where(Hospital.name == h["name"]))
            if row is None:
                row = Hospital(**{k: v for k, v in h.items() if k != "key"})
                s.add(row)
                await s.flush()
            self.hospital_ids[h["key"]] = row.id
        stations = {}
        for p in POLICE_STATIONS:
            row = await s.scalar(select(PoliceStation).where(PoliceStation.name == p["name"]))
            if row is None:
                row = PoliceStation(**{k: v for k, v in p.items() if k != "key"})
                s.add(row)
                await s.flush()
            stations[p["key"]] = row.id
        for unit in POLICE_UNITS:
            if await s.get(PoliceUnit, unit["id"]) is None:
                start = PLACES[self.place]["police"][unit["id"]]["geometry"][0]
                s.add(PoliceUnit(id=unit["id"], station_id=stations[unit["station"]], name=unit["name"], force=unit["force"], lat=start[0], lon=start[1]))
        for u in USERS:
            row = await users.find_by_phone(s, u["phone"])
            if row is None:
                fields = {k: v for k, v in u.items() if k in ("age", "sex", "blood_type", "conditions", "emergency_contact_name",
                                                                "emergency_contact_phone", "skills", "is_helper")}
                row = await users.create_user(s, u["phone"], DEMO_PASSWORD, u["name"], "citizen", **fields)
            self.ids[u["id"]] = row.id
        agent = await users.find_by_phone(s, AGENT["phone"])
        if agent is None:
            agent = await users.create_user(s, AGENT["phone"], DEMO_PASSWORD, AGENT["name"], AGENT["role"], centre_id=centre.id)
        self.agent_id = agent.id
        for member in STAFF:
            if await users.find_by_phone(s, member["phone"]) is None:
                await users.create_user(s, member["phone"], DEMO_PASSWORD, member["name"], member["role"],
                                        hospital_id=self.hospital_ids.get(member.get("hospital")))

    # ------------------------------------------------------------ what the demo world looks like to the services

    async def saved_route(self, origin, dest):
        """Routes saved from OSRM, so the demo gives the same ETAs offline. Only for the demo's own places."""
        geo = PLACES[self.place]
        if haversine_m(origin, geo["crash"]) < 150:      # hospitals are far apart: the road ends near one, not on its point
            for r in geo["hospitals"].values():
                if haversine_m(dest, r["geometry"][-1]) < 1000:
                    return r
        goal = [geo["crash"]] + ([self.dz["entry"]] if self.dz else [])
        if not any(haversine_m(dest, g) < 150 for g in goal):
            return None
        for r in geo["police"].values():
            if haversine_m(origin, r["geometry"][0]) < 100:
                return r
        if haversine_m(origin, geo["centre"]) < 2500:
            r = geo["ambulance"]
            return {"geometry": [list(origin), *r["geometry"]], "duration_s": r["duration_s"], "distance_m": r["distance_m"]}
        for r in geo["helpers"].values():
            if haversine_m(origin, r["geometry"][0]) < 100:
                return {"geometry": r["geometry"], "duration_s": r["duration_s"], "distance_m": r["distance_m"]}

    def severity_source(self, device_id):
        s = SCENARIOS.get(self.scenario)
        if s and "case" in s and device_id == self.device_id:
            return {"case": s["case"], "note": f"the scenario's case below is. {s['case_source']}"}

    def say(self, text, kind="dev"):
        self.log.append({"t": time.time(), "who": "dev", "kind": kind, "text": text})

    # ------------------------------------------------------------ resetting and starting

    def cancel_background(self):
        for t in list(self.background):
            t.cancel()
        self.background.clear()

    async def reset(self, place="town"):
        """Clears every car, incident, notification and ambulance trip. Accounts, hospitals (with their beds back) and telemetry stay."""
        self.cancel_background()
        for bb in self.blackboxes.values():
            bb.stopped = True
        self.scenario = self.device_id = self.run_started = self.dz = None
        self.log, self.trips, self.finished, self.at_scene = [], {}, set(), {}
        self.place = place
        settings.traffic_factor = 1.0
        await telemetry.forget_all()
        async with db.session_scope() as s:
            for table in (IncidentEvent, Notification, Dispatch, HelperRequest, PoliceRequest, DeadZoneCheckin, PatientTransfer, Incident):
                await s.execute(delete(table))
            await s.execute(delete(Device).where(Device.id.like("user0-run%")))
            await self.place_world(s, place)
        await realtime.emit_dispatchers("reset", {})

    async def place_world(self, s, place):
        centre = PLACES[place]["centre"]
        spread = PARKED_SPREAD[place]
        for a in AMBULANCES:
            await s.execute(update(Ambulance).where(Ambulance.id == a["id"]).values(
                state=a["state"], lat=centre[0] + a["parked"][0] * spread, lon=centre[1] + a["parked"][1] * spread, located_at=clock.now()))
        for h in HOSPITALS:       # a delivered patient takes a bed: each run starts with the same choice of hospital
            await s.execute(update(Hospital).where(Hospital.name == h["name"]).values(beds_free=h["beds_free"]))
        for unit in POLICE_UNITS:
            start = PLACES[place]["police"][unit["id"]]["geometry"][0]
            await s.execute(update(PoliceUnit).where(PoliceUnit.id == unit["id"]).values(state="available", lat=start[0], lon=start[1], located_at=clock.now()))
        for u in USERS:
            pos = PLACES[place]["users"][str(u["id"])]
            await s.execute(update(User).where(User.id == self.ids[u["id"]]).values(
                last_lat=pos[0], last_lon=pos[1], last_driving=u["driving"], last_seen_at=clock.now()))

    async def start_scenario(self, key, run_blackbox=True):
        s = SCENARIOS[key]
        await self.reset(s["place"])
        self.run_no += 1
        self.scenario, self.device_id, self.run_started = key, f"user0-run{self.run_no}", time.monotonic()
        settings.traffic_factor = s["traffic"]
        self.say(f"Scenario {s['title']}", "scenario")
        if "case" in s:
            self.say(f"Severity case: {s['case_source']}", "case")
        async with db.session_scope() as db_s:
            db_s.add(Device(id=self.device_id, key_hash=auth.hash_key(auth.new_device_key()), owner_id=self.ids[0], body_type="car"))
        dead = s.get("kind") == "dead_zone"
        if dead:
            self.start_dead_zone()
        geo = PLACES[s["place"]]
        # The recording is the same in every scenario; in scenario 3 it is a drive with no crash the server can see
        location = (lambda: tuple(self.dz["car"])) if dead else tuple(geo["crash"])
        bb = Blackbox("normal" if dead else s.get("recording", "crash"), lead_in_s=0, location=location, fast=not dead)
        self.blackboxes[self.device_id] = bb
        if run_blackbox:
            self.spawn(self.consume(self.run_blackbox(self.device_id, bb)))
        return self.device_id

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self.background.add(task)
        task.add_done_callback(self.background.discard)
        return task

    @staticmethod
    async def consume(generator):
        async for _ in generator:
            pass

    # ------------------------------------------------------------ blackboxes

    async def tell_monitors(self, message):
        for ws in list(self.monitors):
            try:
                await ws.send_json(message)
            except Exception:
                self.monitors.discard(ws)

    async def run_blackbox(self, device_id, bb):
        """Sends one packet per second through the same path as a real blackbox, and prints each one."""
        replies = Replies()
        next_at = bb.started
        try:
            await self.tell_monitors(bb.started_message(device_id))
            yield f"{device_id}: {bb.scenario}. Press Ctrl+C to stop.\n\n"
            for seq, (stage, packet) in enumerate(bbx.scenario_packets(bb.scenario, bb.lead_in_s)):
                next_at += bb.gap(seq)     # a second is sent once it is complete, so the prediction comes 8 s after the impact
                await asyncio.sleep(max(0, next_at - time.monotonic()))
                if bb.stopped:
                    yield f"\nStopped by {bb.stopped_by}.\n"
                    return
                packet = bbx.stamp(packet, device_id, seq, bb.where())
                raw = json.dumps(packet, separators=(",", ":"))
                await telemetry.handle(raw, replies, trusted=True)
                yield bbx.packet_line(stage, packet, len(raw))
                for message in replies.messages:
                    yield bbx.reply_line(message)
                replies.messages.clear()
        finally:
            self.blackboxes.pop(device_id, None)
            self.spawn(self.tell_monitors({"type": "blackbox_stopped", "device_id": device_id}))

    def stop_blackbox(self, why):
        bb = self.blackboxes.get(self.device_id)
        if bb:
            bb.stopped, bb.stopped_by = True, why

    # ------------------------------------------------------------ scenario 3: the driver and the dead zone

    def start_dead_zone(self):
        """The server can't see a crash with no network, so the only safety net is what the driver says before going in."""
        d = DEAD_ZONE
        road = Path(PLACES[self.place]["ambulance"]["geometry"])
        start, entry = road.length - d["start_m"], road.length - d["entry_m"]
        centre = road.from_end(d["entry_m"] / 2)
        entry_point = road.at_distance(entry)
        self.dz = {"phase": "driving", "asked": False, "crashed": False, "entered_at": None, "patched": False,
                   "trip": Trip(Path(road.part(start, entry)), time.monotonic(), d["drive_s"]), "start": road.at_distance(start),
                   "entry": entry_point, "car": road.at_distance(start),
                   "zone": {"center": centre, "radius_m": haversine_m(centre, entry_point) * 1.05}}

    async def dead_zone_step(self, s):
        dz, d, now = self.dz, DEAD_ZONE, time.monotonic()
        victim = await s.get(User, self.ids[0])
        checkin = await s.scalar(select(DeadZoneCheckin).where(DeadZoneCheckin.user_id == victim.id).order_by(DeadZoneCheckin.id.desc()))
        if checkin and checkin.state == "turned_back" and dz["phase"] != "turned_back":
            dz["phase"] = "turned_back"
            self.stop_blackbox("the driver turning back")
        if not dz["asked"] and now - self.run_started >= d["ask_s"]:
            dz["asked"] = True
            data = {"road": d["road"], "zone": dz["zone"], "entry": dz["entry"], "default": d["default"],
                    "back": {k: list(v) for k, v in d["back"].items()}}
            await notify(s, victim.id, "dz_back", "No network ahead",
                         f"In about 15 minutes you enter a zone with no network, on {d['road']}. When do you expect to be back?",
                         [{"id": k, "label": v[0]} for k, v in d["back"].items()], d["answer_s"], data=data)
            self.say("The app asked the driver when they expect to be back.", "dead_zone")
        if dz["phase"] == "driving":
            trip = dz["trip"]
            dz["car"] = trip.position(now)
            await users.update_location(s, victim, *dz["car"], driving=True)   # a tick covers more road than the zone's margin, so this includes the last one, at the edge
            if trip.done(now) and checkin:      # the car is in the zone: no more locations reach the server
                dz["phase"], dz["entered_at"] = "in_zone", now
                self.stop_blackbox("the loss of the signal")
                self.say(f"{victim.name} entered the zone. Nothing reaches the server from here.", "dead_zone")
        elif dz["phase"] == "in_zone":
            if not dz["crashed"] and now >= dz["entered_at"] + d["crash_after_s"]:
                dz["crashed"] = True
                self.say("The car crashed in the zone. The blackbox keeps the data, but nothing can reach the server. Only this log knows.", "dead_zone")
            if checkin and checkin.state == "in_zone" and not dz["patched"]:     # the promised hour, compressed
                dz["patched"] = True
                left = max(0.0, dz["entered_at"] + d["overdue_after_s"] - now)
                checkin.overdue_at = clock.now() + timedelta(seconds=left)
                self.say(f"{victim.name} promised to be back by {clock.local_hhmm(checkin.back_by)}. The return time is compressed for the demo: "
                         f"the alarm comes {d['overdue_after_s']} s after the signal is lost.", "dead_zone")

    # ------------------------------------------------------------ the chores every tick: vehicles move, phones answer

    async def run(self):
        while True:
            await asyncio.sleep(TICK_S)
            try:
                async with db.session_scope() as s:
                    if self.dz:
                        await self.dead_zone_step(s)
                    await self.move_vehicles(s)     # also for an incident from /blackbox, with no scenario running
                    if self.auto:
                        await self.auto_answer(s)
            except Exception:
                log.exception("the simulation's tick failed")

    def trip_seconds(self, kind):
        return SCENARIOS.get(self.scenario, SCENARIOS["a"])["trip_s" if kind == "amb" else "helper_trip_s"]

    async def move_vehicles(self, s):
        now = time.monotonic()
        for d in (await s.scalars(select(Dispatch).where(Dispatch.state == "on_scene"))).all():
            arrived = self.at_scene.setdefault(d.id, now)     # once the agent has chosen a hospital, the crew leaves with the patient
            if now - arrived >= TRANSPORT["scene_s"] and await s.scalar(select(PatientTransfer.id).where(PatientTransfer.incident_id == d.incident_id,
                                                                                                         PatientTransfer.state == "incoming")):
                await fleet.leave_scene(s, await s.get(Ambulance, d.ambulance_id))
        for d in (await s.scalars(select(Dispatch).where(Dispatch.state.in_(fleet.MOVING)))).all():
            key = (d.state, d.id)
            if key in self.finished:
                continue
            amb = await s.get(Ambulance, d.ambulance_id)
            if key not in self.trips:
                self.trips[key] = Trip(Path(d.route), now, TRANSPORT["trip_s"]) if d.state == "transport" else \
                    Trip(Path(d.route), now + amb.prep_s * self.trip_seconds("amb") / d.eta_s, self.trip_seconds("amb"))   # the crew's preparation, compressed like the drive
            trip = self.trips[key]
            if now < trip.started:
                continue
            if trip.done(now):
                self.finished.add(key)
                await self.found_by(s, d.incident_id, amb.name, trip.path.points[-1])
            await fleet.report_ambulance(s, amb, *trip.position(now))
        for req in (await s.scalars(select(HelperRequest).where(HelperRequest.state.in_(("accepted", "confirmed"))))).all():
            key = ("helper", req.id)
            if key in self.finished:
                continue
            trip = self.trips.setdefault(key, Trip(Path(req.route), now, self.trip_seconds("helper")))
            if trip.done(now):
                self.finished.add(key)
                await self.found_by(s, req.incident_id, req.user.name, trip.path.points[-1])
            await users.update_location(s, req.user, *trip.position(now), driving=False)
        for req in (await s.scalars(select(PoliceRequest).where(PoliceRequest.state == "en_route"))).all():
            key = ("police", req.id)
            if key in self.finished:
                continue
            trip = self.trips.setdefault(key, Trip(Path(req.route), now, self.trip_seconds("helper")))
            if trip.done(now):
                self.finished.add(key)
                await self.found_by(s, req.incident_id, req.unit.name, trip.path.points[-1])
            await fleet.report_unit(s, req.unit, *trip.position(now))

    async def found_by(self, s, incident_id, who, point):
        """In a dead zone nobody knows where the crash is until a responder gets there and says so."""
        inc = await s.get(Incident, incident_id)
        if inc and inc.kind == "dead_zone" and inc.status in ("overdue", "dispatched"):
            await checkins.found(s, inc, point[0], point[1], who)

    async def auto_answer(self, s):
        """What the Auto switch plays: a witness confirms, a helper accepts. User 0 never answers."""
        now = clock.now()
        for number, kind, answer, wait_s in ((1, "witness", "confirm", AUTO["witness_s"]), (4, "helper", "accept", AUTO["helper_s"])):
            note = await s.scalar(select(Notification).where(Notification.user_id == self.ids[number], Notification.kind == kind,
                                                             Notification.state == "open", Notification.created_at <= now - timedelta(seconds=wait_s)))
            if note:
                await incidents.respond(s, note, answer)

    # ------------------------------------------------------------ what /dev shows

    async def state(self):
        out = {"server_time": time.time(), "auto": self.auto, "place": self.place, "scenario": None, "incident": None, "dead_zone": None,
               "users": [], "events": list(self.log), "agent": None}
        async with db.session() as s:
            for u in USERS:
                row = await s.get(User, self.ids[u["id"]])
                out["users"].append({"id": u["id"], "name": u["name"], "role": ROLES.get(self.place, {}).get(u["id"], u["role"]),
                                     "token": auth.make_token(row)})
            out["agent"] = {"token": auth.make_token(await s.get(User, self.agent_id)), "phone": AGENT["phone"]}
            sc = SCENARIOS.get(self.scenario)
            if sc:
                out["scenario"] = {"key": self.scenario, "title": sc["title"], "device_id": self.device_id, "kind": sc.get("kind", "crash"),
                                   "elapsed_s": time.monotonic() - self.run_started, "trip_s": sc["trip_s"], "traffic": sc["traffic"],
                                   "skip_s": 0 if sc.get("kind") == "dead_zone" else REPLAY["skip_s"],
                                   "after_x": 1 if sc.get("kind") == "dead_zone" else REPLAY["after_x"]}
                q = select(Incident).order_by(Incident.id.desc())
                q = q.where(Incident.victim_id == self.ids[0], Incident.kind == "dead_zone") if sc.get("kind") == "dead_zone" \
                    else q.where(Incident.device_id == self.device_id)
                inc = await s.scalar(q)
                if inc:
                    out["incident"] = {"id": inc.id, "stage": inc.status, "m2": inc.m2_level and {"level": inc.m2_level, "p_crash": inc.m2_p}}
                    for e in (await s.scalars(select(IncidentEvent).where(IncidentEvent.incident_id == inc.id))).all():
                        out["events"].append({"t": clock.epoch(e.at), "who": e.audience, "kind": e.kind, "text": e.text})
                if self.dz:
                    checkin = await s.scalar(select(DeadZoneCheckin).where(DeadZoneCheckin.user_id == self.ids[0]).order_by(DeadZoneCheckin.id.desc()))
                    phase = self.dz["phase"]
                    if checkin and checkin.state in ("overdue", "found", "returned"):
                        phase = checkin.state if checkin.state != "returned" else "found"
                    out["dead_zone"] = {"phase": phase, "crashed": self.dz["crashed"], "found": bool(checkin and checkin.state == "found")}
        out["events"].sort(key=lambda e: e["t"])
        return out


sim = Sim()
