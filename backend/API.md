# SolveX API

For the React Native developer, and anyone building the Protection Civile or hospital pages. The live, always-correct reference is the OpenAPI page at `http://<server>:8000/docs`; this file explains how the pieces fit.

- **REST** under `/api/v1`, JSON in and out.
- **Socket.IO** at the server root (path `/socket.io`) for everything that happens without you asking.
- **Telemetry** from the blackbox over a plain WebSocket, `ws://<server>:8000/ws/telemetry`: see [PROTOCOL.md](PROTOCOL.md).

Every call except `register` and `login` needs `Authorization: Bearer <token>`. A token lasts 24 hours. A missing or bad token is `401`; the wrong role is `403`.

## Roles

| Role | Who | Can |
|---|---|---|
| `citizen` | app users, including helpers (`is_helper`) | their own profile, location, notifications, SOS, check-ins, blackboxes |
| `dispatcher` | Protection Civile agents | incidents and everything that acts on them, ambulances, hospitals (read) |
| `hospital` | a hospital's staff (has `hospital_id`) | their own beds and their own incoming patients |
| `admin` | us | everything a dispatcher can, plus creating staff accounts |

Sign-up always makes a `citizen`. Staff are made by an admin (`POST /admin/users`).

**Medical profiles** are shown only for a reason (PROJECT.md S13): a dispatcher sees the victim's profile only while the incident is open (`profile` in `GET /incidents/{id}`), and a hospital only for a patient the agent has sent to it (`GET /hospitals/{id}/incoming`). No other endpoint returns another person's profile.

## Accounts

| Call | Body | Returns |
|---|---|---|
| `POST /auth/register` | `phone`, `password` (6+), `name`, and optionally `age`, `sex` (`male`/`female`), `blood_type`, `conditions`, `emergency_contact_name`, `emergency_contact_phone`, `skills`, `is_helper` | `201 {token, user}`; `409` if the phone has an account |
| `POST /auth/login` | `phone`, `password` | `{token, user}` |
| `GET /me`, `PATCH /me` | any of the profile fields above | the user, with `location` |
| `POST /me/location` | `lat`, `lon`, `driving` (optional) | send it whenever it changes. The server finds people near a crash from this, so **an app that doesn't send locations is never asked to witness or help**. A location older than 15 min doesn't count. |
| `POST /me/push-token` | `token` (an Expo push token) | `{status: "ok"}` |
| `POST /devices` | `device_id`, `body_type` (`car`, `utility`, `van`, `pickup`) | `201 {device_id, device_key, owner_id}`. **The key is shown once.** The blackbox sends it as `device_key` in every packet. |

A crash from a blackbox whose owner is known opens an incident for that person, with their profile feeding M3. A blackbox nobody registered still opens one, flagged `unknown_victim`, with a default adult (`default_profile`).

## Notifications and answers

`GET /notifications?open_only=true` lists the user's notifications; new ones arrive on the `notification` Socket.IO event. Answer one with `POST /notifications/{id}/respond {"action": "<an id from actions>"}`. `409` means it was already answered or has expired, `422` an unknown action.

```json
{"id": 12, "kind": "check", "title": "Are you OK?", "text": "We detected a crash. Are you OK?",
 "actions": [{"id": "ok", "label": "I'm OK"}, {"id": "help", "label": "I need help"}],
 "state": "open", "answer": null, "remaining_s": 14.2, "incident_id": 3, "data": {}, "sent_at": "2026-10-30T12:00:00.000Z"}
```

`state` is `open` (waiting for an answer), `answered`, `expired` (nobody answered by the deadline) or `delivered` (no answer needed). `remaining_s` counts down to the deadline, `null` if there is none.

