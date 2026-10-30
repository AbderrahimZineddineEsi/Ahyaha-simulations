"""Sends a crash over the raw telemetry WebSocket, as the other team's telemetry service does, and prints the replies."""
import asyncio
import json
import os
from pathlib import Path

import websockets

EX = Path(__file__).resolve().parents[2] / "sim" / "examples"
URL = os.environ.get("SOLVEX_URL", "http://localhost:8000").replace("http", "ws", 1) + "/ws/telemetry"


async def main():
    normal, crash = (json.loads((EX / f"{n}.json").read_text()) for n in ("normal", "crash"))
    async with websockets.connect(URL, max_size=None) as ws:
        for seq, p in enumerate([*(normal[i % 16] for i in range(10)), *crash, *normal[:2]]):
            await ws.send(json.dumps({**p, "device_id": "ws-check", "seq": seq, "location": {"lat": 36.2, "lon": 3.7}}))
        replies = []
        try:
            while True:
                replies.append(json.loads(await asyncio.wait_for(ws.recv(), 3)))
        except asyncio.TimeoutError:
            pass
    print([(m["type"], m.get("level")) for m in replies])
    assert [m.get("level") for m in replies if m["type"] == "prediction"] == ["crash"]
    print("ok")


asyncio.run(main())
