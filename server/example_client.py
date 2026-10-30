"""
Sends the example windows in server/examples/ to the server and prints each reply. Only needs `websockets`.

    python server/example_client.py                                     # server on this PC
    python server/example_client.py ws://192.168.1.20:8000/ws/telemetry # server on another PC
"""
import sys
from pathlib import Path

from websockets.sync.client import connect

url = sys.argv[1] if len(sys.argv) > 1 else "ws://localhost:8000/ws/telemetry"

with connect(url) as ws:
    for path in sorted((Path(__file__).parent / "examples").glob("*.json")):
        ws.send(path.read_text())
        print(f"{path.name}: {ws.recv()}")
