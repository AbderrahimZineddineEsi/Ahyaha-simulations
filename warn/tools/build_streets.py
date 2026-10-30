"""
One-off: saves the streets and place names around the routes into warn/static/streets.json, so the map works with no internet.

    python warn/tools/build_streets.py             # needs warn/roads.json first (the area is taken from the routes)
    python warn/tools/build_streets.py --offline   # no Overpass: only the roads saved along the routes

The map is drawn from this OpenStreetMap *data* (a handful of Overpass requests), not from map tiles: the OpenStreetMap
tile servers forbid downloading tiles for offline use. The page shows "© OpenStreetMap contributors".
"""
import json
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_roads  # noqa: E402
from build_roads import OVERPASS, WARN, cached_union, fetch, haversine_m  # noqa: E402

PAD_DEG = 0.025            # about 2.5 km around the routes
CLASS = {                   # drawn width/colour is chosen by the page from this class
    "motorway": 0, "motorway_link": 0,
    "trunk": 1, "trunk_link": 1, "primary": 1, "primary_link": 1,
    "secondary": 2, "secondary_link": 2, "tertiary": 2, "tertiary_link": 2,
    "unclassified": 3, "residential": 3,
}
MIN_STEP_M = 4              # drop points closer than this to the previous one
PLACES = {"city": 0, "town": 1, "village": 2}


def thin(points):
    out = [points[0]]
    for p in points[1:-1]:
        if haversine_m(out[-1], p) >= MIN_STEP_M:
            out.append(p)
    out.append(points[-1])
    return out


def query(kind, box):
    south, west, north, east = box
    b = f"{south:.4f},{west:.4f},{north:.4f},{east:.4f}"
    if kind == "ways":
        classes = "|".join(CLASS)
        q = f'[out:json][timeout:120];way["highway"~"^({classes})$"]({b});out geom;'
    else:
        q = f'[out:json][timeout:60];node["place"~"^(city|town|village)$"]({b});out;'
    try:
        return fetch(OVERPASS, data=urllib.parse.urlencode({"data": q}), name=f"streets-{kind}", attempts=3)["elements"]
    except RuntimeError:
        return None


def main():
    build_roads.OFFLINE = "--offline" in sys.argv   # ask Overpass for nothing: use the OSM ways already saved
    roads = json.loads((WARN / "roads.json").read_text())
    ways, places = {}, {}
    degraded = False
    for pid, route in roads.items():
        pts = [p for leg in ("main", "alt") if leg in route for p in route[leg]["path"]]
        lats, lons = [p[0] for p in pts], [p[1] for p in pts]
        box = (min(lats) - PAD_DEG, min(lons) - PAD_DEG, max(lats) + PAD_DEG, max(lons) + PAD_DEG)
        print(f"{pid}: area {box}", flush=True)
        found = query("ways", box)
        if found is None:
            degraded = True
            found = [e for e in cached_union("ways") if e["type"] == "way"]
        for w in found:
            if w["tags"]["highway"] in CLASS:
                ways[w["id"]] = [CLASS[w["tags"]["highway"]], [[round(g["lat"], 5), round(g["lon"], 5)] for g in w["geometry"]]]
        found = query("places", box)
        if found is None:
            found = [e for e in cached_union("places") if e["type"] == "node"]
        for n in found:
            if n["tags"].get("place") not in PLACES:
                continue
            name = n["tags"].get("name:fr") or n["tags"].get("name")
            if name:
                places[n["id"]] = [name, round(n["lat"], 5), round(n["lon"], 5), PLACES[n["tags"]["place"]]]
    out = {"ways": [[c, thin(pts)] for c, pts in ways.values() if len(pts) > 1], "places": list(places.values())}
    path = WARN / "static" / "streets.json"
    path.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    if degraded:
        print("Overpass was refused: drew only the OSM ways saved along the routes (no full street grid). Run again later for the full map.")
    print(f"{len(out['ways'])} streets, {len(out['places'])} places -> static/{path.name} ({path.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
