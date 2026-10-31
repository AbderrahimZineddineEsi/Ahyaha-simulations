"""The demo's endpoints: the /dev screens, the scenario buttons, and the blackboxes that run on the server. Only exist with SOLVEX_DEMO=1."""
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.sim.runtime import UNKNOWN_SPOT_OFFSET, Blackbox, sim
from backend.sim.data import PLACES

WEB = Path(__file__).resolve().parent.parent / "web"
router = APIRouter()
NO_CACHE = {"Cache-Control": "no-cache"}   # a reload must not keep an old page
Key = Literal["a", "b", "2", "3", "n"]


class Auto(BaseModel):
    on: bool


@router.get("/dev")
def dev_page():
    return FileResponse(WEB / "dev.html", headers=NO_CACHE)


@router.get("/dev/monitor")
def monitor_page():
    return FileResponse(WEB / "monitor.html", headers=NO_CACHE)


@router.get("/dev/live")
def live_page():
    return FileResponse(WEB / "live.html", headers=NO_CACHE)


@router.get("/dev/simulator")
def simulator_page():
    return FileResponse(WEB / "simulator.html", headers=NO_CACHE)


@router.get("/dev/state")
async def state():
    return await sim.state()


@router.post("/dev/start/{key}")
async def start(key: Key):
    return {"device_id": await sim.start_scenario(key)}


@router.get("/dev/run/{key}")
async def run(key: Key, request: Request):
    """The same as the /dev button, from a terminal: `curl -N`, and the packets print as they are sent."""
    device_id = await sim.start_scenario(key, run_blackbox=False)    # this request is what runs it, so the packets print
    host = request.headers.get("host", "localhost:8000")

    async def stream():
        yield f"Scenario {key.upper()} started. Watch it on http://{host}/ and http://{host}/dev\n\n"
        async for chunk in sim.run_blackbox(device_id, sim.blackboxes[device_id]):
            yield chunk

    return StreamingResponse(stream(), media_type="text/plain")


@router.post("/dev/auto")
async def auto(body: Auto):
    sim.auto = body.on
    return {"auto": sim.auto}


@router.post("/dev/reset")
async def reset():
    await sim.reset()
    return {"status": "ok"}


@router.get("/blackbox/{device_id}/{scenario}")
async def blackbox(device_id: str, scenario: Literal["normal", "crash", "braking"], lat: float | None = None, lon: float | None = None):
    """A blackbox that runs on the server: one request, no files. A device nobody registered is an unknown person's."""
    if device_id in sim.blackboxes:
        return PlainTextResponse(f"{device_id} is already running. Stop it first, or use another name.\n", 409)
    town = PLACES["town"]["crash"]
    where = (lat, lon) if lat is not None and lon is not None else (town[0] + UNKNOWN_SPOT_OFFSET[0], town[1] + UNKNOWN_SPOT_OFFSET[1])
    sim.blackboxes[device_id] = Blackbox(scenario, location=where)
    return StreamingResponse(sim.run_blackbox(device_id, sim.blackboxes[device_id]), media_type="text/plain")


@router.websocket("/ws/monitor")
async def monitor(ws: WebSocket):
    """Live feed for the monitor pages. It only listens: nothing it sends is used."""
    from backend.telemetry.service import service
    await ws.accept()
    for device_id, bb in sim.blackboxes.items():
        await ws.send_json(bb.started_message(device_id))
    for car in service.cars.values():
        for message in (car.prediction, car.status):
            if message:
                await ws.send_json(message)
    sim.monitors.add(ws)
    try:
        while (await ws.receive())["type"] != "websocket.disconnect":
            pass
    finally:
        sim.monitors.discard(ws)


def mount(app):
    app.include_router(router)
    app.mount("/examples", StaticFiles(directory=Path(__file__).resolve().parent / "examples"), name="examples")