| `kind` | Sent to | Actions | Deadline |
|---|---|---|---|
| `check` | the person in the crash, and the driver of any other car reporting the same crash | `ok`, `help` | 30 s (15 s in the demo). No answer means "user not responding". |
| `sos` | the same person, when nobody is near | `silent`, `voice` | none |
| `witness` | citizens within 2.5 km | `confirm`, `deny` | none |
| `helper` | helpers the agent calls | `accept`, `decline` | none |
| `alert` | drivers within 300 m of an ambulance | none | none |
| `info` | a helper the agent confirmed (first-aid card) | none | none |
| `dz_back` | a driver about to enter a no-network zone | one per `data.back` option | 30 s (15 s in the demo), then `data.default` is used |
| `dz_warning` | the same driver, when the agent warns them | `turn_back`, `continue` or `ok` | none |

**Acknowledge every notification you receive**, with `POST /notifications/{id}/ack` or the `notification.ack` socket event; answering counts too. A user with no socket gets an Expo push at once. A connected phone that doesn't acknowledge within 5 s gets one as well, and anything still unacknowledged after 15 s goes by SMS. **Push needs an Expo project token and SMS has no provider yet: both only write a line to the log.**

`POST /sos {"kind": "silent" | "voice"}` is the SOS button, any time. It joins the user's open incident (confidence high, and a silent SOS means severity at least high) or, if the blackbox saw nothing, raises a new incident from the user's last location.

## Dead-zone check-ins

When the route ahead leaves coverage, the app asks the driver when they expect to be back and sends it:

`POST /checkins {"road": "RN 8B", "zone": {"center": [lat, lon], "radius_m": 3150}, "entry": [lat, lon], "back_in_min": 55}` → `201 {id, incident_id, back_by}`

The agent sees a "dead zone" incident at once and can call or warn the driver (`dz_warning`). Once the driver's location stops while inside the zone, the server marks them *in the zone* and keeps their last position. If they aren't back by `back_by` and the server has heard nothing from them, the incident becomes `overdue`, even if their last position was outside the mapped zone: the emergency contact and the Gendarmerie are warned (today: lines in the timeline) and the agent can send help to the last known position. A location from outside the zone ends it ("back and reachable"), as does `POST /checkins/{id}/return`. The deadline is stored, so a server restart doesn't lose it.

## The agent (dispatcher)

| Call | Body | Notes |
|---|---|---|
| `GET /incidents?open_only=true` | | short summaries, newest first |
| `GET /incidents/{id}` | | everything: `status`, `confidence`, `severity` (M3, M4 regions), `m2`, `profile`, `people`, `helpers`, `dispatch`, `helper_ranges`, `ambulances`, `dead_zone`, `hospital`, `events`, `notifications` |
| `POST /incidents/{id}/confirm` | `severity` (optional: `low`, `medium`, `high`, `extreme`) | defaults to the estimate |
| `POST /incidents/{id}/dispatch` | `ambulance_id` | one ambulance per incident. Only once M2 has answered (and, in a dead zone, once overdue). |
| `POST /incidents/{id}/call-helpers` | `radius_km` (5 or 20) | alerts helpers in range who weren't asked before. `409 nobody_left` if there are none. |
| `POST /incidents/{id}/confirm-helper` | `user_id` | after they accepted: sends the first-aid card |
| `POST /incidents/{id}/call` | `who`: `user` or `contact` | **a mock**: it writes the outcome to the timeline. There is no phone line yet. |
| `POST /incidents/{id}/warn` | `message`: `dont_go` or `no_help` | dead zone only, while the driver still has network |
| `POST /incidents/{id}/acknowledge` | | the agent has seen the dead-zone pop-up |
| `POST /incidents/{id}/found` | `lat`, `lon`, `by` | a responder reached a dead-zone driver: the crash is real, here |
| `POST /incidents/{id}/location` | `lat`, `lon` | places a crash that arrived with no GPS, and alerts the people near it. Until then nothing can be sent (`409 no_location`). |
| `GET /incidents/{id}/hospital-suggestions` | | hospitals ranked by distance, free beds and the specialties M4's injuries call for |
| `POST /incidents/{id}/assign-hospital` | `hospital_id` | the hospital then sees the patient |
| `POST /incidents/{id}/close` | | frees its ambulance, ends what was waiting, and tells helpers on their way to stand down |

