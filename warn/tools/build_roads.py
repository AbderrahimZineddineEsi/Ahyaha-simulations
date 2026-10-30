"""
One-off: builds warn/roads.json, the road data the phone gets from /api/road-ahead.

    python warn/tools/build_roads.py                 # all presets in warn/presets.json
    python warn/tools/build_roads.py --only night-road
    python warn/tools/build_roads.py --presets other.json --out other_roads.json --report

For each route: geometry and OSM node ids from OSRM, way tags from Overpass, places from Overpass (to know where
a town starts), elevation from Open-Meteo. Everything is turned into the French BAAC codes M1 was trained on
(MODEL_SPEC.md, "Inputs"), cut into ~200 m segments.

Blank OSM fields are filled with defaults by road type, never left blank: M1 calls a road with a blank speed limit
much safer (MODEL_SPEC.md, "Known problems"). The services are shared and free, so requests are slow and cached in
warn/tools/cache/.
"""
import argparse
import hashlib
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

WARN = Path(__file__).resolve().parents[1]
CACHE = WARN / "tools" / "cache"
UA = "SolveX-hackathon-demo/1.0 (offline copy for a demo)"
OSRM = "https://router.project-osrm.org"
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter"]   # tried in turn when one is overloaded
ELEVATION = "https://api.open-meteo.com/v1/elevation"
PAUSE_S = 1.2
OFFLINE = False           # --offline: use only what is already in the cache

SEGMENT_M = 200
EARTH_M = 6_371_000

# OSM highway class -> (BAAC catr, default speed limit outside town). catr: 1 motorway, 2 national, 3 departmental, 4 municipal
CLASSES = {
    "motorway": (1, 120), "motorway_link": (1, 60),
    "trunk": (2, 100), "trunk_link": (2, 60),
    "primary": (2, 80), "primary_link": (2, 60),
    "secondary": (3, 80), "secondary_link": (3, 60),
    "tertiary": (3, 80), "tertiary_link": (3, 60),
    "unclassified": (4, 70), "residential": (4, 50), "living_street": (4, 30), "service": (4, 30),
}
# Ways that count as a branch of a junction (not driveways, tracks, paths)
JUNCTION_CLASSES = set(CLASSES) - {"service", "living_street"}
TOWN_SPEED = 50          # speed limit in built-up areas in Algeria
TOWN_RADIUS_M = {"city": 2200, "town": 1300, "suburb": 700, "village": 450, "hamlet": 250}
SLOPE = 0.04             # grade that counts as "slope" (prof 2)
CREST_M = 4              # metres above/below both ends that count as hilltop/bottom (prof 3/4)
CURVE_DEG = 15           # net turn over a segment that counts as a curve
S_BEND_DEG = 20          # turning both ways by this much makes an S-bend


def haversine_m(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * EARTH_M * math.asin(math.sqrt(h))


def bearing(a, b):
    la1, la2 = math.radians(a[0]), math.radians(b[0])
    dlon = math.radians(b[1] - a[1])
    y = math.sin(dlon) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dlon)
    return math.degrees(math.atan2(y, x))


def angle_diff(a, b):
    return (b - a + 540) % 360 - 180


class Line:
    """A polyline with distances along it. Points are [lat, lon]."""

    def __init__(self, points):
        self.points = points
        self.cum = [0.0]
        for a, b in zip(points, points[1:]):
            self.cum.append(self.cum[-1] + haversine_m(a, b))
        self.length = self.cum[-1]

    def at(self, d):
        d = min(max(d, 0), self.length)
        lo, hi = 0, len(self.cum) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self.cum[mid] <= d:
                lo = mid
            else:
                hi = mid
        seg = self.cum[hi] - self.cum[lo]
        u = 0 if seg == 0 else (d - self.cum[lo]) / seg
        a, b = self.points[lo], self.points[hi]
        return [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u]

    def edge_at(self, d):
        """Index of the edge (points[i], points[i+1]) that contains distance d."""
        lo, hi = 0, len(self.cum) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self.cum[mid] <= d:
                lo = mid
            else:
                hi = mid
        return lo


