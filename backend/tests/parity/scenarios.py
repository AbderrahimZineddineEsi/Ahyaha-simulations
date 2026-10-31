"""Drives the four demo scenarios through the API. Usage: scenarios [a|b|2|3|sos|run|all]"""
import sys
import time

from backend.tests.parity.common import c, current, get, phone, post, reset, wait


def check(label, ok, extra=""):
    print(("ok   " if ok else "FAIL ") + label, extra)
    if not ok:
        raise SystemExit(1)


def scenario_a():
    print("== A: ambulance close, severity medium")
    reset("a")
    d = wait(lambda d: d["stage"] == "detected")
    check("severity medium", d["severity"]["shown"] == "medium", f"{d['severity']['m3']['p']:.3f}")
    check("legs first", [r["region"] for r in d["severity"]["m4"] if r["state"] != "unlikely"][:1] == ["legs"])
    check("5 people asked to confirm", len([n for n in d["notifications"] if n["kind"] == "witness"]) == 5)
    i = d["id"]
    check("confirm", post(f"/incidents/{i}/confirm", {"severity": "medium"}) == 200)
    check("dispatch", post(f"/incidents/{i}/dispatch", {"ambulance_id": "amb-1"}) == 200)
    d = current()
    check("ETA about 7 min", 6 <= d["dispatch"]["eta_s"] / 60 <= 8, f"{d['dispatch']['eta_s'] / 60:.1f} min")
    d = wait(lambda d: d["stage"] == "on_scene", 60, 3)
    texts = [e["text"] for e in d["events"]]
    check("yellow-line alerts", any("Yellow-line alert" in t for t in texts))
    check("ambulance on the scene", any("Ambulance 1 is on the scene" in t for t in texts))


def scenario_b():
    print("== B: ambulance far in traffic, severity extreme, helper first (Auto on)")
    reset("b", auto=True)
    d = wait(lambda d: d["stage"] == "detected")
    check("severity extreme", d["severity"]["shown"] == "extreme", f"{d['severity']['m3']['p']:.3f}")
    i = d["id"]
    wait(lambda d: "confirmed by witness" in d["status"], 40)
    check("confirm", post(f"/incidents/{i}/confirm", {}) == 200)
    check("dispatch", post(f"/incidents/{i}/dispatch", {"ambulance_id": "amb-1"}) == 200)
    d = current()
    check("ETA about 18 min", 17 <= d["dispatch"]["eta_s"] / 60 <= 19, f"{d['dispatch']['eta_s'] / 60:.1f} min")
    check("call for help", post(f"/incidents/{i}/call-helpers", {"radius_km": 5}) == 200)
    d = wait(lambda d: "accepted" in d["helpers"].values(), 20)
    helper = [int(k) for k, v in d["helpers"].items() if v == "accepted"][0]
    check("confirm helper", post(f"/incidents/{i}/confirm-helper", {"user_id": helper}) == 200)
    d = wait(lambda d: "on_scene" in d["helpers"].values(), 70, 3)
    check("helper first, ambulance still on the road", d["dispatch"]["state"] == "en_route", f"{d['dispatch']['eta_remaining_s'] / 60:.1f} min left")