Errors are `{"detail": "<code>"}`: `no_crash` (too early, or not that kind of incident), `no_location`, `closed`, `unavailable`, `already_dispatched`, `nobody_left`, `not_accepted`, `bad_radius`, `bad_severity`, `no_network`, `already_sent`.

### Merging

Two reports within 5 minutes, from the same blackbox or within 300 m of each other, are one incident: `flags.reports` counts the extra ones, and a second alarm raises confidence. If the first report turns out to be nothing and a merged one sees a crash, that one takes the incident over, so a real crash is never lost. The owner of the other car is in `flags.others`, appears in `people` as `also_in_crash`, and gets their own `check`. **The severity and profile shown are the first person's only.**

## Fleet

| Call | Body |
|---|---|
| `GET /centres`, `GET /ambulances` | |
| `POST /ambulances/{id}/location` | `lat`, `lon`. Send it while the ambulance moves: the server updates the ETA, tells drivers near it to clear the yellow line, and marks the arrival within 60 m. Returns `eta_remaining_s`. |
| `POST /ambulances/{id}/state` | `state`: `available` or `busy`. If it was on its way, the incident goes back to needing an ambulance. |

Routes come from OSRM, falling back to a straight line at 50 km/h (a rough estimate). The demo uses saved routes and leaves OSRM off, since it has to work offline. Outside the demo, set `OSRM_URL` to your own server: the public one is for testing only. `traffic_factor` multiplies every duration.

## Hospitals

| Call | Who | Body |
|---|---|---|
| `GET /hospitals` | any staff | |
| `PATCH /hospitals/{id}/beds` | that hospital's staff | `beds_free`, `beds_total` (optional) |
| `GET /hospitals/{id}/incoming` | that hospital's staff | patients the agent sent: severity, injured regions, ETA, and the profile |
| `POST /hospitals/{id}/incoming/{transfer_id}/arrived` | that hospital's staff | the patient is here: off the list, one bed fewer |

## Socket.IO

```js
const socket = io("http://<server>:8000", { auth: { token } });   // refused without a valid token
socket.on("notification", n => ...);
```

Rooms are chosen from the token: `user:{id}` for everyone, `dispatchers`, `hospital:{id}`. You can't join others.

| Event | Goes to | Payload |
|---|---|---|
| `notification` | that user, also when one of theirs changes state (answered, expired). Replace yours by `id`. | the notification object above |
| `incident.updated` | dispatchers, and `incident:{id}` | the incident summary. Fetch `GET /incidents/{id}` for the rest. |
| `ambulance.moved` | dispatchers | the ambulance, its `location`, `eta_remaining_s` |
| `hospital.incoming` | that hospital | the patient, or `{incident_id, cancelled: true}` |

A dispatcher can send `incident.watch {id}` to join `incident:{id}`. Anyone can send `notification.ack {id}` for their own notifications. Both return `{ok}`.

## Running it

See [README.md](README.md). Settings come from the environment: `DATABASE_URL`, `JWT_SECRET` (**change it**), `SOLVEX_DEMO` (`0` removes `/dev`, `/blackbox` and the seed accounts), `OSRM`, `OSRM_URL`, `EXPO_PUSH`.

## What isn't real yet

- Severity from a real blackbox uses `crash_summary()`, which can underestimate ΔV (MODEL_SPEC.md), so severity can come out low.
- Push is a log line until there is an Expo project; SMS, phone calls and the Gendarmerie alert are timeline lines.
- There are no hospital or police pages, and the scoring in the hospital suggestion is a simple one (PROJECT.md S4 weights aren't used).
- SQLite only has been run. The code avoids SQLite-only features, but Postgres hasn't been tried.