def fetch(url, data=None, name=None, attempts=8):
    urls = url if isinstance(url, list) else [url]
    CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1((urls[0] + (data or "")).encode()).hexdigest()[:20]
    path = CACHE / f"{name or 'req'}-{key}.json"
    if path.exists():
        return json.loads(path.read_text())
    if OFFLINE and name.endswith(("ways", "places")):
        raise RuntimeError("offline: not in the cache")
    for attempt in range(attempts):
        url = urls[attempt % len(urls)]
        try:
            request = urllib.request.Request(url, data=data.encode() if data else None, headers={"User-Agent": UA})
            body = urllib.request.urlopen(request, timeout=120).read()
            result = json.loads(body)
            if name in ("ways", "places") and not result.get("elements"):
                raise ValueError(f"empty answer {result.get('remark', '')}")   # an overloaded server answers with nothing
            break
        except Exception as error:   # shared services drop requests now and then
            print(f"  retry {attempt + 1} after: {error}", file=sys.stderr)
            time.sleep(4 + 3 * attempt)
    else:
        raise RuntimeError(f"giving up on {url}")
    time.sleep(PAUSE_S)
    path.write_text(json.dumps(result))
    return result


def osrm_route(points):
    coords = ";".join(f"{lon},{lat}" for lat, lon in points)
    url = f"{OSRM}/route/v1/driving/{coords}?overview=full&geometries=geojson&annotations=nodes&continue_straight=true"
    result = fetch(url, name="osrm")
    if result.get("code") != "Ok":
        sys.exit(f"OSRM failed for {points}: {result.get('code')}")
    route = result["routes"][0]
    nodes = []
    for leg in route["legs"]:
        for n in leg["annotation"]["nodes"]:
            if not nodes or nodes[-1] != n:
                nodes.append(n)
    return {"nodes": nodes, "geometry": [[lat, lon] for lon, lat in route["geometry"]["coordinates"]]}


def ways_query(points, radius=30):
    # Thin the line to about one point per 60 m so the query stays short
    thin, last = [points[0]], points[0]
    for p in points[1:-1]:
        if haversine_m(last, p) >= 60:
            thin.append(p)
            last = p
    thin.append(points[-1])
    line = ",".join(f"{p[0]:.5f},{p[1]:.5f}" for p in thin)
    classes = "|".join(CLASSES)
    return f'[out:json][timeout:120];way["highway"~"^({classes})$"](around:{radius},{line});out body geom;'


def cached_union(name):
    """Everything saved in earlier answers of one kind, merged: other routes along the same roads cover this one too."""
    found = {}
    for path in CACHE.glob(f"{name}-*.json"):
        for e in json.loads(path.read_text()).get("elements", []):
            found[(e["type"], e["id"])] = e
    return list(found.values())


def overpass_ways(geometry, nodes, chunk_m=4000):
    """The OSM ways along a route. One query for the whole route. If the servers refuse: ways saved from earlier routes,
    when they cover the route's nodes; otherwise one query per ~4 km."""
    try:
        result = fetch(OVERPASS, data=urllib.parse.urlencode({"data": ways_query(geometry)}), name="ways", attempts=3)
        return {w["id"]: w for w in result["elements"] if w["type"] == "way"}
    except RuntimeError:
        print("  whole-route query refused", file=sys.stderr)
    saved = {e["id"]: e for e in cached_union("ways") if e["type"] == "way"}
    have = {n for w in saved.values() for n in w["nodes"]}
    covered = sum(n in have for n in nodes) / len(nodes)
    if covered >= 0.98:
        print(f"  using OSM ways saved from earlier routes ({covered:.1%} of the route's nodes)", file=sys.stderr)
        return saved
    print(f"  saved ways cover only {covered:.1%}: asking in pieces", file=sys.stderr)
    ways, piece, length = {}, [geometry[0]], 0.0
    pieces = []
    for a, b in zip(geometry, geometry[1:]):
        length += haversine_m(a, b)
        piece.append(b)
        if length >= chunk_m:
            pieces.append(piece)
            piece, length = [b], 0.0
    if len(piece) > 1:
        pieces.append(piece)
    for piece in pieces:
        result = fetch(OVERPASS, data=urllib.parse.urlencode({"data": ways_query(piece)}), name="ways")
        ways.update({w["id"]: w for w in result["elements"] if w["type"] == "way"})
    return ways


