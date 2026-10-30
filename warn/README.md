# Road warnings: M1 on the phone

A demo of the "warn before you get there" feature. The page is split in two: on the left a map with the car driving, on the right the phone. Every 10 seconds the phone scans the road ahead with **M1** (the real model, running in the page) and pops up a warning when it finds a risky stretch.

```bash
python warn/app.py                 # then open http://localhost:8001
python warn/app.py --host 0.0.0.0  # to open it from a real phone on the same network
```

Needs the M1 weights (`python download_models.py`). The first run exports them to `warn/static/m1_model.json`.

## What happens

1. The car drives a real route near Bouira, 6× faster than life: a 10 s scan is about one minute of road.
2. Each scan, the phone sends its GPS position to the map pack (`GET /api/road-ahead`) and gets the next 3 km as 200 m segments: road type, speed limit, lanes, junctions, curves, slope, built-up or not. These are M1's inputs, taken from OpenStreetMap. The clock gives the hour, weekday, month and whether it is dark.
3. M1 scores every segment in the browser (about a millisecond for 15 segments). The score is "× riskier than an average situation", cut at 0.7 / 1.5 / 3 into low, medium, high and very high.
4. Runs of high or very high segments are stretches. The phone shows:
   - **high:** a banner;
   - **very high:** a full-screen alert with a voice;
   - **short stretch (under 1.5 km):** the warning only;
   - **long stretch:** it also scores the other route from where the two part. If that cuts the high-risk km by 40% or more, it offers *Look for a different route*. Otherwise it says there is **no safer road**.

The warning screen depends on the cause. The phone re-scores the stretch at Tuesday 11h: if it stops being risky, the hour is the cause (night driving, night bends); if it stays risky, the road is (town, junction, roundabout, or a generic risky stretch).

## Situations

The buttons at the top. The **Day** and **Hour** selectors replay the same road at another time.

| Situation | Road and time | What to look for |
|---|---|---|
| Calm drive | RN18, Tuesday 11h | no pop-up |
| Village crossing | RN18 through Ain Laloui, Tuesday 15h | short stretch: a warning, no other route |
| Busy junction | RN18 at Said Abid, Friday 17h | short stretch whose worst spot is a junction |
| Through Bouira | Bouira, Friday 17h | long stretch, the bypass is offered |
| Night on the open road | the calm road, Saturday 02h | long stretch, nothing better |
| Mountain at night | CW98, Saturday 01h | bends in the dark |

The left panel shows what the phone knows: the road is coloured only where it has already scanned, and the table lists every scan with its request and M1's time.

## Be honest about

- **M1 gives one score, not a danger type.** The type is worked out from the road data and a second scoring. M1 itself does not see bends, hills or fog as dangerous: the mountain road is calm by day, and the demo shows it only at night. The weather is always dry.
- **M1 learned on French roads.** Applied here, built-up areas and the hours after dark stand out most; it rates all roads very high at night, even a motorway.
- **The road data comes from OpenStreetMap plus defaults.** Blank fields get a default by road type (50 km/h in town, 80 outside), because a blank speed limit makes M1 call a road much safer. Built-up areas come from `place` points and a radius, not from real boundaries.
- **The map pack is a local server** and the drive is sped up. In the app the road attributes would come from a cached map pack, and the scan about once a minute.

## Files

```
app.py                  server: the page, /api/presets, /api/road-ahead
presets.json            the situations: route, alternative route, clock, expected result
roads.json              road attributes for each route, in M1's codes (built by tools/build_roads.py)
index.html, static/     the page: m1.js (the model), scan.js (what the phone decides), phone.js, map.js, drive.js
tools/export_m1.py      M1 weights -> static/m1_model.json
tools/check_m1.py       the JavaScript model gives the same answers as Python
tools/check_presets.js  runs every situation without a browser and compares with presets.json
tools/build_roads.py    rebuilds roads.json from OSRM, OpenStreetMap (Overpass) and Open-Meteo elevation
tools/fetch_tiles.py    downloads the map tiles, so the demo works offline
```

Checks:

```bash
python warn/tools/check_m1.py       # needs node
node warn/tools/check_presets.js
```
