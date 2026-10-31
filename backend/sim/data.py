"""
The people, ambulances and scenarios of the demo. Everything here is made up, apart from the severity cases, which are
real rows from the NHTSA CISS data. Run `python -m backend.sim.tools.fetch_map routes` once to create the routes.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROUTES_FILE = HERE / "data" / "demo_routes.json"
PLACES = json.loads(ROUTES_FILE.read_text())  # where a crash can happen: "town" and "remote"

CHECKIN_S = 10         # how long user 0 has to answer "Are you OK?" (the spec says 30 s; shorter for a demo)
AUTO = {"witness_s": 12, "helper_s": 2}  # when the Auto switch answers: seconds after the alert, and after the call for help
# The blackbox replay is sped up so the demo fits a presentation. M2 counts packets, not seconds, so it reads the same 16 s:
# the first skip_s packets go in a burst, up to the impact at real speed (the audience sees the approach), the 8 s after it at after_x.
REPLAY = {"skip_s": 6, "after_x": 6}
DEMO_PASSWORD = "demo"
AGENT = {"phone": "+213000000100", "name": "Agent Protection Civile", "role": "dispatcher"}
STAFF = [
    {"phone": "+213000000101", "name": "Hopital de Bouira (staff)", "role": "hospital", "hospital": "Bouira"},
    {"phone": "+213000000102", "name": "CHU Mustapha (staff)", "role": "hospital", "hospital": "Alger"},
    {"phone": "+213000000103", "name": "Admin", "role": "admin"},
]
HOSPITALS = [
    {"key": "Bouira", "name": "Hopital Mohamed Boudiaf, Bouira", "lat": 36.3710, "lon": 3.9020, "beds_total": 40, "beds_free": 6,
     "specialties": ["trauma", "orthopedics", "thoracic"]},
    {"key": "Alger", "name": "CHU Mustapha, Alger", "lat": 36.7640, "lon": 3.0560, "beds_total": 120, "beds_free": 14,
     "specialties": ["trauma", "neurosurgery", "thoracic", "orthopedics", "maxillofacial"]},
]

USERS = [
    {"id": 0, "name": "Karim Benali", "role": "In the accident", "age": 45, "sex": "male", "driving": True,
     "blood_type": "O+", "conditions": "Hypertension. Takes no blood thinners.", "phone": "+213000000000",
     "emergency_contact_name": "Samira Benali (wife)", "emergency_contact_phone": "+213 000 00 00 10"},
    {"id": 1, "name": "Yacine Hamdi", "role": "Nearby on foot", "age": 28, "sex": "male", "driving": False, "phone": "+213000000001"},
    {"id": 2, "name": "Lina Ouali", "role": "Driving on the ambulance's road", "age": 33, "sex": "female", "driving": True, "phone": "+213000000002"},
    {"id": 3, "name": "Mourad Saadi", "role": "Driving on the ambulance's road", "age": 52, "sex": "male", "driving": True, "phone": "+213000000003"},
    {"id": 4, "name": "Amel Kaci", "role": "Helper: nurse", "skills": "nurse", "age": 36, "sex": "female", "driving": False, "is_helper": True,
     "phone": "+213000000004"},
    {"id": 5, "name": "Redouane Meziane", "role": "Helper: Red Crescent volunteer", "skills": "Red Crescent volunteer", "age": 41, "sex": "male",
     "driving": False, "is_helper": True, "phone": "+213000000005"},
]
ROLES = {"remote": {1: "In Bouira, far from the crash"}}   # who people are at each place, where it differs from USERS

AMBULANCES = [
    {"id": "amb-1", "name": "Ambulance 1", "state": "available", "prep_s": 0, "parked": [0.0028, -0.0012]},
    {"id": "amb-2", "name": "Ambulance 2", "state": "available", "prep_s": 60, "parked": [-0.0028, -0.0012], "note": "Crew needs 1 min to get ready"},
    {"id": "amb-3", "name": "Ambulance 3", "state": "busy", "prep_s": 0, "parked": [0.0, 0.0009], "note": "On another mission"},
]
PARKED_SPREAD = {"town": 1, "remote": 4}  # the remote map is more zoomed out: parked ambulances need more room to show apart

# The severity of each scenario: real people from the CISS crash database, run through the real M3 and M4.
# The blackbox recording is the same in both, so it can't tell the two apart: the demo case stands in for it.
SCENARIOS = {
    "a": {
        "title": "A: ambulance close, medium severity",
        "traffic": 1.0,   # the ambulance's drive takes as long as the road says
        "trip_s": 15,     # and is compressed to this many seconds on screen
        "helper_trip_s": 10,
        "place": "town",
        "case": {"dv_kmh": 24, "pdof_deg": 10, "rollover": 0, "multi_impact": 0, "body": "car"},
        "case_source": "CISS 2018 case 2018_1-13-2018-099-02_2_1: dv 24 km/h, frontal, one impact. The real person had a moderate injury (MAIS 2).",
        "expect": "medium",
    },
    "b": {
        "title": "B: ambulance far in traffic, high severity",
        "traffic": 2.5,   # heavy traffic
        "trip_s": 25,
        "helper_trip_s": 10,
        "place": "town",
        "case": {"dv_kmh": 67, "pdof_deg": 0, "rollover": 0, "multi_impact": 1, "body": "car"},
        "case_source": "CISS 2018 case 2018_1-20-2018-108-02_1_1: dv 67 km/h, frontal, several impacts. The real person had a serious injury (MAIS 3) and survived.",
        "expect": "extreme",
    },
    "2": {
        "title": "2: no one around, ambulance far",
        "traffic": 1.0,
        "trip_s": 25,
        "helper_trip_s": 12,
        "place": "remote",
        "case": {"dv_kmh": 50, "pdof_deg": 0, "rollover": 0, "multi_impact": 1, "body": "car"},
        "case_source": "CISS 2021 case 2021_1-73-2021-056-08_1_1: dv 50 km/h, frontal, several impacts. The real person had a serious injury (MAIS 3) and survived.",
        "expect": "high",
    },
    "n": {
        "title": "N: hard braking, no crash",
        "traffic": 1.0,
        "trip_s": 15,
        "helper_trip_s": 10,
        "place": "town",
        "kind": "no_crash",    # the trigger fires, M2 says no crash: nobody is disturbed
        "recording": "braking",
    },
    "3": {
        "title": "3: no network, no one around",
        "traffic": 1.0,
        "trip_s": 25,
        "helper_trip_s": 12,
        "place": "remote",
        "kind": "dead_zone",   # no crash signal can arrive: no severity case either
    },
}

# Scenario 3: the driver heads into a stretch of road with no network. Distances are back from the end of the road.
DEAD_ZONE = {
    "road": "RN 8B",
    "start_m": 13000, "entry_m": 6000,   # where the driver starts, and where the signal is lost
    "ask_s": 1,          # when the app asks about the return (the spec: about 15 min before the zone)
    "answer_s": 5,       # the time the driver has to say when they will be back
    "drive_s": 18,       # seconds on screen until the driver reaches the zone
    "crash_after_s": 3,  # in the zone, unseen by the server
    "overdue_after_s": 10,   # the promised return time, compressed
    "back": {"1h": ("In 1 hour", 60), "eta30": ("ETA + 30 min", 55), "long": ("Longer stay, 3 hours", 180)},
    "default": "eta30",  # with no answer, the app uses the route's ETA
    "silence_s": 3,      # a driver silent this long inside the zone counts as being in it
}