def overpass_places(lines):
    lats = [p[0] for line in lines for p in line]
    lons = [p[1] for line in lines for p in line]
    box = f"{min(lats) - .03:.3f},{min(lons) - .03:.3f},{max(lats) + .03:.3f},{max(lons) + .03:.3f}"
    query = f'[out:json][timeout:60];node["place"~"^(city|town|village|suburb|hamlet)$"]({box});out body;'
    try:
        elements = fetch(OVERPASS, data=urllib.parse.urlencode({"data": query}), name="places", attempts=3)["elements"]
    except RuntimeError:
        print("  places query refused: using places saved from earlier routes", file=sys.stderr)
        elements = [e for e in cached_union("places") if e["type"] == "node"]
    return [(e["lat"], e["lon"], TOWN_RADIUS_M[e["tags"]["place"]], e["tags"].get("name:fr") or e["tags"].get("name", "")) for e in elements]


def elevations(points):
    out = []
    for i in range(0, len(points), 100):
        chunk = points[i:i + 100]
        url = f"{ELEVATION}?latitude={','.join(f'{p[0]:.5f}' for p in chunk)}&longitude={','.join(f'{p[1]:.5f}' for p in chunk)}"
        out += fetch(url, name="elev")["elevation"]
    return out


def parse_speed(text):
    if not text:
        return None
    digits = "".join(c for c in str(text).split()[0] if c.isdigit())
    return int(digits) if digits else None


def parse_int(text):
    try:
        return int(str(text).split(";")[0])
    except (TypeError, ValueError):
        return None


class Network:
    """The OSM ways along a route, indexed to answer: which way is this edge, and is this node a junction?"""

    def __init__(self, ways):
        self.coords = {}
        self.edge_way = {}
        self.node_branches = {}
        self.node_roundabout = set()
        for wid, way in ways.items():
            for nid, g in zip(way["nodes"], way["geometry"]):
                self.coords[nid] = [g["lat"], g["lon"]]
            for u, v in zip(way["nodes"], way["nodes"][1:]):
                self.edge_way.setdefault((u, v), way)
                self.edge_way.setdefault((v, u), way)
            if way["tags"]["highway"] not in JUNCTION_CLASSES:
                continue
            ring = way["tags"].get("junction") in ("roundabout", "circular")
            for i, nid in enumerate(way["nodes"]):
                end = i == 0 or i == len(way["nodes"]) - 1
                self.node_branches[nid] = self.node_branches.get(nid, 0) + (1 if end and way["nodes"][0] != way["nodes"][-1] else 2)
                if ring:
                    self.node_roundabout.add(nid)

    def junction(self, nid):
        """BAAC int code at a node: 1 none, 2 X, 3 T, 5 more than four branches, 6 roundabout."""
        n = self.node_branches.get(nid, 0)
        if n < 3:
            return 1
        if nid in self.node_roundabout:
            return 6
        return {3: 3, 4: 2}.get(n, 5)


