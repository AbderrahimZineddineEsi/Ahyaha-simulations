"""
One-off: downloads the OpenStreetMap tiles the demo map needs, so it works with no internet.

    python warn/tools/fetch_tiles.py          # tiles along every route in warn/roads.json -> warn/static/map/{z}/{x}/{y}.png

Zoom 11 to 15 (the page zooms in further by enlarging zoom 15), one tile of margin around the routes. Already
downloaded tiles are skipped. The tile server is shared and free: one request a second, and the User-Agent says what
this is. The map must show "© OpenStreetMap contributors" (the page does).
"""
import json
import math
import sys
import time
import urllib.request
from pathlib import Path

WARN = Path(__file__).resolve().parents[1]
OUT = WARN / "static" / "map"
TILES = "https://tile.openstreetmap.org"
UA = "SolveX-hackathon-demo/1.0 (offline copy for a demo)"
MIN_ZOOM, MAX_ZOOM = 11, 15
PAUSE_S = 1.0


def tile_xy(lat, lon, z):
    n = 2 ** z
    return int((lon + 180) / 360 * n), int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)


def wanted():
    roads = json.loads((WARN / "roads.json").read_text())
    tiles = set()
    for route in roads.values():
        for leg in ("main", "alt"):
            if leg not in route:
                continue
            for lat, lon in route[leg]["path"]:
                for z in range(MIN_ZOOM, MAX_ZOOM + 1):
                    x, y = tile_xy(lat, lon, z)
                    for dx in (-1, 0, 1):
                        for dy in (-1, 0, 1):
                            tiles.add((z, x + dx, y + dy))
    return sorted(tiles)


def main():
    todo = [t for t in wanted() if not (OUT / str(t[0]) / str(t[1]) / f"{t[2]}.png").exists()]
    print(f"{len(todo)} tiles to download")
    for i, (z, x, y) in enumerate(todo, 1):
        path = OUT / str(z) / str(x) / f"{y}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(f"{TILES}/{z}/{x}/{y}.png", headers={"User-Agent": UA})
        for attempt in range(3):
            try:
                path.write_bytes(urllib.request.urlopen(request, timeout=30).read())
                break
            except Exception as error:
                print(f"  {z}/{x}/{y}: {error}", file=sys.stderr)
                time.sleep(3 * (attempt + 1))
        if i % 25 == 0:
            print(f"  {i}/{len(todo)}", flush=True)
        time.sleep(PAUSE_S)
    print("Done")


if __name__ == "__main__":
    main()