def scenario_2():
    print("== 2: no one around")
    reset("2", auto=True)
    d = wait(lambda d: d["stage"] == "detected")
    check("nobody nearby, confidence high", d["nobody_around"] and d["confidence"] == "high")
    check("severity high", d["severity"]["shown"] == "high", f"{d['severity']['m3']['p']:.3f}")
    headers, _ = phone(0)
    notes = c.get("/api/v1/notifications", headers=headers).json()
    check("the driver has a check-in and an SOS card", sorted(n["kind"] for n in notes) == ["check", "sos"])
    ok = [n for n in notes if n["kind"] == "check"][0]
    c.post(f"/api/v1/notifications/{ok['id']}/respond", headers=headers, json={"action": "ok"})
    check("OK leaves confidence high", current()["confidence"] == "high")
    i = d["id"]
    post(f"/incidents/{i}/confirm", {"severity": "high"})
    post(f"/incidents/{i}/dispatch", {"ambulance_id": "amb-1"})
    check("nobody within 5 km", post(f"/incidents/{i}/call-helpers", {"radius_km": 5}) == 409)
    check("20 km works", post(f"/incidents/{i}/call-helpers", {"radius_km": 20}) == 200)
    d = wait(lambda d: "accepted" in d["helpers"].values(), 20)
    helper = [int(k) for k, v in d["helpers"].items() if v == "accepted"][0]
    post(f"/incidents/{i}/confirm-helper", {"user_id": helper})
    d = wait(lambda d: "on_scene" in d["helpers"].values(), 80, 3)
    check("helper arrives long before the ambulance", d["dispatch"]["eta_remaining_s"] > 600, f"{d['dispatch']['eta_remaining_s'] / 60:.1f} min left")


def scenario_3(turn_back):
    print(f"== 3: no network ({'driver turns back' if turn_back else 'driver goes in'})")
    reset("3", auto=True)
    d = wait(lambda d: d["kind"] == "dead_zone", 40, 1)
    i = d["id"]
    headers, _ = phone(0)
    check("warn", post(f"/incidents/{i}/warn", {"message": "dont_go"}) == 200)
    if turn_back:
        warning = [n for n in c.get("/api/v1/notifications", headers=headers).json() if n["kind"] == "dz_warning"][0]
        c.post(f"/api/v1/notifications/{warning['id']}/respond", headers=headers, json={"action": "turn_back"})
        time.sleep(2)
        check("the incident ends", get("/incidents") == [])
        return
    d = wait(lambda d: d["stage"] == "in_zone", 60)
    check("no warning reaches them in the zone", post(f"/incidents/{i}/warn", {"message": "no_help"}) == 409)
    d = wait(lambda d: d["stage"] == "overdue", 60)
    check("contact and Gendarmerie warned", any("Gendarmerie" in e["text"] for e in d["events"]))
    post(f"/incidents/{i}/call-helpers", {"radius_km": 20})
    post(f"/incidents/{i}/dispatch", {"ambulance_id": "amb-1"})
    d = wait(lambda d: d["dead_zone"]["found"], 90, 3)
    check("found: it was a crash", d["status"] == "Found: it was a crash")


def scenario_n():
    print("== N: hard braking, no crash (a quick false alarm)")
    reset("n")
    time.sleep(9)       # the trigger comes at 3 s and the model's answer about 1 s later
    check("no open incident: the model said no crash", get("/incidents") == [])
    asked = [n for i in range(6) for n in c.get("/api/v1/notifications", headers=phone(i)[0]).json()]
    check("nobody's phone was disturbed", asked == [], asked)


def sos():
    print("== SOS buttons")
    for kind in ("silent", "voice"):
        reset("2")
        d = wait(lambda d: d["stage"] == "detected")
        headers, _ = phone(0)
        card = [n for n in c.get("/api/v1/notifications", headers=headers).json() if n["kind"] == "sos"][0]
        check(f"{kind} SOS", c.post(f"/api/v1/notifications/{card['id']}/respond", headers=headers, json={"action": kind}).status_code == 200)
        d = current()
        check("crash verified", d["user_state"] == f"{kind}_sos" and d["severity"]["shown"] in ("high", "extreme"))


def dev_run():
    print("== /dev/run from a terminal")
    with c.stream("GET", "/dev/run/a", timeout=40) as r:
        lines = [line for line in r.iter_lines() if "PREDICTION" in line]
    check("one prediction, and it is a crash", len(lines) == 1 and "PREDICTION: crash" in lines[0], lines)


RUN = {"a": scenario_a, "b": scenario_b, "2": scenario_2, "3": lambda: (scenario_3(True), scenario_3(False)), "n": scenario_n, "sos": sos, "run": dev_run}

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    for name, fn in RUN.items():
        if which in ("all", name):
            fn()
    print("all checks passed")
