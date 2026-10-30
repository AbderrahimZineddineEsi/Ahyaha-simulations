"""
One-off: downloads what the demo map needs, so the demo works with no internet.

    python server/tools/fetch_map.py routes     # road routes from the OSRM demo server -> server/demo_routes.json
    python server/tools/fetch_map.py leaflet    # the Leaflet library -> server/static/vendor/leaflet/
    python server/tools/fetch_map.py roads      # the streets around each place, from OpenStreetMap -> server/static/roads_*.json

The map is drawn from the street data, not from downloaded map pictures: OpenStreetMap's tile servers forbid
downloading tiles for offline use. The street data is one request to the Overpass API. The map must show
"© OpenStreetMap contributors" (dashboard.html does). The data is ODbL.
"""
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER))
from world import Path as RoadPath, haversine_m  # noqa: E402

UA = "SolveX-hackathon-demo/1.0 (offline copy for a demo)"
OSRM = "https://router.project-osrm.org"
OVERPASS = "https://overpass-api.de/api/interpreter"
LEAFLET = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4"

CENTRE = (36.3800, 3.8600)       # where the demo's ambulances wait: a made-up position, not a real station
# Each place: where the crash is, and where people are. Distances are along the ambulance's way, back from the crash.
PLACES = {
    "town": {"crash": (36.3741, 3.9012), "on_route_m": {1: 90, 2: 1100, 3: 2300},
             "helpers": {4: (36.3700, 3.9150), 5: (36.3790, 3.8950)}, "classes": "motorway|trunk|primary|secondary|tertiary|unclassified|residential"},
    "remote": {"crash": (36.2200, 3.8300), "on_route_m": {1: 21000, 2: 16000, 3: 12000},  # RN 8B, south of Bouira
               "helpers": {4: 9500, 5: (36.1500, 3.6900)}, "classes": "motorway|trunk|primary|secondary|tertiary"},
}
PAUSE_S = 1.1


def get(url):
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def route(a, b):
    url = f"{OSRM}/route/v1/driving/{a[1]},{a[0]};{b[1]},{b[0]}?overview=full&geometries=geojson"
    r = json.loads(get(url))["routes"][0]
    time.sleep(PAUSE_S)
    return {"geometry": [[lat, lon] for lon, lat in r["geometry"]["coordinates"]],
            "distance_m": round(r["distance"]), "duration_s": round(r["duration"])}


def fetch_place(place):
    ambulance = route(CENTRE, place["crash"])
    crash = ambulance["geometry"][-1]
    road = RoadPath(ambulance["geometry"])
    helpers = {}
    for uid, start in place["helpers"].items():  # a number is a distance back along the ambulance's way
        helpers[str(uid)] = route(road.from_end(start) if isinstance(start, int) else start, crash)
    users = {"0": crash, **{str(uid): road.from_end(d) for uid, d in place["on_route_m"].items()}}
    users.update({uid: h["geometry"][0] for uid, h in helpers.items()})
    return {"crash": crash, "centre": ambulance["geometry"][0], "ambulance": ambulance, "helpers": helpers, "users": users}


def fetch_routes():
    out = {name: fetch_place(place) for name, place in PLACES.items()}
    (SERVER / "demo_routes.json").write_text(json.dumps(out, separators=(",", ":")))
    for name, data in out.items():
        crash = data["crash"]
        print(name, f"ambulance {data['ambulance']['distance_m']} m, {data['ambulance']['duration_s']} s;",
              "helpers", {k: (v["distance_m"], v["duration_s"]) for k, v in data["helpers"].items()})
        print("   km from the crash:", {k: round(haversine_m(v, crash) / 1000, 1) for k, v in data["users"].items()})


def fetch_roads():
    routes = json.loads((SERVER / "demo_routes.json").read_text())
    for name, place in PLACES.items():
        out = SERVER / "static" / f"roads_{name}.json"
        if out.exists():
            continue
        data = routes[name]
        points = list(data["ambulance"]["geometry"]) + [p for h in data["helpers"].values() for p in h["geometry"]]
        pad = 0.008
        s, n = min(p[0] for p in points) - pad, max(p[0] for p in points) + pad
        w, e = min(p[1] for p in points) - pad, max(p[1] for p in points) + pad
        query = f'[out:json][timeout:120];way["highway"~"^({place["classes"]})$"]({s:.5f},{w:.5f},{n:.5f},{e:.5f});out geom;'
        body = urllib.parse.urlencode({"data": query}).encode()
        for attempt in range(1, 7):
            try:
                request = urllib.request.Request(OVERPASS, data=body, headers={"User-Agent": UA})
                with urllib.request.urlopen(request, timeout=150) as response:
                    elements = json.loads(response.read())["elements"]
                break
            except (OSError, ValueError) as err:
                if attempt == 6:
                    sys.exit(f"Giving up on {name} ({err}). Run the command again later.")
                print(f"{err}; retrying in {attempt * 10} s", flush=True)
                time.sleep(attempt * 10)
        roads = [{"c": el["tags"]["highway"], "p": [[round(g["lat"], 5), round(g["lon"], 5)] for g in el["geometry"]]}
                 for el in elements if el.get("geometry")]
        out.write_text(json.dumps(roads, separators=(",", ":")))
        print(f"{name}: {len(roads)} streets, {out.stat().st_size // 1024} kB", flush=True)
        time.sleep(PAUSE_S)


def fetch_leaflet():
    folder = SERVER / "static" / "vendor" / "leaflet"
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("leaflet.js", "leaflet.css"):
        (folder / name).write_bytes(get(f"{LEAFLET}/{name}"))
    print("saved Leaflet 1.9.4 to", folder)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["routes", "leaflet", "roads"])
    args = ap.parse_args()
    {"routes": fetch_routes, "leaflet": fetch_leaflet, "roads": fetch_roads}[args.what]()
