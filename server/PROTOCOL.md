# Telemetry protocol

How the telemetry service talks to the SolveX server. Open one WebSocket and send one JSON message per car, every second, for as long as the simulation runs. You only send data: the server stores it, looks for triggers and runs the crash model.

```
ws://<server-ip>:8000/ws/telemetry
```

The server prints the full address when it starts. Both PCs must be on the same network.

## 1. Test the connection

Open `http://<server-ip>:8000/health` in a browser. You should see `"status":"ok"`.

Then run the example simulator, which replays the recordings in [examples/](examples/):

```bash
pip install websockets
python example_client.py crash --url ws://<server-ip>:8000/ws/telemetry
python example_client.py trigger_no_crash --url ws://<server-ip>:8000/ws/telemetry
```

Each run takes about 36 s and ends with one prediction: `level` `crash` for the first, `none` for the second. Add `--fast` to send without waiting. `python example_client.py normal` sends normal driving until you press Ctrl+C.

## 2. What to send

```json
{
  "type": "telemetry",
  "device_id": "blackbox-01",
  "seq": 42,
  "recorded_at": "2026-10-30T02:13:00+01:00",
  "location": {"lat": 36.3741, "lon": 3.9012},
  "acc": [[0.05, 0.02, 1.01], [0.06, 0.02, 0.99], "... 100 rows"],
  "gyro": [[0.0, 1.0, 0.0], [0.0, 1.0, -1.0], "... 100 rows"],
  "gps_speed_kmh": 74
}
```

| Field | Required | Format |
|---|---|---|
| `type` | yes | always `"telemetry"` |
| `device_id` | yes | which car, up to 128 characters. Each car needs its own. |
| `seq` | yes | whole number that goes up by exactly 1 each second, per car, from 0 |
| `recorded_at` | no | ISO 8601 time of this second |
| `location` | no | `lat` and `lon` of the car |
| `acc` | yes | exactly **100 rows** of `[x, y, z]`, in **g** |
| `gyro` | yes | exactly **100 rows** of `[x, y, z]`, in **deg/s**, same axes |
| `gps_speed_kmh` | yes | one number from 0 to 300, or `null` if GPS is lost |

Send `recorded_at` and `location` even though they're optional: the next phase needs them to place the crash. Extra fields are ignored, so you can add something like `"scenario": "crash"` for your own use.

**Axes:** x forward, y **left**, z up. At rest the accelerometer reads `[0, 0, +1]`, because gravity is included.

The mistakes that matter:

- **Using y pointing right** gives no error, but flips every side impact.
- **Sending gyroscope values in rad/s** gives no error either, and the values will be far too small.
- **Sending acceleration in m/s²** is rejected.
- **Sending `NaN`** is rejected. Use `null` for a lost GPS speed.

## 3. What happens on the server

1. Every valid message is saved to the database.
2. Each second is checked for a trigger: x-y acceleration, `sqrt(x² + y²)`, above **1 g**.
3. When it fires, the server keeps the **8 s before** and collects the **8 s after**.
4. M2 scores those 16 s, and you get the prediction.
5. New triggers are ignored until 8 s after the window ends, so shaking after a crash doesn't count as a second event.

Normal messages get no reply. To see triggers and predictions as they happen, watch the server's log.

If a car's messages stop for 3 s during step 3, or its connection closes, M2 scores what arrived, with the missing seconds filled in. A short `seq` gap is filled the same way. A `seq` that goes backwards means the simulator restarted, so that car starts fresh. Don't resend old seconds.

## 4. Scenarios

Run one scenario at a time for each car. Between them, keep sending normal driving.

### Normal driving: sent all the time

- Keep x-y acceleration **below 1 g** at every sample, including noise.
- Keep z around +1 g.
- Quiet driving with 0.05 g of accelerometer noise and 0.5 deg/s of gyroscope noise ran for 120 s with no trigger.

### Scenario 1: crash

Send **at least 8 s of normal driving first**, then in order:

