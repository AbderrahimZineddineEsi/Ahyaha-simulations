"""
Road-risk warning simulator: M1 on the phone.

The phone page scans the road ahead every few seconds. This server plays the map pack: it holds the road data
(OpenStreetMap attributes, already in the codes M1 expects) and answers GET /api/road-ahead. M1 itself runs in the
browser, not here.

    python warn/app.py                  # http://localhost:8001
    python warn/app.py --host 0.0.0.0   # reachable from a real phone on the same network
"""
import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent
MODEL = HERE / "static" / "m1_model.json"
EARTH_M = 6_371_000
MAX_LOOKAHEAD_M = 1e9   # the phone asks for the rest of the route when it compares two routes
# The demo has no weather feed: every request answers "dry" (the saved M1 barely uses weather, MODEL_SPEC.md)
DRY = {"rain_mm": 0, "rain_3h_mm": 0, "is_rain": 0, "is_fog": 0, "is_snow": 0, "is_storm": 0, "source": "demo: dry"}
SEGMENT_FIELDS = ("a", "b", "road", "town", "catr", "plan", "vma", "int", "agg", "circ", "nbv", "prof", "infra", "bend")


def load():
    presets = json.loads((HERE / "presets.json").read_text())["presets"]
    roads_file = HERE / "roads.json"
    if not roads_file.exists():
        sys.exit("warn/roads.json is missing: run python warn/tools/build_roads.py")
    roads = json.loads(roads_file.read_text())
    missing = [p["id"] for p in presets if p["id"] not in roads]
    if missing:
        print(f"No road data for {', '.join(missing)}: left out. Run python warn/tools/build_roads.py --only <id>")
    return [p for p in presets if p["id"] in roads], roads


def ensure_model():
    if MODEL.exists():
        return
    if not (HERE.parent / "m1" / "models" / "risk_model_nohist.txt").exists():
        sys.exit("The M1 weights are missing: run python download_models.py")
    subprocess.run([sys.executable, str(HERE / "tools" / "export_m1.py")], check=True)


def metres_between(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * EARTH_M * math.asin(math.sqrt(h))


def cumulative(path):
    cum = [0.0]
    for a, b in zip(path, path[1:]):
        cum.append(cum[-1] + metres_between(a, b))
    return cum


def snap(path, cum, lat, lon):
    """Distance along the path of the point nearest to a GPS fix."""
    best, best_m = float("inf"), 0.0
    k = math.cos(math.radians(lat))
    for i in range(len(path) - 1):
        ax, ay = (path[i][1] - lon) * k, path[i][0] - lat
        bx, by = (path[i + 1][1] - lon) * k, path[i + 1][0] - lat
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy
        u = 0.0 if length2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / length2))
        d2 = (ax + u * dx) ** 2 + (ay + u * dy) ** 2
        if d2 < best:
            best, best_m = d2, cum[i] + u * (cum[i + 1] - cum[i])
    return best_m


def make_app():
    ensure_model()
    presets, roads = load()
    by_id = {p["id"]: p for p in presets}
    cums = {(pid, leg): cumulative(r[leg]["path"]) for pid, r in roads.items() for leg in ("main", "alt") if leg in r}

    app = FastAPI(title="SolveX road warnings")

    @app.get("/")
    def index():
        return FileResponse(HERE / "index.html")

    @app.get("/api/presets")
    def list_presets():
        """The routes and the pace of the simulated car. No risk data: that is what the phone has to work out."""
        out = []
        for p in presets:
            r = roads[p["id"]]
            legs = {leg: {"path": r[leg]["path"], "length": r[leg]["length"],
                          "speeds": [[s["a"], s["b"], s["vma"]] for s in r[leg]["segments"]]}
                    for leg in ("main", "alt") if leg in r}
            out.append({"id": p["id"], "name": p["name"], "blurb": p["blurb"], "clock": p["clock"],
                        "branch_m": r.get("branch_m"), **legs})
        return out

    @app.get("/api/road-ahead")
    def road_ahead(preset: str, leg: str = "main", lat: float | None = None, lon: float | None = None,
                   from_m: float | None = None, length: float = 3000):
        """The road ahead of a GPS fix (or of `from_m` metres along the route), as segments with M1's inputs."""
        if preset not in by_id or leg not in roads[preset]:
            raise HTTPException(404, "unknown route")
        if length <= 0 or length > MAX_LOOKAHEAD_M:
            raise HTTPException(400, "length out of range")
        route = roads[preset][leg]
        if from_m is None:
            if lat is None or lon is None:
                raise HTTPException(400, "give lat and lon, or from_m")
            from_m = snap(route["path"], cums[(preset, leg)], lat, lon)
        segments = [{k: s[k] for k in SEGMENT_FIELDS} for s in route["segments"] if s["b"] > from_m and s["a"] < from_m + length]
        last = route["segments"][-1]["b"]
        return {"preset": preset, "leg": leg, "from_m": round(from_m), "to_m": segments[-1]["b"] if segments else round(from_m),
                "end": not segments or segments[-1]["b"] >= last, "segments": segments, "weather": DRY}

    @app.get("/health")
    def health():
        return {"ok": True, "presets": len(presets)}

    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    return app


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8001)
    args = ap.parse_args()
    print(f"Road warnings: http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}/")
    uvicorn.run(make_app(), host=args.host, port=args.port, log_level="warning")