def road_tags(way):
    """BAAC fields from one OSM way, with defaults for what OSM leaves blank."""
    tags = way["tags"] if way else {}
    highway = tags.get("highway", "unclassified")
    catr, default_speed = CLASSES.get(highway, (4, 50))
    ref = tags.get("ref", "")
    if highway in ("primary", "trunk") and ref.startswith("A"):
        catr = 1
    oneway = tags.get("oneway") in ("yes", "true", "1") or tags.get("junction") in ("roundabout", "circular")
    if catr == 1:
        circ = 3
    elif oneway and highway in ("trunk", "primary", "secondary", "trunk_link", "primary_link"):
        circ = 3
    elif oneway:
        circ = 1
    else:
        circ = 2
    # nbv is OSM `lanes` as tagged (MODEL_SPEC.md); 2 when OSM leaves it blank
    lanes = parse_int(tags.get("lanes")) or 2
    if tags.get("tunnel") in ("yes", "building_passage"):
        infra = 1
    elif tags.get("bridge") in ("yes", "viaduct"):
        infra = 2
    else:
        infra = 0
    return {"highway": highway, "catr": catr, "default_speed": default_speed, "maxspeed": parse_speed(tags.get("maxspeed")),
            "circ": circ, "nbv": lanes, "infra": infra, "name": tags.get("ref") or tags.get("name:fr") or tags.get("name") or ""}


