"""
Blackboxes that run on the server, started by one request: the packets of each scenario, and how to print them.
"""
import json
from datetime import datetime, timezone
from itertools import chain, cycle, islice
from pathlib import Path

EXAMPLES = Path(__file__).parent / "examples"
LEAD_IN_S = 10  # normal driving before a scenario
TRIGGER_G = 1.0
SCENARIOS = ("normal", "crash", "braking")


def _load(name):
    return json.loads((EXAMPLES / f"{name}.json").read_text())


NORMAL, CRASH, BRAKING = _load("normal"), _load("crash"), _load("trigger_no_crash")


def _labelled(name, packets):
    return ((f"{name} {i + 1}/{len(packets)}", p) for i, p in enumerate(packets))


def scenario_packets(scenario, lead_in_s=LEAD_IN_S):
    """(stage, packet) for each second. Crash ends with the crash; the other two keep driving.
    The crash recording holds 8 s before the impact, so a lead-in of 0 is a crash that starts at once."""
    driving = (("normal", p) for p in cycle(NORMAL))
    if scenario == "normal":
        return driving
    lead_in = islice(driving, lead_in_s)
    if scenario == "crash":
        return chain(lead_in, _labelled("crash", CRASH))
    return chain(lead_in, _labelled("braking", BRAKING), driving)


def stamp(packet, device_id, seq):
    return {**packet, "device_id": device_id, "seq": seq, "recorded_at": datetime.now(timezone.utc).isoformat()}


def packet_line(stage, packet, size):
    peak = max((x * x + y * y) ** 0.5 for x, y, _ in packet["acc"])
    note = "  <-- over the 1 g trigger" if peak > TRIGGER_G else ""
    return (f"{datetime.now():%H:%M:%S}  packet {packet['seq']:<5} {stage:<13} peak {peak:5.2f} g"
            f"  speed {packet['gps_speed_kmh']:5.1f} km/h  {size} bytes{note}\n")


def reply_line(m):
    if m["type"] == "prediction":
        return (f"\n*** PREDICTION: {m['level']} (crash chance {m['p_crash'] * 100:.1f}%),"
                f" trigger in packet {m['trigger_seq']} ***\n\n")
    return f"\nRejected: {m.get('message')}\n\n"
