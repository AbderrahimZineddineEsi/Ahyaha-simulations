"""
A small simulator: sends the ready-made messages in server/examples/ the way the telemetry service should.
Only needs `websockets`.

    python server/example_client.py normal             # normal driving until Ctrl+C
    python server/example_client.py crash              # 10 s of normal driving, then the crash
    python server/example_client.py trigger_no_crash   # 10 s of normal driving, the jolt, normal driving again
    python server/example_client.py crash --fast       # no waiting between messages
    python server/example_client.py crash --url ws://192.168.1.20:8000/ws/telemetry
"""
import argparse
import json
import time
from datetime import datetime, timezone
from itertools import chain, count, cycle, islice
from pathlib import Path

from websockets.exceptions import ConcurrencyError
from websockets.sync.client import connect

EXAMPLES = Path(__file__).parent / "examples"
LEAD_IN_S = 10
AFTER_NO_CRASH_S = 30  # normal driving carries on after the jolt

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("scenario", choices=["normal", "crash", "trigger_no_crash"])
ap.add_argument("--url", default="ws://localhost:8000/ws/telemetry")
ap.add_argument("--device", default="blackbox-01")
ap.add_argument("--fast", action="store_true")
args = ap.parse_args()


def load(name):
    return json.loads((EXAMPLES / f"{name}.json").read_text())


def show_replies(ws, wait=0):
    while True:
        try:
            print(ws.recv(timeout=wait))
        except (TimeoutError, ConcurrencyError):
            return


normal = cycle(load("normal"))
if args.scenario == "normal":
    playlist = normal
elif args.scenario == "crash":
    playlist = chain(islice(normal, LEAD_IN_S), load("crash"))
else:
    playlist = chain(islice(normal, LEAD_IN_S), load("trigger_no_crash"), islice(normal, AFTER_NO_CRASH_S))

with connect(args.url) as ws:
    print(f"sending {args.scenario} as {args.device}")
    for seq, message in zip(count(), playlist):
        ws.send(json.dumps({**message, "device_id": args.device, "seq": seq,
                            "recorded_at": datetime.now(timezone.utc).isoformat()}))
        show_replies(ws)
        if not args.fast:
            time.sleep(1)
    show_replies(ws, wait=2)
