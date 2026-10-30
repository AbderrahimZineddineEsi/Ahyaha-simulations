# Blackbox telemetry protocol

How a simulated blackbox talks to the SolveX server. You open one WebSocket, send a JSON message for each event, and get one JSON reply with M2's crash prediction.

```
ws://<server-ip>:8000/ws/telemetry
```

Ask for `<server-ip>`: the server prints the full address when it starts. Both PCs must be on the same network.

## 1. Test the connection

Open `http://<server-ip>:8000/health` in a browser. You should see `{"status":"ok","model":"m2"}`.

Then send the three real recordings in [examples/](examples/):

```bash
pip install websockets
python example_client.py ws://<server-ip>:8000/ws/telemetry
```

| File | Expected `level` | Expected `p_crash` |
|---|---|---|
| `crash.json` | `crash` | 0.999 |
| `near_miss.json` | `none` | 0.00007 |
| `normal_driving.json` | `none` | 0.00002 |

## 2. Request

Send **one message per event, not one per sample**. Like the real blackbox, the simulator records 16 seconds around a trigger and sends the whole window as a single text frame. You can generate it instantly: nothing has to wait 16 real seconds. A window is about 60 kB, and the limit is 2 MB.

```json
{
  "type": "telemetry_window",
  "event_id": "frontal-rn5-001",
  "device_id": "blackbox-01",
  "recorded_at": "2026-10-30T02:13:00+01:00",
  "location": {"lat": 36.3741, "lon": 3.9012},
  "acc": [[0.05, 0.02, 1.01], [0.06, 0.02, 0.99], "... 1600 rows"],
  "gyro": [[0.0, 1.0, 0.0], [0.0, 1.0, -1.0], "... 1600 rows"],
  "gps_speed_kmh": [75, 74, 74, 74, 74, 74, 74, 73, 73, 73, 70, 55, 39, 29, 21, 14]
}
```

| Field | Required | Format |
|---|---|---|
| `type` | yes | always `"telemetry_window"` |
| `event_id` | yes | your own unique text, up to 128 characters. It comes back in the reply. |
| `device_id` | yes | which simulated blackbox, up to 128 characters |
| `recorded_at` | no | ISO 8601 time of the event |
| `location` | no | `lat` and `lon` of the event |
| `acc` | yes | exactly **1600 rows** of `[x, y, z]`, in **g**, 100 Hz |
| `gyro` | yes | exactly **1600 rows** of `[x, y, z]`, in **deg/s**, 100 Hz, same axes |
| `gps_speed_kmh` | yes | exactly **16 values**, in km/h, 0 to 300, 1 Hz. Use `null` for a lost GPS sample. |

M2 ignores `recorded_at` and `location`, but send them anyway: the next phase needs them to place the crash. Extra fields are ignored, so you can add something like `"scenario": "frontal"` for your own use.

**Axes:** x forward, y **left**, z up. At rest the accelerometer reads `[0, 0, +1]`, because gravity is included.

The mistakes that matter:

- **Using y pointing right** gives no error, but flips every side impact.
- **Sending gyroscope values in rad/s** gives no error either, and the values will be far too small.
- **Sending acceleration in m/s²** is rejected.
- **Sending `NaN`** is rejected. Python's `json.dumps` writes it for you if your array has one, so turn missing GPS samples into `None`.

Acceleration beyond ±50 g and rotation beyond ±2000 deg/s are clipped.

## 3. Response

You get one reply for each message, in the same order.

```json
{
  "type": "prediction",
  "event_id": "frontal-rn5-001",
  "device_id": "blackbox-01",
  "model": "m2",
  "p_crash": 0.999062,
  "level": "crash",
  "p_lgbm": 0.998691,
  "p_cnn": 0.999433,
  "latency_ms": 6.65
}
```

| Field | Meaning |
|---|---|
| `p_crash` | chance of a crash, 0 to 1: the mean of `p_lgbm` and `p_cnn` |
| `level` | the decision, see below |
| `p_lgbm`, `p_cnn` | the two models inside M2. A big gap between them often means the simulated data looks unrealistic. |
| `latency_ms` | time M2 took |

| `level` | Rule |
|---|---|
| `crash` | `p_crash` ≥ 0.9585 |
| `likely` | `p_crash` ≥ 0.5726 |
| `possible` | `p_crash` ≥ 0.0886 |
| `none` | below that |

The server applies these rules for you, so you only need to read `level`.

## 4. Errors

A rejected message gets an error reply. The connection stays open, so just send the next one.

```json
{
  "type": "error",
  "code": "invalid_window",
  "event_id": "frontal-rn5-001",
  "message": "acc: List should have at least 1600 items after validation, not 800"
}
```

| `code` | Meaning |
|---|---|
| `invalid_json` | the message isn't valid JSON |
| `invalid_window` | valid JSON, but a field is missing or wrong. `message` says which. |
| `internal_error` | the server failed. Tell the server's owner. |

`event_id` is `null` when the server couldn't read it from your message. A message over 2 MB closes the connection with code 1009.

## 5. Making scenarios M2 believes

M2 learned from real commercial-vehicle recordings, so keep the simulated signals close to them. The real crashes in our samples peak at only **2–6 g** in the x-y plane, far below what you might expect, and many are low-speed contacts.

**Harder is not better.** M2 answers "was there a crash?", not "how bad was it?". These are results for simple half-sine pulses on a quiet signal:

| Simulated event | `p_crash` | `level` |
|---|---|---|
| Frontal, 30 km/h, 5 g, 100 ms | 0.959 | `crash` |
| Frontal, 50 km/h, 12 g, 120 ms | 0.957 | `likely` |
| Frontal, 90 km/h, 30 g, 120 ms | 0.937 | `likely` |
| Side, 60 km/h, 10 g, 80 ms | 0.744 | `likely` |
| Rear, 20 km/h, 4 g, 120 ms | 0.613 | `likely` |
| Hard braking: 0.7 g for 3 s, 90 to 35 km/h | 0.008 | `none` |
| Pothole: 3 g on z for 150 ms | 0.035 | `none` |
| Quiet driving at 60 km/h | 0.00007 | `none` |

All the simulated crashes above also had the GPS speed drop after the pulse, so always include that drop.

**A clean pulse isn't enough to reach `crash`.** What helps most is making the moments after the impact look messy: noise on the accelerometer, a spin on the gyroscope, and shaking that fades over about 3 s. Here is the share of 20 random runs that reached `crash`, with a 100 ms pulse at 60 km/h and the car stopping within 2 s:

| Pulse | Clean signal | With noise, spin and shaking |
|---|---|---|
| Frontal, 3 g | 0 of 20 | 17 of 20 |
| Frontal, 5 g | 0 of 20 | 20 of 20 |
| Frontal, 8 g | not tested | 20 of 20 |
| Side, 5 g | not tested | 7 of 20 |
| Rear, 5 g | not tested | 0 of 20 |

In these runs the clean pulses and the side and rear hits that missed `crash` all still reached `likely`.

The settings for the right-hand column were accelerometer noise of 0.05 g throughout, a gyroscope spin starting at 60 deg/s on z and 24 deg/s on x, and shaking of 0.4 g on x and y, both fading over about 0.8 s.

For a scenario that must work every time in the demo, replay `crash.json` instead of a synthetic window. The impact can sit anywhere in the window: moving it between 2 s and 15 s barely changed the score.
