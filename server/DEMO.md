# Demo script

Two screens: the **dashboard** (`/`) is what the Protection Civile agent sees, and **dev** (`/dev`) plays everyone else:
user 0 in the accident, and users 1 to 5 on their phones. Everything is made up, apart from the two severity cases.

Open both before you start. Press **Reset all** on `/dev` between runs.

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
7. Amel Kaci (nurse, 9.5 km by road, 9 min) accepts. Press **Call**, then **Confirm helper**: she gets the first-aid card.
8. She reaches the crash long before the ambulance, on an empty road, at night.

To show the victim's side, press a button on user 0's phone during the run:
- **Silent SOS:** the crash is verified, and severity is at least high. For a victim who can't speak.
- **Voice SOS:** the crash is verified, and the agent's call is answered.
- **I'm OK:** confidence still stays high.

## Notes for the pitch

- The blackbox recording is the same in every scenario, so it can't give different severities. Each scenario uses a **real crash case from the NHTSA CISS database**, run through M3 and M4. Say so if asked: the blackbox's own ΔV measurement is shown in the dev log, and is not used yet.
- The ambulance's drive is compressed (30 s for A, 60 s for B); the ETA shows real minutes.
- Phone numbers and people are fictional. The map's streets come from OpenStreetMap.
- Voice SOS is a button here. In the app it is a spoken phrase (Vosk), which isn't built yet. There is no phone line either: Call is a mock.
- Scenario 3 (no network) and the hospital view are not built yet.
