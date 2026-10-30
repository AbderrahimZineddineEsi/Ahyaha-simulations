# Demo script

Two screens: the **dashboard** (`/`) is what the Protection Civile agent sees (it logs in as the demo agent, phone `+213000000100`, password `demo`), and **dev** (`/dev`) plays everyone else: user 0 in the accident, and users 1 to 5 on their phones. Each phone is a logged-in user of the real API, exactly what the React Native app will do. Everything is made up, apart from the severity cases.

Open both before you start. Press **Reset all** on `/dev` between runs. In demo mode **Reset all** deletes the incidents, notifications and dispatches in the database, so run the demo on its own database (`DATABASE_URL`), never on real data.

## What the audience should understand

- The blackbox detects the crash. The server then estimates severity (M3) and the injured regions (M4).
- The person, and people nearby, are asked to confirm. Nobody has to call first.
- The agent sees it all, decides, and sends an ambulance with an ETA. Helpers can get there first.

## Scenario 1, network on, people nearby

### A: the ambulance is close, severity medium

1. On `/dev`, press **Scenario A**. User 0's car heads for a wall.
2. **Dashboard, 9 s in:** "Analyzing the trigger". The agent already sees the impact.
3. **16 s in:** "High confidence". Severity **medium (3.2%)**, check first: legs. 'Are you OK?' went to user 0 and an alert to the 5 people nearby.
4. **15 s later:** "High confidence, user not responding". (User 0 stays silent. Pressing "I'm OK" on their phone would lower the confidence instead.)
5. On `/dev`, press **Confirm** on user 1's phone. Dashboard: "confirmed by witness".
6. The agent has called the person's family or the witness. On the dashboard press **Confirm crash**.
7. Press **Dispatch** on Ambulance 1: ETA about 7 minutes. Watch it drive on the map (30 s on screen).
8. As it nears users 2 and 3, their phones get "Ambulance coming. Clear the yellow line." (once each). The dashboard timeline shows both.
9. "Ambulance 1 is on the scene."

### B: the ambulance is far, severity high

1. Tick **Auto** on `/dev` (a witness confirms and a helper accepts by themselves; user 0 never answers), then press **Scenario B**.
2. Same start. Severity **extreme (69%)**: thorax and legs likely.
3. On the dashboard: **Confirm crash**, then **Dispatch** Ambulance 1: ETA about **18 minutes** (heavy traffic). Ambulance 2 would take 19.
4. Press **Call for help.** Both helpers get the alert. Auto makes Amel Kaci (nurse) accept after 3 s.
5. The helper's card appears with her background and phone number. Press **Call** (a mock, there is no phone line yet), then **Confirm helper**: she gets the first-aid card.
6. On the map, the green helper reaches the crash well before the ambulance: "Amel Kaci is on the scene", with the ambulance still minutes away.

## Scenario 2, network on, no one around

A crash on RN 8B, 24 km south of Bouira. The map switches to that road by itself. The nearest person is over 8 km away.

1. On `/dev`, press **Scenario 2**. Leave Auto on if you want the helper to accept by herself.
2. **16 s in:** "High confidence, nobody nearby". Severity **high (41.7%)**. No witness alert goes out, because nobody is within 2.5 km. Only user 0's phone rings: the check-in, and a card with **Silent SOS** and **Voice SOS** that stays available.
3. Whatever the driver does, confidence stays high: nobody can confirm, and "I'm OK" could be wrong. Press **Call Karim** on the dashboard. If they haven't answered, the call goes unanswered too. **Call the contact** is the other option.
4. **Confirm crash**, with the severity you have.
5. **Dispatch** Ambulance 1: ETA about **23 minutes**. That's too long, so look at the helpers.
6. "No helpers within 5 km". Press **Widen to 20 km (2)**. A circle shows the range on the map.
7. Amel Kaci (nurse, about 9 km away, 9 min by road) accepts. Press **Call**, then **Confirm helper**: she gets the first-aid card.
8. She reaches the crash long before the ambulance, on an empty road, at night.

To show the victim's side, press a button on user 0's phone during the run:
- **Silent SOS:** the crash is verified, and severity is at least high. For a victim who can't speak.
- **Voice SOS:** the crash is verified, and the agent's call is answered.
- **I'm OK:** confidence still stays high.

## Scenario 3, no network, no one around

No crash signal can reach us in a dead zone, so the safety net comes first: the driver says when they'll be back, and the agent decides what to do before the signal goes. The map shows the zone in grey.

1. On `/dev`, press **Scenario 3**. The app asks user 0 when they expect to be back (15 s to answer). With no answer, it uses the route's ETA.
2. **A pop-up opens on the dashboard:** Karim is heading into a no-coverage zone, expected back by a given time. There's about half a minute before the signal goes. The agent can:
   - **Call Karim** (the call connects: he still has network).
   - **Warn them not to go.** The phone offers Turn back or Continue anyway.
   - **Tell them help can't reach them.**
   - **OK, keep watching.**
3. **If the driver turns back** (press it on their phone): the incident ends there.
4. **If they go in:** the signal drops. The telemetry stops, the map keeps the last known position, the Call button can't connect, and no warning can reach them.
5. **In the zone** the car crashes. The `/dev` screen shows it, but the dashboard can't: **the agent sees nothing**, which is the point.
6. **When the return time passes:** a red pop-up. The emergency contact is warned first, then the Gendarmerie, with the last position, route and car description.
7. On the panel, **Call for help** and **Dispatch**: the help heads past the last known position, along the planned route.
8. The first to arrive finds Karim. Only then does the agent learn it was a crash.

## Two incidents at once

Start any scenario, then in a terminal run `curl -N http://<server>:8000/blackbox/friend-01/crash`. A second incident appears in the dashboard's **Incidents** list, "Unknown person": nobody is registered for that blackbox, so there is no check-in and no profile, and severity assumes a default adult. Click either incident to switch. (The two don't merge because the unknown blackbox is 3 km away from the scenarios' crashes; two reports within 300 m and 5 minutes are one incident.)

## Hospital

Once M2 has detected a crash, the **Hospital** card on the dashboard lists hospitals with the suggested one first (free beds, distance, and the specialties M4's likely injuries call for: head goes to neurosurgery). **Send patient** sends them there. A hospital user (`+213000000101` Bouira, `+213000000102` Alger, password `demo`) sees only their own incoming patients through `GET /api/v1/hospitals/{id}/incoming`; there is no hospital page yet.

## Notes for the pitch

- The blackbox recording is the same in every scenario, so it can't give different severities. Each scenario uses a **real crash case from the NHTSA CISS database**, run through M3 and M4. Say so if asked: the blackbox's own ΔV measurement is shown in the dev log, and is not used yet.
- The ambulance's drive is compressed (30 s for A, 60 s for B); the ETA shows real minutes.
- Phone numbers and people are fictional. The map's streets come from OpenStreetMap.
- Voice SOS is a button here. In the app it is a spoken phrase (Vosk), which isn't built yet. There is no phone line either: Call is a mock.
- In scenario 3 the return time is compressed: the 30 s in the zone stand for the hour the driver promised. The grey zone is made up; in the real app it comes from OpenCelliD and our users' signal logs. Calling the Gendarmerie is a timeline line, not a real alert.
- The hospital page is not built yet: the API and the destination choice are.