1. **The impact:** a pulse of about 5 g for 100 ms on x-y. Negative x is a frontal hit.
2. **The messy part:** for about 3 s, add a gyroscope spin starting at 60 deg/s on z and 24 deg/s on x, plus 0.4 g of shaking on x and y. Both fade over about 0.8 s.
3. **The stop:** drop `gps_speed_kmh` to 0 within 2 s, then send a quiet, stopped car.
4. **Keep sending** for at least 8 s after the impact, or the server treats the stream as cut.

Results from 20 runs of each, with this recipe:

| Crash | `crash` | `likely` |
|---|---|---|
| Frontal, 5 g | 20 | 0 |
| Frontal, 8 g | 20 | 0 |
| Side, 5 g (positive or negative y) | 10 | 10 |
| Rear, 5 g (positive x) | 2 | 18 |

Hitting harder doesn't help: real crashes peak around 3.7 g, and a clean 30 g pulse scored lower than a clean 5 g one. Don't skip step 2, because the messy seconds after the impact matter more than the size of the pulse.

For a result that must work every time in the demo, replay `examples/crash.json`, a real recording.

### Scenario 2: trigger without a crash

The jolt must go **above 1 g on x-y**, then the car carries on driving. Results from 20 runs of each:

| Event | How to simulate it | Trigger fired | Result |
|---|---|---|---|
| Hard braking | x falls to -1.1 g over 2 s, speed 90 to 40 km/h, car keeps moving | 20 | `none` 20 |
| Kerb hit | 1.5 g on y and 2 g on z for 150 ms, speed unchanged | 20 | `possible` 20 |
| Harder kerb hit | 2.5 g on y and 2 g on z for 150 ms, speed unchanged | 20 | `possible` 19, `likely` 1 |
| Pothole | 3 g on z only | 0 | no prediction |

Hard braking is the cleanest choice, and `examples/trigger_no_crash.json` is that case. A pothole only on z never fires the trigger, because the trigger reads x-y.

## 5. What you get back

When a window is finished, a prediction goes to the connection that last sent data for that car:

```json
{
  "type": "prediction",
  "device_id": "blackbox-01",
  "model": "m2",
  "trigger_seq": 19,
  "trigger_recorded_at": "2026-10-30T02:13:00+01:00",
  "location": {"lat": 36.3741, "lon": 3.9012},
  "p_crash": 0.998957,
  "level": "crash",
  "p_lgbm": 0.998187,
  "p_cnn": 0.999727,
  "latency_ms": 6.71,
  "filled_s": 0.0,
  "stream_ended": false,
  "prediction_id": 18
}
```

| Field | Meaning |
|---|---|
| `trigger_seq` | the second the trigger fired in. The prediction arrives about 8 s later. |
| `p_crash` | chance of a crash, 0 to 1: the mean of `p_lgbm` and `p_cnn` |
| `level` | the decision, see below |
| `p_lgbm`, `p_cnn` | the two models inside M2. A big gap between them often means the simulated data looks unrealistic. |
| `filled_s` | seconds of the window that weren't received. Above 0, trust the result less. |
| `stream_ended` | `true` if the stream stopped before the window was complete |

| `level` | Rule |
|---|---|
| `crash` | `p_crash` ≥ 0.9585 |
| `likely` | `p_crash` ≥ 0.5726 |
| `possible` | `p_crash` ≥ 0.0886 |
| `none` | below that |

The server applies these rules for you, so you only need to read `level`.

## 6. Errors

A rejected message gets an error reply, and isn't stored. The connection stays open, so keep sending.

```json
{
  "type": "error",
  "code": "invalid_telemetry",
  "device_id": "blackbox-01",
  "seq": 42,
  "message": "acc: List should have at least 100 items after validation, not 99"
}
```

| `code` | Meaning |
|---|---|
| `invalid_json` | the message isn't valid JSON |
| `invalid_telemetry` | valid JSON, but a field is missing or wrong. `message` says which. |
| `internal_error` | the server failed. Tell the server's owner. |

`device_id` and `seq` are `null` when the server couldn't read them. A message over 200 kB closes the connection with code 1009.
