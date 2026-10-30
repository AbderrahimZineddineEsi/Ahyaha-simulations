"""
The people, ambulances and scenarios of the demo. Everything here is made up, apart from the two severity cases,
which are real rows from the NHTSA CISS data. Run `python server/tools/fetch_map.py routes` once to create the routes.
"""
import json
from pathlib import Path

ROUTES_FILE = Path(__file__).parent / "demo_routes.json"
if not ROUTES_FILE.exists():
    raise SystemExit("server/demo_routes.json is missing: run `python server/tools/fetch_map.py routes` once.")
PLACES = json.loads(ROUTES_FILE.read_text())  # where a crash can happen: "town" and "remote"

CHECKIN_S = 15         # how long user 0 has to answer "Are you OK?" (the spec says 30 s; shorter for a demo)
YELLOW_LINE_M = 300    # a driver this close to the ambulance is told to clear the way
WITNESS_RADIUS_M = 2500           # people this close are asked to confirm the crash
HELPER_RANGES_KM = (5, 20)        # the call for help, then the wider one the agent can choose
AUTO = {"witness_s": 18, "helper_s": 3}  # when the Auto switch answers: seconds after the crash is detected, and after the call for help

USERS = [
    {"id": 0, "name": "Karim Benali", "role": "In the accident", "age": 45, "male": 1, "driving": True,
     "profile": "Blood type O+. Hypertension. Takes no blood thinners.",
     "phone": "+213 000 00 00 00", "emergency_contact": "Samira Benali (wife), +213 000 00 00 10"},
    {"id": 1, "name": "Yacine Hamdi", "role": "Nearby on foot", "age": 28, "male": 1, "driving": False,
     "profile": "Walking 90 m from the crash.", "phone": "+213 000 00 00 01"},
    {"id": 2, "name": "Lina Ouali", "role": "Driving on the ambulance's road", "age": 33, "male": 0, "driving": True,
     "profile": "In her car, heading towards the crash.", "phone": "+213 000 00 00 02"},
    {"id": 3, "name": "Mourad Saadi", "role": "Driving on the ambulance's road", "age": 52, "male": 1, "driving": True,
     "profile": "Truck driver, on the same road.", "phone": "+213 000 00 00 03"},
    {"id": 4, "name": "Amel Kaci", "role": "Helper: nurse", "skill": "nurse", "age": 36, "male": 0, "driving": False, "helper": True,
     "profile": "Nurse at the hospital, first-aid certificate (demo data).", "phone": "+213 000 00 00 04"},
    {"id": 5, "name": "Redouane Meziane", "role": "Helper: Red Crescent volunteer", "skill": "Red Crescent volunteer", "age": 41, "male": 1, "driving": False,
     "helper": True, "profile": "Red Crescent first-aid volunteer, 6 years (demo data).", "phone": "+213 000 00 00 05"},
]

AMBULANCES = [
    {"id": "amb-1", "name": "Ambulance 1", "state": "available", "prep_s": 0, "parked": [0.0028, -0.0012]},
    {"id": "amb-2", "name": "Ambulance 2", "state": "available", "prep_s": 60, "parked": [-0.0028, -0.0012], "note": "Crew needs 1 min to get ready"},
    {"id": "amb-3", "name": "Ambulance 3", "state": "busy", "prep_s": 0, "parked": [0.0, 0.0009], "note": "On another mission"},
]

# The severity of each scenario: real people from the CISS crash database, run through the real M3 and M4.
# The blackbox recording is the same in both, so it can't tell the two apart: the demo case stands in for it.
SCENARIOS = {
    "a": {
        "title": "A: ambulance close, medium severity",
        "traffic": 1.0,   # the ambulance's drive takes as long as the road says
        "trip_s": 30,     # and is compressed to this many seconds on screen
        "helper_trip_s": 18,
        "place": "town",
        "case": {"dv_kmh": 24, "pdof_deg": 10, "rollover": 0, "multi_impact": 0, "body": "car"},
        "case_source": "CISS 2018 case 2018_1-13-2018-099-02_2_1: dv 24 km/h, frontal, one impact. The real person had a moderate injury (MAIS 2).",
        "expect": "medium",
    },
    "b": {
        "title": "B: ambulance far in traffic, high severity",
        "traffic": 2.5,   # heavy traffic
        "trip_s": 60,
        "helper_trip_s": 18,
        "place": "town",
        "case": {"dv_kmh": 67, "pdof_deg": 0, "rollover": 0, "multi_impact": 1, "body": "car"},
        "case_source": "CISS 2018 case 2018_1-20-2018-108-02_1_1: dv 67 km/h, frontal, several impacts. The real person had a serious injury (MAIS 3) and survived.",
        "expect": "extreme",
    },
    "2": {
        "title": "2: no one around, ambulance far",
        "traffic": 1.0,
        "trip_s": 60,
        "helper_trip_s": 25,
        "place": "remote",
        "case": {"dv_kmh": 50, "pdof_deg": 0, "rollover": 0, "multi_impact": 1, "body": "car"},
        "case_source": "CISS 2021 case 2021_1-73-2021-056-08_1_1: dv 50 km/h, frontal, several impacts. The real person had a serious injury (MAIS 3) and survived.",
        "expect": "high",
    },
    "3": {
        "title": "3: no network, no one around",
        "traffic": 1.0,
        "trip_s": 60,
        "helper_trip_s": 25,
        "place": "remote",
        "kind": "dead_zone",   # no crash signal can arrive: no severity case either
    },
}

# Scenario 3: the driver heads into a stretch of road with no network. Distances are back from the end of the road.
DEAD_ZONE = {
    "road": "RN 8B",
    "start_m": 13000, "entry_m": 6000,   # where the driver starts, and where the signal is lost
    "ask_s": 3,          # when the app asks about the return (the spec: about 15 min before the zone)
    "drive_s": 40,       # seconds on screen until the driver reaches the zone
    "crash_after_s": 8,  # in the zone, unseen by the server
    "overdue_after_s": 30,   # the promised return time, compressed
    "back": {"1h": ("In 1 hour", 60), "eta30": ("ETA + 30 min", 55), "long": ("Longer stay, 3 hours", 180)},
    "default": "eta30",  # with no answer, the app uses the route's ETA
}

PARKED_SPREAD = {"town": 1, "remote": 4}  # the remote map is more zoomed out: parked ambulances need more room to show apart

# Who people are at each place, where it differs from USERS
ROLES = {"remote": {1: {"role": "In Bouira, far from the crash", "profile": "At home in Bouira."}}}