def resample(line, step=20):
    n = max(2, int(line.length // step))
    return [line.at(line.length * i / n) for i in range(n + 1)]


def turn_profile(line, a, b):
    """Signed degrees turned between chords of ~20 m inside [a, b] (positive: to the right)."""
    pts = [line.at(a + (b - a) * i / max(2, int((b - a) // 20))) for i in range(max(2, int((b - a) // 20)) + 1)]
    headings = [bearing(p, q) for p, q in zip(pts, pts[1:])]
    turns = [angle_diff(h1, h2) for h1, h2 in zip(headings, headings[1:])]
    # Smooth over 3 chords: GPS-like zigzag on a straight road shouldn't count
    smooth = [sum(turns[max(0, i - 1):i + 2]) / len(turns[max(0, i - 1):i + 2]) for i in range(len(turns))]
    return smooth


def plan_code(turns):
    pos = sum(t for t in turns if t > 0)
    neg = -sum(t for t in turns if t < 0)
    if pos >= S_BEND_DEG and neg >= S_BEND_DEG:
        return 4
    net = pos - neg
    if net >= CURVE_DEG:
        return 3
    if net <= -CURVE_DEG:
        return 2
    return 1


def build_route(points, places):
    route = osrm_route(points)
    ways = overpass_ways(route["geometry"], route["nodes"])
    net = Network(ways)
    known = [n for n in route["nodes"] if n in net.coords]
    if len(known) < 0.9 * len(route["nodes"]):
        sys.exit(f"only {len(known)} of {len(route['nodes'])} route nodes found in OSM ways")
    nodes = known
    path = [[round(net.coords[n][0], 5), round(net.coords[n][1], 5)] for n in nodes]
    line = Line(path)

    # elevation every ~100 m, lightly smoothed
    n_samples = max(2, int(line.length // 100))
    sample_d = [line.length * i / n_samples for i in range(n_samples + 1)]
    raw = elevations([line.at(d) for d in sample_d])
    elev = [sum(raw[max(0, i - 1):i + 2]) / len(raw[max(0, i - 1):i + 2]) for i in range(len(raw))]

    def elevation_at(d):
        i = min(len(sample_d) - 2, max(0, int(d / (line.length / n_samples))))
        u = (d - sample_d[i]) / (sample_d[i + 1] - sample_d[i])
        return elev[i] + (elev[i + 1] - elev[i]) * u

    count = max(1, round(line.length / SEGMENT_M))
    size = line.length / count
    segments = []
    for k in range(count):
        a, b = k * size, (k + 1) * size
        mid = (a + b) / 2
        i0, i1 = line.edge_at(a + .01), line.edge_at(b - .01)
        edges = range(i0, i1 + 1)
        ways_here = [net.edge_way.get((nodes[i], nodes[i + 1])) for i in edges]
        mid_way = net.edge_way.get((nodes[line.edge_at(mid)], nodes[line.edge_at(mid) + 1]))
        tags = road_tags(mid_way)
        centre = line.at(mid)
        town = None
        if tags["catr"] != 1:    # a motorway next to a town is not a built-up road
            town = next((name or "town" for lat, lon, radius, name in places if haversine_m(centre, (lat, lon)) <= radius), None)
        agg = 2 if town else 1
        speed = tags["maxspeed"] or (min(tags["default_speed"], TOWN_SPEED) if agg == 2 else tags["default_speed"])
        junction = 1
        if tags["catr"] != 1:    # motorway interchanges are slip roads, not junctions
            inside = [i for i in range(i0, min(i1 + 2, len(nodes))) if a <= line.cum[i] < b or (k == count - 1 and line.cum[i] <= b)]
            codes = [net.junction(nodes[i]) for i in inside]
            for wanted in (6, 2, 3, 5):
                if wanted in codes:
                    junction = wanted
                    break
        infra = max((road_tags(w)["infra"] for w in ways_here if w), default=0)
        e_a, e_m, e_b = elevation_at(a), elevation_at(mid), elevation_at(b)
        grade = (e_b - e_a) / size
        if abs(grade) >= SLOPE:
            prof = 2
        elif e_m - (e_a + e_b) / 2 >= CREST_M:
            prof = 3
        elif e_m - (e_a + e_b) / 2 <= -CREST_M:
            prof = 4
        else:
            prof = 1
        segments.append({
            "a": round(a), "b": round(b), "road": tags["name"], "town": town or "",
            "catr": tags["catr"], "plan": plan_code(turn_profile(line, a, b)), "vma": speed, "int": junction, "agg": agg,
            "circ": tags["circ"], "nbv": tags["nbv"], "prof": prof, "infra": infra,
            "bend": round(sum(abs(t) for t in turn_profile(line, a, b))),
            "grade": round(grade * 100, 1), "explicit_speed": tags["maxspeed"] is not None,
        })
    return {"path": path, "length": round(line.length), "nodes": nodes, "segments": segments}


def common_prefix_m(main, alt):
    """Distance along `main` where the alternative leaves it, from the OSM nodes both routes share at the start."""
    k = 0
    while k < min(len(main["nodes"]), len(alt["nodes"])) and main["nodes"][k] == alt["nodes"][k]:
        k += 1
    if k < 2:
        return 0
    return Line(main["path"][:k]).length


def score(route, clock):
    """M1's x-vs-average for every segment at a clock (Python model, for choosing presets)."""
    import lightgbm as lgb
    import pandas as pd
    config = json.loads((WARN.parent / "m1" / "models" / "risk_model_nohist.json").read_text())
    booster = lgb.Booster(model_file=str(WARN.parent / "m1" / "models" / "risk_model_nohist.txt"))
    hour, wd = clock["hour"], clock["weekday"]
    rows = [{**{k: s[k] for k in ("catr", "plan", "vma", "int", "agg", "circ", "nbv", "prof", "infra")},
             "hour": hour, "weekday": wd, "month": clock["month"], "is_weekend": int(wd >= 5),
             "is_dark": int(hour < 7 or hour >= 18), "rain_mm": 0, "rain_3h_mm": 0, "is_rain": 0, "is_fog": 0,
             "is_snow": 0, "is_storm": 0} for s in route["segments"]]
    frame = pd.DataFrame(rows)[config["features"]].astype(float)
    for col, cats in zip(config["categorical"], booster.pandas_categorical):
        frame[col] = pd.Categorical(frame[col], categories=cats)
    p = booster.predict(frame)
    return (p / (1 - p)) / 0.25


def report(name, route, clock, summary=False):
    x = score(route, clock)
    print(f"\n== {name}: {route['length'] / 1000:.1f} km, {len(route['segments'])} segments, clock {clock}")
    group = lambda v: "VERY HIGH" if v >= 3 else "HIGH" if v >= 1.5 else "medium" if v >= .7 else "low"
    if summary:
        runs = []
        for s, v in zip(route["segments"], x):
            if runs and runs[-1]["g"] == group(v):
                r = runs[-1]
                r["b"] = s["b"]; r["max"] = max(r["max"], v); r["roads"].add(s["road"] or "?"); r["towns"].add(s["town"] or "-")
                r["ints"] += s["int"] > 1; r["curves"] += s["plan"] > 1
            else:
                runs.append({"g": group(v), "a": s["a"], "b": s["b"], "max": v, "roads": {s["road"] or "?"}, "towns": {s["town"] or "-"},
                             "ints": int(s["int"] > 1), "curves": int(s["plan"] > 1)})
        for r in runs:
            at = Line(route["path"]).at(r["a"])
            print(f"  {r['a'] / 1000:6.2f}-{r['b'] / 1000:6.2f} km ({at[0]:.4f},{at[1]:.4f}) {(r['b'] - r['a']) / 1000:5.2f} {r['g']:9} max {r['max']:5.2f} "
                  f"junctions {r['ints']} curves {r['curves']}  {','.join(sorted(r['roads']))[:40]} | {','.join(sorted(r['towns']))[:40]}")
        return
    for s, v in zip(route["segments"], x):
        lvl = "V" if v >= 3 else "H" if v >= 1.5 else "m" if v >= .7 else "."
        print(f"{s['a'] / 1000:6.2f} {s['road'][:14]:14} {s['town'][:12]:12} catr{s['catr']} v{s['vma']:<3}{'*' if s['explicit_speed'] else ' '} plan{s['plan']} int{s['int']} circ{s['circ']} nbv{s['nbv']} prof{s['prof']} infra{s['infra']} {s['grade']:5.1f}%  {v:5.2f} {lvl}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--presets", default=str(WARN / "presets.json"))
    ap.add_argument("--out", default=str(WARN / "roads.json"))
    ap.add_argument("--only", help="build just this preset id")
    ap.add_argument("--offline", action="store_true", help="ask Overpass for nothing: use the OSM data already in the cache")
    ap.add_argument("--report", action="store_true", help="print each segment with M1's score at the preset's clock")
    ap.add_argument("--summary", action="store_true", help="with --report: print runs of equal level instead of every segment")
    args = ap.parse_args()
    global OFFLINE
    OFFLINE = args.offline

    presets = json.loads(Path(args.presets).read_text())["presets"]
    if args.only:
        presets = [p for p in presets if p["id"] == args.only]
    out_path = Path(args.out)
    out = json.loads(out_path.read_text()) if out_path.exists() else {}
    for preset in presets:
        print(f"building {preset['id']} ...", flush=True)
        # one geometry pass first, so the town lookup covers both routes
        probe = [osrm_route(preset["route"])["geometry"]] + ([osrm_route(preset["alt"])["geometry"]] if preset.get("alt") else [])
        places = overpass_places(probe)
        main_route = build_route(preset["route"], places)
        alt_route = build_route(preset["alt"], places) if preset.get("alt") else None
        entry = {"main": {k: v for k, v in main_route.items() if k != "nodes"}}
        if alt_route:
            entry["alt"] = {k: v for k, v in alt_route.items() if k != "nodes"}
            entry["branch_m"] = round(common_prefix_m(main_route, alt_route))
        out[preset["id"]] = entry
        out_path.write_text(json.dumps(out, separators=(",", ":")))   # saved as we go: the services are flaky
        if args.report:
            report(preset["id"] + " (main)", main_route, preset["clock"], args.summary)
            if alt_route:
                print(f"alternative leaves the main route at {entry['branch_m']} m")
                report(preset["id"] + " (alt)", alt_route, preset["clock"], args.summary)
    out_path.write_text(json.dumps(out, separators=(",", ":")))
    print(f"wrote {out_path} ({out_path.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
