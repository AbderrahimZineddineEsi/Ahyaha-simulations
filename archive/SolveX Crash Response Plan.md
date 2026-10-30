SolveX 3.0 · Problématique 1 · Team doc · Fri 30 Oct, 03:00 · \~34 h left

# Crash response plan

Everything we said, organized, plus research and suggestions. The team picks the features and splits the work.

1. **Accident**GPS from phone + blackbox
2. **Alert**app, people nearby, SMS
3. **Evaluation**M2–M4 + caller answers
4. **Orientation**right hospital: missing [S2](#s2)

Plain text: from our notes grey italic: clarification S3 suggestion in section 5 open not decided yet

[Read first](#first)[0 Brief](#brief)[1 Our plan](#plan)[2 Algeria](#algeria)[3 What we build](#build)[4 AI models](#ai)[5 Suggestions](#suggestions)[Sources](#sources)

## Read this first

- The brief asks for **vital signs** and for **sending the patient to a suitable facility**. Our plan has neither yet. [S2](#s2)
- 34 hours is short for 4 models, 4 apps and a simulator. Write the demo story first and build only what it shows. [S1](#s1)
- Crash detection on the blackbox doesn't need AI; physics rules work. Spend the AI effort on severity and body parts, which have papers and datasets. [Section 4](#ai)
- Don't ask drivers to tap pop-ups while they drive. [S3](#s3)
- The hidden work needs owners too: simulated sensor data, datasets, training, the demo panel, the pitch. [Task list](#tasks)

## 0What the brief asks

Minutes are lost at four steps: locating the crash, judging its severity, passing the information on, and sending the patient to a suitable facility. The jury wants a decision-support layer that *complements* today's services, uses geolocation, crash data and vital signs, and copes with weak connectivity (SMS, low bandwidth).

| The brief asks for | Our plan |
| --- | --- |
| Locate the crash | coveredGPS from phone and blackbox |
| Judge severity | coveredM3, M4, caller and witness answers |
| Pass information on | coveredshared platform, SMS |
| Vital signs | missing[S2](#s2) |
| Suitable facility | partlywe dispatch an ambulance but never choose the hospital [S2](#s2) |
| Weak connectivity | coveredSMS fallback, dead-zone timer |
| Complement existing services | coveredthe dispatcher logs phone calls into the platform; [S12](#s12) makes it stronger |

## 1Our plan, organized

Everything from our notes is here. Only the order and wording changed.

### Pieces and assumptions

- **Mobile app** for users.
- **Role-based platform** for the Protection Civile (pompiers, himaya madaniya), the police and Gendarmerie (darak watani), and hospitals.
- Two devices exist: the **phone** and the **blackbox**, an in-car device we design but only simulate (no hardware). The blackbox has a **digital twin** in the app that shows its results. open [S5](#s5)
- We didn't know which Algerian parties are involved or how they work together. Researched in [section 2](#algeria).

### Sign-up profile

Collected once, used later by the models and the hospital:

- medical background
- first-aid skills and certificates
- chronic diseases
- pregnancy
- age
- emergency contacts
- address
- more to decide

### AI models (3 or 4)

|  | Model | Output | Used for |
| --- | --- | --- | --- |
| M1 | Dangerous roads, from road type, weather, history… Warn the user before entering the road. الوقاية خير من العلاج | risk level | warnings while driving |
| M2 | Crash detection from phone + blackbox. Fuse both for higher confidence, or coordinate them? open [S4](#s4) | confidence | different actions per confidence level |
| M3 | Severity for the **person**, not the car | low · medium · high · extreme | dispatch priority |
| M4 | Injured body parts (might merge with M3) | body map | hospital's digital twin |

**Why keep M3 and M4 separate:** if M3 is strong and M4 weak, a merged model drags severity down. Separate, we get a reliable M3 for dispatch, and M4 only serves the hospital.

### Who sees what

| Hospital | The patient's digital twin after the crash: M4 body map + sign-up profile. |
| --- | --- |
| Protection Civile | Map of the crash · devices (people) nearby · helper profiles (medics and first-aiders highlighted in red) · ambulance availability · destination of ambulances already on the way (used later) · staff nearby. |
| Gendarmerie | Not decided, maybe staff nearby. open [S8](#s8) |

### The flow

It depends on two things: **are people nearby?** and **is there network?**

#### Before the crash

network on

M1 warns: "Medium accident risk ahead, be careful" or "Lower speed advised", as a notification or pop-up. Very high risk gets a stronger alert, maybe.

#### Crash detected

network on

M2 detects it and broadcasts to nearby devices: "Accident nearby: confirm / deny", with the confidence level. M3 runs and gives the severity level.

#### A · People nearby

Goal: fastest reaction, save the life

1. One person taps confirm → confidence 100%.
2. Nearby cars get "Accident ahead, slow down immediately".
3. Someone probably called. The dispatcher (we said "responder"):
   1. marks the crash confirmed, or creates a new entry if the app never reported it;
   2. sets the severity: if a caller answers the dispatcher's questions, that severity wins;
   3. frees an ambulance, prioritized by severity, gives it the destination, gets the ETA;
   4. presses **"Call for help"** to alert nearby helpers, then waits for one to accept or calls one directly to guide them. This matters most when the crash is far, severity is high and the ambulance is slow.
4. Ambulance on the way (maybe the accepted helper too, an idea, not confirmed) → phones on its route get **"Libérez la ligne jaune"** (free the emergency lane).
5. Before leaving, the crew sees the patient's condition on the digital twin and prepares. Same for the hospital.

#### B · Nobody around

Goal: get the report to the emergency services

1. "Are you OK? 30 seconds to answer." No answer → alert the authorities.
2. Nobody can confirm severity, so we use the predicted one.
3. Same "Call for help" button, maybe with a wider radius.
4. **Voice trigger**, Siri/Bixby style but inside our app, so it works on any phone and offline (Bixby needs internet): "help help help" starts a call. For when you can't hold the phone.
5. **Silent alert button**: doesn't call, just raises the alert on the platforms. For when you can't talk.
6. 4 and 5 set confidence to 100%. With 4, the caller can tell the exact severity; with 5, we assume high.
7. The dispatcher may wait for 4 or 5 before pressing "Call for help". If severity is high, they press it and guide the helper. open [S9](#s9)

#### No network

- No mobile data (we wrote "no wifi") → send the same alerts and calls by SMS.
- If SMS fails too, is there another technology? open [S6](#s6)
- No network at all:
  1. Survival guide in the app (nice to have; juries may not care much).
  2. We can't help inside a dead zone, so we warn before it. With a map of Algeria's no-coverage zones, about 15 minutes before entering: *"You're entering a no-network zone. Set a time (1 h, ETA + 30 min, or longer if you're staying). If you're not back in coverage by then, we assume you're in danger and send help."*

### Problems we listed (handwritten notes)

1. Unknown or isolated location
2. Difficult access, long travel time
3. Very short tolerance time (!)
4. Coordination: ambulance, police, tow truck (dépannage)
5. Body injuries: can't use the hands even when conscious
6. Phone or sensors damaged in the crash
7. Different groups of society: languages, especially when calling
8. Unpredictable time (2 AM!)
9. Victim isn't a driver: may have nothing on them
10. Many passengers or vehicles: each needs different help
11. No feedback system for bad help (linked to #4)
12. Lack of awareness: people don't know what to do; nobody around; "I'm fine, the car isn't, no help"; how to deal with the car, a wife, a child, an elderly person

## 2Who's involved in Algeria

In 2025: **3,838 deaths** and 37,020 injured in 26,976 injury crashes (DNSR). About 10 deaths a day. 842 of those deaths happened in cities (DGSN).

| Party | Role in a road crash | Number | In our platform |
| --- | --- | --- | --- |
| Protection Civile الحماية المدنية | First on scene: rescue, extrication, first aid, then takes the injured to hospital. In practice it is the road-crash ambulance. | 14 · 1021 | Main dispatch dashboard |
| Gendarmerie Nationale الدرك الوطني | Outside cities (national roads, highways, rural areas): secures the scene, manages traffic, writes the accident report (PV). | 1055 | Scene, traffic and report view |
| Sûreté Nationale (DGSN) | Police: same job inside cities. | 17 · 1548 | Same view, city jurisdiction |
| Hospitals | Emergency units (UMC) of CHU and EPH hospitals, plus specialized EHS: receive, triage, treat; transfer the patient if they can't handle the injury. | – | Pre-arrival twin, free beds |
| SAMU | Medical regulation and transfers, only in some wilayas. | varies | Optional |
| DNSR | Délégation nationale à la sécurité routière: prevention and national statistics. | – | Black-spot data for M1, our KPIs |
| Others | Telecom operators (Mobilis, Djezzy, Ooredoo) for SMS and mass alerts · ONM for weather · tow trucks · insurers · Croissant-Rouge Algérien (trained volunteers) |  |  |

### How a crash is handled today (simplified, from press reports)

1. A witness calls 14 or 1021 (or 1055, 17) and describes the place by voice.
2. The Protection Civile sends its nearest unit.
3. The Gendarmerie (outside cities) or the police (in cities) secure the scene and write the report.
4. The Protection Civile takes the injured to the nearest hospital.
5. If that hospital can't treat the injury, the patient is transferred.

**Where minutes are lost:** vague location · nobody calls (night, empty road) · severity unknown until the crew arrives · hospital not warned · services coordinate by phone · wrong first hospital, then a transfer.

Numbers and roles come from news and travel sources and mostly agree. SAMU's role and number vary by wilaya. If someone knows a Protection Civile officer, confirm before the pitch.

## 3What we're building

Features of each piece. Untagged items come from our notes; tagged items are suggestions you can take or drop.

### Citizen app

mobile · drivers, passengers, helpers

- Sign-up with medical profile and emergency contacts
- Blackbox pairing and its digital twin [S5](#s5)
- Drive mode: M1 risk warnings (by voice [S3](#s3)), phone-side crash detection (M2), voice trigger
- After a crash: 30 s "Are you OK?" countdown, silent alert button
- Alerts: confirm/deny a nearby crash, "accident ahead", "libérez la ligne jaune"
- Helper mode: get a call for help, accept, navigate, follow guidance cards [S9](#s9)
- No network: SMS, dead-zone timer, survival guide
- Bystander report [S7](#s7) · watch vitals [S2](#s2)

### Blackbox

simulated · in every car

- Streams accelerometer, gyroscope, GPS speed, seatbelt and airbag state
- Detects the crash and computes speed change (ΔV), impact direction, rollover → feeds M2, M3, M4
- Own SIM: sends the SMS or call when the phone is dead [S5](#s5) [S12](#s12)
- Knows who is in the car via Bluetooth [S5](#s5)
- Crash replay shown on its twin [S5](#s5)

### Protection Civile

web · dispatchers

- Live map: crash, people nearby, helpers in red, staff nearby
- Incident card: confidence, severity, status, timeline [S4](#s4)
- New entry from a phone call + severity questions
- Ambulances: availability, assign, destination, ETA; starts the "ligne jaune" alerts
- Call for help: alert helpers, or call one directly
- Patient digital twin
- Hospital suggestion [S2](#s2) · several victims [S7](#s7)

### Police & Gendarmerie

web · filtered by jurisdiction

- Same map, their area only
- Staff and patrols nearby
- Upstream warnings and diversions [S8](#s8)
- Pre-filled accident report [S8](#s8)
- Black-spot statistics [S8](#s8)

### Hospital

web · emergency unit

- Incoming patients with ETA
- Digital twin: body map (M4) + profile (age, chronic diseases, pregnancy…)
- Live vitals [S2](#s2)
- Free beds and specialties, used to pick the hospital [S2](#s2)

### Backend

shared by everything

- Accounts and roles
- Incident engine: confidence score, statuses, merging duplicate reports
- Geo queries: who is near the crash, who is on the ambulance's route
- Push notifications + SMS gateway
- Routing and ETA on OpenStreetMap (OSRM or OpenRouteService)
- AI API serving M1–M4

### Tasks that are easy to forget

Each needs an owner. Size: **S** ≈ 1–2 h, **M** ≈ 3–5 h, **L** ≈ 6 h or more (rough guesses).

| Area | Task | Size |
| --- | --- | --- |
| Data | **Blackbox simulator:** generate accelerometer, gyroscope, GPS speed, seatbelt and airbag streams for normal driving, hard braking, potholes and crashes (frontal, side, rear, rollover, chosen speed). Replay real VZCrash windows where possible. | M |
| Data | **Phone sensor data:** record real drives and phone drops with the Sensor Logger app (false-alarm examples). | S |
| Data | **Datasets:** download and clean VZCrash, nassCDS, CISS (join crash, occupant and injury tables), US-Accidents or STATS19. | M |
| AI | **Train M1–M4 on Kaggle**, evaluate, export the models (joblib or ONNX). | L |
| Backend | AI API (FastAPI) + incident engine (confidence, statuses, merging). | M |
| Backend | Geo queries, routing and ETA, push notifications, SMS gateway and message format. | M |
| Data | Map layers: M1 risk per road segment (OSM), dead-zone map (OpenCelliD). | M |
| Mobile | App screens: sign-up, drive mode, countdown, alerts, helper mode, offline. | L |
| Web | Three role dashboards sharing one map component. | L |
| Demo | **Seed data** for one wilaya: users with profiles, helpers, ambulances, hospitals, staff. | S |
| Demo | **Scenario panel**: crash type, network on/off, witnesses yes/no [S1](#s1) | M |
| Demo | Pitch deck, backup demo video, two full rehearsals. | M |

## 4AI models: what exists, data, papers

Short version: M3 and M4 are real AI with published research and public data behind them. The blackbox detects crashes with rules; ML helps on the phone. M1 is ML trained on foreign data and applied to Algerian roads. Everything trains in a Kaggle notebook; only the VZCrash model needs the GPU.

### M1 · Dangerous roads

AI: yes, after a simple baseline

Exists

**Waze crash-history alerts** (since Nov 2023) combine past crashes, road traits (traffic, elevation, highway or local) and user reports. Waze skips roads you drive every day and limits how many alerts it shows; we should copy both. Research: Moosavi et al. 2019, the US-Accidents paper, predicts crash risk from weather, time and road features.

Need AI?

Start with a black-spot heatmap of past crashes. ML adds weather, time and road context on top.

Datasets

- **US-Accidents** (Kaggle): 7.7M US crashes, 2016–2023, with weather and road features (junction, signal, crossing…). Careful: its "Severity" column measures traffic delay, not injuries.
- **UK STATS19** (data.gov.uk): every police-reported crash with road type, speed limit, junction, light, weather, road surface, urban/rural and casualty severity. Very clean.
- **For Algeria** at prediction time: OSM roads (Geofabrik Algeria extract), Open-Meteo weather API (free), time features (night, weekend, Ramadan pre-iftar, holidays).
- Later: near-misses and hard braking from our own blackboxes.
- Skip the generic "road accident" datasets on Kaggle; many are synthetic.

Plan

LightGBM on crash vs. sampled non-crash moments → risk per road segment and hour → precomputed map layer. In the pitch: patterns learned from US/UK data, applied to Algerian roads and weather, improving with our own data.

### M2 · Crash detection

AI: not on the blackbox; useful on the phone

Exists

**Apple Crash Detection** (iPhone 14+, Apple Watch): ML on a high-g accelerometer, gyroscope, barometer, GPS and microphone. **Google Pixel** has car crash detection too. **EU eCall** (mandatory in new cars since 2018) triggers from the airbag system, with no AI. Car event recorders use one rule: speed change of 8 km/h or more within 150 ms (US rule 49 CFR 563).

Need AI?

**Blackbox: no.** It's fixed to the car, so physics rules work: g-force peak + ΔV + airbag or rollover + car stopped afterwards. **Phone: ML helps**, because phones drop, bounce and fly around the cabin. **Fusion:** weighted rules, so every alert can be explained [S4](#s4).

Datasets

- **VZCrash** (Hugging Face, 2026): 31,000+ real crashes and 158,000 negatives (near-misses, normal driving) from devices in 73,010 commercial vehicles. 100 Hz accelerometer and gyroscope, 1 Hz GPS speed, 16 s windows. This is real blackbox data. Gated (accept the terms), CC BY-NC (fine for a hackathon), 7.7 GB Parquet, so load a subset.
- **Phone false alarms:** no good public dataset exists. Record our own with Sensor Logger (drops, potholes, hard braking).

Plan

Rules first (about 1 h). Then a small classifier (gradient boosting on window features, or a 1D-CNN) trained on VZCrash. Put "rules vs. AI: false-alarm rate" on a slide.

### M3 · Severity of the person

AI: yes, our strongest AI story

Exists

- **OnStar's injury severity prediction** (Kononen, Flannagan & Wang 2011, *Accident Analysis & Prevention*): logistic regression. Top predictors: ΔV, seatbelt, crash direction, plus multiple impacts and an occupant aged 55+.
- **D-Call Net** (Japan, Nishimoto et al.): sends the probability of serious or fatal injury, from the car's ΔV and impact direction; used to dispatch doctor helicopters.
- A 2026 paper (*Traffic Injury Prevention*) trained on NASS-CDS + CISS (17,045 occupants) and beat the OnStar algorithm.

Datasets

- **nassCDS** (Rdatasets): 26,217 occupants in one clean CSV: ΔV band, seatbelt, airbag deployed, frontal impact, age, sex, seat, injury severity, dead or alive. Trainable within the hour.
- **NHTSA CISS** (2017 onward) and **NASS-CDS** (1988–2015): same idea with many more features (recorder ΔV, impact direction, rollover). Free, but the tables must be joined.

Plan

Inputs = what the blackbox measures (ΔV, direction, rollover, belt, airbag) + profile (age, sex). Output = low, medium, high, extreme. Profile flags (pregnancy, heart disease, blood thinners) raise the priority by rule.

### M4 · Injured body parts

AI: yes, multi-label classifier

Papers

- **Weaver et al. 2015**, "Estimated injury risk for specific injuries and body regions in frontal motor vehicle crashes", *Traffic Injury Prevention* 16(S1):S108–16. Logistic models on NASS-CDS give the risk of AIS 2+ to 5+ injury per body region.
- **Stitzel et al. 2016**, "An injury severity-, time sensitivity-, and predictability-based advanced automatic crash notification algorithm improves motor vehicle crash occupant triage", *J. Am. Coll. Surg.* Predicts specific injuries from crash data and turns them into a triage decision.
- **Cluster analysis of seriously injured occupants** (*Accident Analysis & Prevention*, NASS-CDS 2000–2015): injured body regions cluster by crash type.

Datasets

**CISS / NASS-CDS injury tables.** Every injury has an AIS code whose first digit is the body region (1 head, 2 face, 3 neck, 4 thorax, 5 abdomen, 6 spine, 7 arms, 8 legs). Label each occupant "region injured at AIS 2+", then join with the crash table for the features. NHTSA's CIREN has fewer cases in more detail.

Plan

8 yes/no outputs, same inputs as M3. If accuracy is weak, fall back to rules from the papers (frontal → head, chest, legs; side impact → chest, pelvis, head; rollover → head, spine). One more reason to keep M3 and M4 apart: M3 can train on the bigger, simpler nassCDS, while M4 needs the detailed injury tables.

### Parts that look like AI but need no training

- **Voice trigger:** Vosk (offline speech recognition, has French and Arabic models) or Android's offline recognizer. Test Darija keywords early and keep the words it actually catches.
- **Triage questions** (conscious? breathing? bleeding? trapped?): rules, as in START triage.
- **Hospital choice** [S2](#s2): a scoring rule.
- **Routing and ETA:** OSRM or OpenRouteService.

## 5Suggestions

Checked against our handwritten problem list: #7 languages, #9 non-drivers, #10 many victims and #11 feedback aren't covered yet. #4 tow trucks, #6 broken phone, #8 night and #12 awareness are only partly covered. The fixes below are tagged with those numbers.

### S1Build for one demo story, fake the rest

The jury sees about 5 minutes. Write the story first, for example: *02:13, RN5 near Bouira, a car hits the guardrail at 90 km/h, the driver is unconscious, nobody around.* Build only what that story shows; the rest goes on slides. Our flow is a 2×2 (network yes/no × people nearby yes/no), so a scenario panel that flips those switches live shows all four branches in one demo.

**A possible cut** (your call):

- **Must:** blackbox simulator + M2 rules · countdown + silent alert · M3 · Protection Civile map, incident card, ambulance + ETA · hospital twin (M4 + profile) · witness confirm · SMS fallback
- **Should:** M1 warnings · call for help to helpers · hospital choice + vitals (S2) · police and Gendarmerie view
- **Could:** dead-zone timer · voice trigger · Bluetooth relay · KPIs · survival guide

### S2Add vital signs and choose the right hospital

- **Vital signs:** simulate a smartwatch (heart rate, SpO₂) paired with the app, shown live on the twin and in the ambulance. It also earns the name "digital twin": a twin updates live, a static profile doesn't.
- **Right hospital, not nearest:** score hospitals by injury type from M4 (head → neurosurgery, burns → burn unit, pregnancy → maternity), free beds and ETA. Hospitals update their beds in their dashboard.

### S3Never ask the driver to tap

A pop-up asking drivers to confirm or deny is a distraction, which is exactly what we're fighting. Send confirm/deny only to passengers, pedestrians and stopped users; drivers get voice-only warnings. Same for the dead-zone timer: compute the deadline from the route ETA and let the driver accept it by voice.

### S4A confidence ladder instead of "100%"

One witness can be wrong (another crash, a prank). Use statuses: **Suspected → Likely → Witness-confirmed → Confirmed by services**. Only the dispatcher confirms or closes; a "deny" lowers the score but never cancels.

| Score | What happens |
| --- | --- |
| \< 40 | Log it; quiet "Are you OK?" on the user's phone |
| 40–70 | Ask the user and nearby witnesses; grey pin on the dispatcher's map |
| 70–90 | Yellow pre-alert to the dispatcher; the 30 s countdown starts |
| 90+ | Red alert to services; warnings to cars behind |

Example weights: blackbox impact +40, airbag or rollover +30, phone impact +15, sudden stop +15, no movement afterwards +10. The SOS button, the voice trigger or a witness confirmation jump straight to red; "I'm OK" drops it to the bottom. Weighted rules are enough, and a jury can follow them.

### S5Make the blackbox our own eCall

Give it its own SIM, GPS and backup battery, like the EU's eCall. It still alerts when the phone is broken (#6). It pairs over Bluetooth with every phone in the car, so it knows who is inside and the hospital gets each occupant's profile (#10). Its twin in the app shows battery, GPS and signal, live g-force and speed, a trip score, and after a crash a replay of the seconds before and after, like a car's event recorder.

### S6A ladder for no network

Mobile data → SMS (works on weak 2G) → Bluetooth or LoRa relay: passing cars with our app or blackbox pick up the SOS and forward it once they have coverage → satellite SOS on newer phones (later). One SMS holds everything in 160 characters:

```
SOS|36.3741,3.9012|02:13|C85|SEV:HIGH|OCC:2|O+|ID4821
```

Android apps can send SMS silently; iPhone apps can't, which is another reason the blackbox's SIM should be the sender. For the demo, an Android phone running an SMS-gateway app receives the messages.

**Dead-zone map:** OpenCelliD (open cell-tower database), operators' coverage maps, and signal strength logged by our own users. When the timer runs out, warn the emergency contacts first, then the Gendarmerie with last position, route and car description.

### S7Bystander reports and crashes with many victims

- **Bystander report** (#9: pedestrians, motorcyclists, victims without the app): one tap → automatic location → four questions (conscious? breathing? bleeding? trapped?) + photo → severity estimate.
- **Merge duplicates:** reports from the same place within a few minutes become one incident.
- **Many victims** (#10): each victim gets a triage color (green, yellow, red, black, as in START triage) and their own twin. In crashes like the Boumerdès bus (July 2026, 27 dead), spread the injured across hospitals so one emergency room isn't flooded.

### S8What the police and Gendarmerie see

Jurisdiction: police in cities, Gendarmerie outside. Their view: securing the scene (trigger upstream warnings and diversions), staff and patrols nearby, an accident report pre-filled from the blackbox (speed before impact, impact direction, time, weather), and black-spot statistics for prevention, which also feed M1.

### S9Guidance cards for helpers

The dispatcher pushes step-by-step cards to the helper's phone. The first card is always scene safety (hazard lights, stay off the road, no smoking near fuel), then: stop bleeding, recovery position, don't move the neck, don't remove a motorcyclist's helmet. The same cards make up the offline survival guide (#12).

### S10A feedback loop

Timestamp every step: crash → alert → dispatch → on scene → hospital. That gives response times per wilaya for a dashboard and for the pitch (#11). Victims or families rate the response afterwards. Confirmed and denied alerts become new training data for M2.

### S11Small fixes with a big effect

- Arabic, Darija, French and Tamazight in the interface and voice keywords (#7).
- Loud alarm and flashlight during the 30 s countdown, for night crashes (#8).
- "I'm fine, the car isn't" flow: hazard lights, warning triangle, get off the road, call a tow truck, e-constat for insurance (#4, #12).
- Show the location as "RN5, km 112, towards Bouira", not only coordinates (#1).
- Send "accident ahead" and "libérez la ligne jaune" only to cars heading toward the crash or along the ambulance's route, not to everyone in a radius.
- Run sensors and voice listening only in drive mode (blackbox connected or speed above about 20 km/h) to save battery.
- Add blood type, allergies and medications (blood thinners matter) to sign-up.

### S12Work even if the services never install our platform

Android apps can't auto-dial emergency numbers (they can only open the dialer), and iPhone apps can't place any call without a tap. So the blackbox's own SIM calls 14 and plays a recorded message with location and severity in Arabic and French, the way eCall and Apple's crash detection do. The phone's voice trigger raises the alert on the platform and auto-calls an emergency contact instead. This answers the likely jury question: "what if the Protection Civile never adopts your platform?"

### S13Trust, privacy and the law

Verify helpers' certificates (Protection Civile and Croissant-Rouge first-aid courses). Health data is "sensitive" under Law 18-07 (amended in 2025): explicit consent at sign-up, encrypted storage, visible to services only during an active incident. Share location only in drive mode or helper mode. The penal code (art. 182) already punishes failing to help a person in danger; we give people the means to help.

### S14For the pitch

Open with "about 10 deaths a day on Algerian roads (DNSR, 2025)". Use the brief's own words: a decision-support layer that complements 14, 1055 and 17. Cite real precedents: EU eCall, Apple crash detection, OnStar's injury prediction, Japan's D-Call Net, PulsePoint and GoodSAM (apps that alert trained volunteers nearby). End with minutes saved per step. If asked about money: insurers subsidize the blackbox in exchange for safe-driving discounts; fleets (buses, trucks, taxis); a license for the state.

Sources and links

#### Algeria

- [2025 national toll, DNSR (Radio Algérie)](https://news.radioalgerie.dz/fr/node/80144) · [2025 urban toll, DGSN](https://news.radioalgerie.dz/fr/node/83244)
- Emergency numbers: [Wikipedia](https://en.wikipedia.org/wiki/Telephone_numbers_in_Algeria) · [1055 and 1548 (Algérie360)](https://www.algerie360.com/ils-sont-mis-a-la-disposition-des-citoyens-par-la-gn-et-la-dgsn-10-55-et-15-48-ces-numeros-qui-sauvent/) · [Canada travel advice](https://voyage.gc.ca/assistance/ambassades-consulats/algerie)
- [Boumerdès bus crash, July 2026](https://www.lalibre.be/dernieres-depeches/2026/07/31/algerie-un-autocar-tombe-dans-un-ravin-le-bilan-salourdit-a-27-morts-MPP2XHC4E5GX5HHXKTSHM4KPLE/)
- [Law 18-07 overview (Gide)](https://www.gide.com/en/news-insights/protection-of-personal-data-focus-on-algerian-regulations/)

#### Products and rules

- [Waze crash-history alerts (TechCrunch)](https://techcrunch.com/2023/11/07/waze-crash-history-alerts/)
- [Event recorder trigger rule, 49 CFR 563](https://www.ecfr.gov/current/title-49/subtitle-B/chapter-V/part-563)
- [Android: apps can't call emergency numbers directly](https://developer.android.com/reference/android/content/Intent#ACTION_CALL)

#### Datasets

- VZCrash: [Hugging Face](https://huggingface.co/datasets/vzc-research-chapter/VZCrash) · [paper](https://arxiv.org/abs/2606.06074)
- US-Accidents: [Kaggle](https://www.kaggle.com/datasets/sobhanmoosavi/us-accidents) · [Moosavi et al. 2019](https://arxiv.org/abs/1909.09638)
- [UK STATS19 road safety data](https://www.data.gov.uk/dataset/cb7ae6f0-4be6-4935-9277-47e5ce24a11f/road-safety-data)
- nassCDS: [description](https://vincentarelbundock.github.io/Rdatasets/doc/DAAG/nassCDS.html) · [CSV](https://vincentarelbundock.github.io/Rdatasets/csv/DAAG/nassCDS.csv)
- [NHTSA CISS](https://www.nhtsa.gov/crash-data-systems/crash-investigation-sampling-system) · [NHTSA NASS](https://www.nhtsa.gov/crash-data-systems/national-automotive-sampling-system)
- [OSM Algeria (Geofabrik)](https://download.geofabrik.de/africa/algeria.html) · [Open-Meteo](https://open-meteo.com) · [OpenCelliD](https://opencellid.org)

#### Papers

- [Kononen, Flannagan & Wang 2011](https://swov.nl/nl/publicatie/identification-and-validation-logistic-regression-model-predicting-serious-injuries)
- [Serious injury prediction with trainable B-splines (2026)](https://pubmed.ncbi.nlm.nih.gov/42102299/)
- D-Call Net: [ESV paper](https://www-esv.nhtsa.dot.gov/Proceedings/26/26ESV-000179.pdf) · [algorithm version 2021](https://tech.jsae.or.jp/paperinfo/en/content/p202101.352/)
- [Weaver et al. 2015](https://www.tandfonline.com/doi/full/10.1080/15389588.2015.1012664) · [Stitzel et al. 2016](https://www.researchgate.net/publication/301772726_An_Injury_Severity_Time_Sensitivity_and_Predictability_Based_Advanced_Automatic_Crash_Notification_Algorithm_Improves_Motor_Vehicle_Crash_Occupant_Triage) · [Cluster analysis of injured occupants](https://www.sciencedirect.com/science/article/abs/pii/S0001457520316079)

#### Tools

- [Vosk models](https://alphacephei.com/vosk/models) · [Sensor Logger](https://www.tszheichoi.com/sensorlogger) · [OSRM](https://project-osrm.org) · [OpenRouteService](https://openrouteservice.org) · [SMS Gateway for Android](https://github.com/capcom6/android-sms-gateway)