"""
A small simulator: streams real recordings from server/examples/ the way the telemetry service should.
Only needs `websockets`.

    python server/example_client.py normal             # normal driving until Ctrl+C
    python server/example_client.py crash              # normal driving, the crash, the car stopped
    python server/example_client.py trigger_no_crash   # normal driving, a jolt, driving on
    python server/example_client.py crash --fast       # no waiting between messages
    python server/example_client.py crash --url ws://192.168.1.20:8000/ws/telemetry
"""
import argparse
import json
import time
from datetime import datetime, timezone
from itertools import chain, count, repeat
from pathlib import Path

from websockets.exceptions import ConcurrencyError
from websockets.sync.client import connect

HZ = 100
EXAMPLES = Path(__file__).parent / "examples"
LEAD_IN_S = 10  # enough seconds before the event for the 8 s window
TAIL_S = 10     # enough seconds after it to finish the window
LOCATION = {"lat": 36.3741, "lon": 3.9012}

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("scenario", choices=["normal", "crash", "trigger_no_crash"])
ap.add_argument("--url", default="ws://localhost:8000/ws/telemetry")
ap.add_argument("--device", default="blackbox-01")
ap.add_argument("--fast", action="store_true")
args = ap.parse_args()


def seconds_of(recording):
    """Splits a 16 s recording into 16 one-second pieces of (acc, gyro, speed)."""
    r = json.loads((EXAMPLES / f"{recording}.json").read_text())
    return [(r["acc"][i * HZ : (i + 1) * HZ], r["gyro"][i * HZ : (i + 1) * HZ], r["gps_speed_kmh"][i])
            for i in range(16)]


def show_replies(ws, wait=0):
    while True:
        try:
            print(ws.recv(timeout=wait))
        except (TimeoutError, ConcurrencyError):
            return


if args.scenario == "normal":
    drive = seconds_of("normal_driving")
    playlist = chain.from_iterable(repeat(drive))
else:
    event = seconds_of(args.scenario)
    playlist = chain(repeat(event[0], LEAD_IN_S), event, repeat(event[-1], TAIL_S))

with connect(args.url) as ws:
    print(f"streaming {args.scenario} as {args.device}")
    for seq, (acc, gyro, speed) in zip(count(), playlist):
        ws.send(json.dumps({
            "type": "telemetry",
            "device_id": args.device,
            "seq": seq,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "location": LOCATION,
            "acc": acc,
            "gyro": gyro,
            "gps_speed_kmh": speed,
        }))
        show_replies(ws)
        if not args.fast:
            time.sleep(1)
    show_replies(ws, wait=2)
