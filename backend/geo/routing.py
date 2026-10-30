"""
Where a vehicle goes and how long it takes. Providers are tried in order: the ones registered first (the sim puts
saved routes there so the demo works offline), OSRM if enabled, then a straight line. A provider is
`async fn(origin, dest) -> {"geometry": [[lat, lon], ...], "duration_s": n, "distance_m": n}` or None.
"""
import logging

import httpx

from backend.config import settings
from backend.geo.distance import haversine_m

log = logging.getLogger("solvex")
_front = []


def register(provider):
    _front.append(provider)


def clear():
    _front.clear()
    _cache.clear()


async def osrm(origin, dest):
    url = f"{settings.osrm_url}/route/v1/driving/{origin[1]},{origin[0]};{dest[1]},{dest[0]}?overview=full&geometries=geojson"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = (await client.get(url)).json()
        route = r["routes"][0]
        return {"geometry": [[lat, lon] for lon, lat in route["geometry"]["coordinates"]], "duration_s": route["duration"],
                "distance_m": route["distance"]}
    except Exception as e:
        log.warning("OSRM failed (%s): falling back", e)
        return None


async def straight(origin, dest):
    d = haversine_m(origin, dest) * 1.3  # roads are never straight
    return {"geometry": [list(origin), list(dest)], "duration_s": d / (settings.straight_speed_kmh / 3.6), "distance_m": d}


async def route(origin, dest):
    """The route and its duration with traffic applied. `traffic_factor` is a setting."""
    providers = [*_front, *([osrm] if settings.osrm_enabled else []), straight]
    for provider in providers:
        found = await provider(origin, dest)
        if found:
            return {**found, "duration_s": round(found["duration_s"] * settings.traffic_factor)}


_cache = {}


async def cached_route(origin, dest, max_age_s=120):
    """The same as route(), remembered for a while: the dashboard asks for an ETA each time it redraws."""
    import time
    key = (round(origin[0], 4), round(origin[1], 4), round(dest[0], 4), round(dest[1], 4), settings.traffic_factor)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < max_age_s:
        return hit[1]
    found = await route(origin, dest)
    _cache[key] = (time.monotonic(), found)
    return found


def forget():
    _cache.clear()
