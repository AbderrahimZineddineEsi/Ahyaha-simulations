"""
The Protection Civile response to one crash: what happens after M2 says "crash".

The engine holds everything the dashboard and /dev show: the incident, the people, the ambulances, the
notifications sent to phones, and a timeline. Notifications and answers go through `notify()` and `respond()`, which
are what the mobile app will use later. It lives in memory: a restart or a reset starts clean.
"""
import asyncio
import logging
import time

from demo_data import (AMBULANCES, AUTO, CHECKIN_S, HELPER_RANGES_KM, PARKED_SPREAD, PLACES, ROLES, SCENARIOS, USERS,
                       WITNESS_RADIUS_M, YELLOW_LINE_M)
from world import Path, Trip, haversine_m

log = logging.getLogger("solvex")
CONFIDENCE = ("low", "medium", "high")
SEVERITIES = ("low", "medium", "high", "extreme")
ALARM = ("likely", "crash")
FIRST_AID = "Scene safety first: hazard lights on, stay off the road, no smoking near fuel. Then check breathing. Do not move the neck."


def pct(p):
    return "over 99.9%" if p >= 0.9995 else f"{p * 100:.1f}%"


class Engine:
    def __init__(self, severity):
        self.severity = severity
        self.ops = set()           # the dashboard and /dev
        self.user_sockets = {}     # user id -> sockets: where the mobile app will listen
        self.auto = False
        self.run_no = 0
        self.incident_no = 0
        self._reset_state()
        for key, scenario in SCENARIOS.items():  # the demo only makes sense if each case gets its level
            victim = USERS[0]
            level = severity.assess(scenario["case"], victim["age"], victim["male"])["m3"]["level"]
            if level != scenario["expect"]:
                raise SystemExit(f"Scenario {key.upper()}'s case should be {scenario['expect']} for M3 but is {level}: change the case in demo_data.py.")

    def _reset_state(self, place="town"):
        self.place, self.geo = place, PLACES[place]
        self.helper_paths = {int(k): Path(v["geometry"]) for k, v in self.geo["helpers"].items()}
        self.scenario = self.device_id = self.run_started = self.incident = None
        self.events, self.notifications, self.due = [], [], []
        self.note_no = 0
        self.users = {u["id"]: {**u, **ROLES.get(place, {}).get(u["id"], {}), "pos": self.geo["users"][str(u["id"])], "trip": None} for u in USERS}
        spread = PARKED_SPREAD[place]
        self.ambulances = {a["id"]: {**a, "pos": [self.geo["centre"][0] + a["parked"][0] * spread, self.geo["centre"][1] + a["parked"][1] * spread], "trip": None}
                           for a in AMBULANCES}

    # ------------------------------------------------------------ small helpers

    def say(self, text, who="agent", kind="info"):
        """A line in the timeline. who is 'agent' for the dashboard and 'dev' for the /dev log only."""
        self.events.append({"t": time.time(), "who": who, "kind": kind, "text": text})

    def name(self, uid):
        return self.users[uid]["name"]

    def schedule(self, delay_s, what, arg=None):
        self.due.append((time.monotonic() + delay_s, what, arg))

    def lower_confidence(self):
        inc = self.incident
        inc["confidence"] = CONFIDENCE[max(0, CONFIDENCE.index(inc["confidence"]) - 1)]

    def status_label(self):
        inc = self.incident
        if inc["stage"] == "analyzing":
            return "Analyzing the trigger"
        if inc["stage"] == "false_alarm":
            return "No crash"
        if inc["agent"]["confirmed"]:
            return "Confirmed by Protection Civile"
        parts = [f"{inc['confidence'].title()} confidence"]
        if inc["nobody_around"]:
            parts.append("nobody nearby")
        parts += {"not_responding": ["user not responding"], "ok": ["user says OK"], "needs_help": ["user asks for help"],
                  "silent_sos": ["silent SOS"], "voice_sos": ["voice SOS"]}.get(inc["user_state"], [])
        if inc["witness"]["confirmed_by"]:
            parts.append("confirmed by witness")
        return ", ".join(parts)

    async def notify(self, uid, kind, title, text, actions=(), deadline_s=None):
        self.note_no += 1
        note = {"id": self.note_no, "user_id": uid, "kind": kind, "title": title, "text": text, "actions": list(actions),
                "state": "open" if actions else "delivered", "answer": None, "sent_at": time.time(),
                "_deadline": time.monotonic() + deadline_s if deadline_s else None}
        self.notifications.append(note)
        message = {"type": "notification", **self.public_note(note)}
        for ws in list(self.user_sockets.get(uid, ())):
            await self.send(ws, message)
        return note

    async def send(self, ws, message):
        try:
            await ws.send_json(message)
        except Exception as e:
            log.warning("couldn't send %s: %s", message["type"], e)

    # ------------------------------------------------------------ what the pages see

    def public_note(self, note):
        out = {k: v for k, v in note.items() if not k.startswith("_")}
        out["remaining_s"] = None if note["_deadline"] is None else max(0.0, note["_deadline"] - time.monotonic())
        return out

    def snapshot(self):
        now = time.monotonic()
        inc = self.incident
        incident = None
        if inc:
            dispatch = None
            if inc["dispatch"]:
                d = inc["dispatch"]
                progress = min(1.0, (now - d["started"]) / d["total_s"])
                dispatch = {"ambulance_id": d["ambulance_id"], "eta_s": d["eta_s"], "eta_remaining_s": round(d["eta_s"] * (1 - progress)),
                            "progress": progress}
            incident = {**{k: inc[k] for k in ("id", "stage", "confidence", "user_state", "m2", "severity", "witness", "agent",
                                                  "helpers", "trigger", "location", "nobody_around", "help_radius_km")},
                        "status": self.status_label(), "dispatch": dispatch, "helper_ranges": self.helper_ranges()}
        s = SCENARIOS.get(self.scenario)
        return {
            "type": "state", "server_time": time.time(), "auto": self.auto, "place": self.place, "incident": incident, "events": self.events,
            "scenario": None if not s else {"key": self.scenario, "title": s["title"], "device_id": self.device_id,
                                            "elapsed_s": now - self.run_started, "trip_s": s["trip_s"], "traffic": s["traffic"]},
            "users": [{**{k: v for k, v in u.items() if k != "trip"}, **self.distance_to_crash(u)} for u in self.users.values()],
            "ambulances": [{**{k: v for k, v in a.items() if k != "trip"}, "eta_s": self.eta_s(a)} for a in self.ambulances.values()],
            "notifications": [self.public_note(n) for n in self.notifications],
        }

    def eta_s(self, amb):
        traffic = SCENARIOS[self.scenario]["traffic"] if self.scenario else 1.0
        return round(self.geo["ambulance"]["duration_s"] * traffic + amb["prep_s"])

    def crash_distance_m(self, user):
        return haversine_m(user["home"] if "home" in user else user["pos"], self.geo["crash"])

    def distance_to_crash(self, user):
        out = {"distance_km": round(self.crash_distance_m(user) / 1000, 1)}
        route = self.geo["helpers"].get(str(user["id"]))
        return {**out, "eta_s": route["duration_s"]} if route else out

    def helper_ranges(self):
        """For each range of the call for help: how many helpers are in it, and how many haven't been asked yet."""
        helpers = [u for u in self.users.values() if u.get("helper")]
        asked = self.incident["helpers"]
        return [{"km": km, "count": sum(self.crash_distance_m(u) <= km * 1000 for u in helpers),
                 "available": sum(self.crash_distance_m(u) <= km * 1000 and u["id"] not in asked for u in helpers)}
                for km in HELPER_RANGES_KM]

    async def broadcast(self):
        message = self.snapshot()
        await asyncio.gather(*(self.send(ws, message) for ws in list(self.ops)))

    # ------------------------------------------------------------ from the telemetry pipeline

    async def reset(self):
        self._reset_state()
        await self.broadcast()

    async def start_scenario(self, key):
        s = SCENARIOS[key]
        self._reset_state(s["place"])
        self.run_no += 1
        self.incident_no = 0
        self.scenario, self.device_id, self.run_started = key, f"user0-run{self.run_no}", time.monotonic()
        self.say(f"Scenario {s['title']}", who="dev", kind="scenario")
        self.say(f"Severity case: {s['case_source']}", who="dev", kind="case")
        await self.broadcast()
        return self.device_id

    async def on_trigger(self, device_id, seq, peak_g, location):
        if device_id != self.device_id or self.incident:
            return
        self.incident_no += 1
        self.incident = {"id": self.incident_no, "stage": "analyzing", "location": self.geo["crash"], "trigger": {"seq": seq, "peak_g": round(peak_g, 2)},
                         "m2": None, "severity": None, "confidence": None, "user_state": None, "nobody_around": False, "help_radius_km": None,
                         "witness": {"confirmed_by": [], "denied_by": []}, "agent": {"confirmed": False, "severity": None},
                         "dispatch": None, "helpers": {}}
        self.say(f"Trigger on {self.name(0)}'s car: impact of {peak_g:.1f} g. Checking the last 16 seconds.", kind="trigger")
        await self.broadcast()

    async def on_prediction(self, reply, summary):
        inc = self.incident
        if reply["device_id"] != self.device_id or not inc or inc["m2"]:
            return
        level = reply["level"]
        inc["m2"] = {"level": level, "p_crash": reply["p_crash"]}
        self.say(f"The blackbox measured: dv {summary['dv_kmh']} km/h, direction {summary['pdof_deg']} deg, rollover {summary['rollover']}, "
                 f"multiple impacts {summary['multi_impact']}. Not used for severity: the scenario's case below is.", who="dev", kind="blackbox")
        if level not in ALARM:
            inc["stage"] = "false_alarm"
            self.say(f"No crash: M2 gives {pct(reply['p_crash'])}.", kind="m2")
            await self.broadcast()
            return

        inc["confidence"] = "high" if level == "crash" else "medium"
        inc["stage"] = "detected"
        self.say(f"Crash detected by M2: {level}, {pct(reply['p_crash'])}. Confidence {inc['confidence']}.", kind="m2")

        scenario, victim = SCENARIOS[self.scenario], self.users[0]
        inc["severity"] = {**self.severity.assess(scenario["case"], victim["age"], victim["male"]), "case": scenario["case"]}
        m3, regions = inc["severity"]["m3"], inc["severity"]["m4"]
        inc["severity"]["shown"] = m3["level"]  # the predicted level stands until a person says otherwise
        flagged = [r["region"] for r in regions if r["state"] != "unlikely"][:3]
        self.say(f"Severity estimate: {m3['level']} ({pct(m3['p'])}). Check first: {', '.join(flagged) if flagged else 'no region stands out'}.", kind="severity")

        inc["user_state"] = "pending"
        await self.notify(0, "check", "Are you OK?", "We detected a crash. Are you OK?",
                          [{"id": "ok", "label": "I'm OK"}, {"id": "help", "label": "I need help"}], CHECKIN_S)
        self.say(f"'Are you OK?' sent to {self.name(0)}. {CHECKIN_S} s to answer.", kind="notification")
        nearby = [u for u in self.users.values() if u["id"] != 0 and self.crash_distance_m(u) <= WITNESS_RADIUS_M]
        for u in nearby:
            meters = round(self.crash_distance_m(u), -1)
            await self.notify(u["id"], "witness", "Accident nearby", f"A crash may have happened about {meters:.0f} m from you. Can you confirm it?",
                              [{"id": "confirm", "label": "Confirm"}, {"id": "deny", "label": "Deny"}])
        if nearby:
            self.say(f"Accident alert sent to the {len(nearby)} people nearby.", kind="notification")
            if self.auto:
                self.schedule(AUTO["witness_s"], "auto_witness")
        else:
            # Nobody can confirm it, so the crash stays at high confidence whatever the driver answers
            inc["nobody_around"], inc["confidence"] = True, "high"
            km = WITNESS_RADIUS_M / 1000
            self.say(f"Nobody within {km:g} km, so no witness can confirm. Call the driver to check the severity.", kind="notification")
            await self.notify(0, "sos", "Can't talk or move?", "Use this at any time, even after the check above ends.",
                              [{"id": "silent", "label": "Silent SOS"}, {"id": "voice", "label": "Voice SOS"}])
        await self.broadcast()

    # ------------------------------------------------------------ answers from phones

    async def respond(self, note_id, action):
        note = next((n for n in self.notifications if n["id"] == note_id), None)
        if note is None:
            return "not_found"
        if note["state"] != "open":
            return "closed"
        if action not in [a["id"] for a in note["actions"]]:
            return "bad_action"
        note["state"], note["answer"] = "answered", action
        inc, uid = self.incident, note["user_id"]
        who = self.name(uid)
        if inc:
            kind = note["kind"]
            if kind == "check" and action == "ok" and inc["nobody_around"]:
                inc["user_state"] = "ok"
                self.say(f"{who} says they are OK, but nobody nearby can confirm. Confidence stays high: call them.", kind="user")
            elif kind == "check" and action == "ok":
                inc["user_state"] = "ok"
                self.lower_confidence()
                self.say(f"{who} says they are OK. Confidence lowered to {inc['confidence']}. You decide.", kind="user")
            elif kind == "check":
                inc["user_state"], inc["confidence"] = "needs_help", "high"
                self.say(f"{who} says they need help.", kind="user")
            elif kind == "sos":
                inc["user_state"], inc["confidence"] = ("silent_sos" if action == "silent" else "voice_sos"), "high"
                for other in self.notifications:  # the SOS answers the check-in too
                    if other["kind"] == "check" and other["state"] == "open":
                        other["state"] = "expired"
                if action == "silent":
                    if SEVERITIES.index(inc["severity"]["shown"]) < SEVERITIES.index("high"):
                        inc["severity"]["shown"] = "high"  # a silent SOS counts as high severity
                    self.say(f"Silent SOS from {who}: the crash is verified. They can't talk. Severity: at least high.", kind="sos")
                else:
                    self.say(f"Voice SOS from {who}: the crash is verified and they are on the line. Ask how they are.", kind="sos")
            elif kind == "witness" and action == "confirm":
                inc["witness"]["confirmed_by"].append(uid)
                inc["confidence"] = "high"
                first = len(inc["witness"]["confirmed_by"]) == 1
                self.say(f"Witness confirmed: {who}." if first else f"Another witness confirmed: {who}.", kind="witness")
            elif kind == "witness":
                inc["witness"]["denied_by"].append(uid)
                if not inc["witness"]["confirmed_by"]:
                    self.lower_confidence()
                self.say(f"{who} says there was no accident near them. Confidence {inc['confidence']}.", kind="witness")
            elif kind == "helper" and action == "accept":
                inc["helpers"][uid] = "accepted"
                self.users[uid]["home"] = self.users[uid]["pos"]  # distances keep meaning where they started from
                self.users[uid]["trip"] = Trip(self.helper_paths[uid], time.monotonic(), SCENARIOS[self.scenario]["helper_trip_s"])
                self.say(f"{who} accepted ({self.users[uid]['skill']}) and is on the way.", kind="helper")
            elif kind == "helper":
                inc["helpers"][uid] = "declined"
                self.say(f"{who} can't help.", kind="helper")
        await self.broadcast()
        return "ok"

    # ------------------------------------------------------------ the agent's actions

    def usable(self):
        inc = self.incident
        return inc is not None and inc["stage"] not in ("analyzing", "false_alarm")

    async def agent_confirm(self, severity=None):
        if not self.usable():
            return "no_crash"
        if severity is not None and severity not in SEVERITIES:
            return "bad_severity"
        inc = self.incident
        level = severity or inc["agent"]["severity"] or inc["severity"]["shown"]
        inc["agent"] = {"confirmed": True, "severity": level}
        if inc["stage"] == "detected":
            inc["stage"] = "agent_confirmed"
        self.say(f"You confirmed the crash. Severity: {level}.", kind="agent")
        await self.broadcast()
        return "ok"

    async def dispatch(self, ambulance_id):
        if not self.usable():
            return "no_crash"
        inc, amb = self.incident, self.ambulances.get(ambulance_id)
        if inc["dispatch"]:
            return "already_dispatched"
        if amb is None or amb["state"] != "available":
            return "unavailable"
        scenario, now = SCENARIOS[self.scenario], time.monotonic()
        eta = self.eta_s(amb)
        wait_s = amb["prep_s"] * scenario["trip_s"] / eta  # the crew's preparation, compressed like the drive
        path = Path([amb["pos"]] + self.geo["ambulance"]["geometry"])  # it leaves from where it is parked
        amb["state"], amb["trip"] = "dispatched", Trip(path, now + wait_s, scenario["trip_s"])
        inc["dispatch"] = {"ambulance_id": ambulance_id, "eta_s": eta, "started": now, "total_s": wait_s + scenario["trip_s"]}
        inc["stage"] = "dispatched"
        self.say(f"{amb['name']} dispatched. ETA {round(eta / 60)} min.", kind="dispatch")
        await self.broadcast()
        return "ok"

    async def call_helpers(self, radius_km=HELPER_RANGES_KM[0]):
        if not self.usable():
            return "no_crash"
        if radius_km not in HELPER_RANGES_KM:
            return "bad_radius"
        inc = self.incident
        asked = [u for u in self.users.values() if u.get("helper") and u["id"] not in inc["helpers"] and self.crash_distance_m(u) <= radius_km * 1000]
        if not asked:
            return "nobody_left"
        inc["help_radius_km"] = radius_km
        for u in asked:
            inc["helpers"][u["id"]] = "requested"
            km = self.crash_distance_m(u) / 1000
            await self.notify(u["id"], "helper", "Someone needs first aid", f"A crash victim needs help about {km:.1f} km from you. Can you go?",
                              [{"id": "accept", "label": "Yes, I'm going"}, {"id": "decline", "label": "I can't"}])
        self.say(f"Call for help within {radius_km} km: {len(asked)} helpers alerted.", kind="helper")
        if self.auto:
            self.schedule(AUTO["helper_s"], "auto_helper")
        await self.broadcast()
        return "ok"

    async def call_person(self, who):
        """The agent phones someone. There is no phone line yet: the answer depends on what we know of them."""
        if not self.usable():
            return "no_crash"
        inc, victim = self.incident, self.users[0]
        if who == "contact":
            self.say(f"You called the emergency contact: {victim['emergency_contact']}.", kind="agent")
        elif inc["user_state"] in ("ok", "needs_help", "voice_sos"):
            self.say(f"You called {victim['name']}: they answered. Set the severity from what they say, then confirm.", kind="agent")
        else:
            self.say(f"You called {victim['name']}: no answer.", kind="agent")
        await self.broadcast()
        return "ok"

    async def choose_helper(self, uid):
        inc = self.incident
        if not self.usable() or inc["helpers"].get(uid) != "accepted":
            return "not_accepted"
        inc["helpers"][uid] = "confirmed"
        await self.notify(uid, "info", "Protection Civile confirms you", f"Go to the crash. {FIRST_AID}")
        self.say(f"You confirmed {self.name(uid)} is going to the scene.", kind="agent")
        await self.broadcast()
        return "ok"

    # ------------------------------------------------------------ time

    async def tick(self):
        now = time.monotonic()
        changed = False
        for note in self.notifications:
            if note["state"] == "open" and note["_deadline"] is not None and now >= note["_deadline"]:
                note["state"] = "expired"
                changed = True
                if note["kind"] == "check" and self.incident and self.incident["user_state"] == "pending":
                    self.incident["user_state"] = "not_responding"
                    self.say(f"No answer from {self.name(note['user_id'])} after {CHECKIN_S} s. {self.status_label()}.", kind="user")
        for item in [d for d in self.due if d[0] <= now]:
            self.due.remove(item)
            changed |= await self.auto_answer(item[1])
        changed |= await self.move(now)
        if changed:
            await self.broadcast()

    async def move(self, now):
        moving = False
        inc = self.incident
        for amb in self.ambulances.values():
            trip = amb["trip"]
            if trip is None or amb["state"] != "dispatched":
                continue
            moving = True
            amb["pos"] = trip.position(now)
            for u in self.users.values():
                if u["driving"] and u["id"] != 0 and not u.get("alerted") and haversine_m(amb["pos"], u["pos"]) <= YELLOW_LINE_M:
                    u["alerted"] = True
                    await self.notify(u["id"], "alert", "Ambulance coming", "Ambulance coming. Clear the yellow line.")
                    self.say(f"Yellow-line alert sent to {u['name']}.", kind="alert")
            if trip.done(now):
                amb["state"] = "on_scene"
                if inc:
                    inc["stage"] = "on_scene"
                self.say(f"{amb['name']} is on the scene.", kind="dispatch")
        for u in self.users.values():
            trip = u["trip"]
            if trip is not None and inc and inc["helpers"].get(u["id"]) in ("accepted", "confirmed"):
                moving = True
                u["pos"] = trip.position(now)
                if trip.done(now):
                    inc["helpers"][u["id"]] = "on_scene"
                    self.say(f"{u['name']} is on the scene.", kind="helper")
        return moving

    async def auto_answer(self, what):
        """What the Auto switch plays: a witness confirms, a helper accepts. User 0 never answers."""
        wanted = {"auto_witness": (1, "witness", "confirm"), "auto_helper": (4, "helper", "accept")}[what]
        note = next((n for n in self.notifications if n["user_id"] == wanted[0] and n["kind"] == wanted[1] and n["state"] == "open"), None)
        if note:
            await self.respond(note["id"], wanted[2])
        return False

    async def run(self):
        while True:
            await asyncio.sleep(0.5)
            try:
                await self.tick()
            except Exception:
                log.exception("the engine's tick failed")
