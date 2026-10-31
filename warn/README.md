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
4. Runs of high or very high segments are stretches. A stretch warns if it has a very high segment, or averages at least 1.8× (a margin over the 1.5× line, so a road hovering at 1.53× doesn't flicker on and off). The phone shows:
   - **high:** a banner;
   - **very high:** a full-screen alert with a voice;
   - **short stretch (under 1.5 km):** the warning only;
   - **long stretch:** it also scores the other route from where the two part, over the rest of the trip, as distance × how many times riskier than average. If the other route carries at least 30% less, it offers *Look for a different route* and shows both routes side by side (average risk, high-risk km, time). Otherwise it says there is **no safer road**, or that there is no other route.

The warning screen depends on the cause. The phone re-scores the stretch at Tuesday 11h: if it stops being risky, the hour is the cause (night driving; night bends when at least 40% of its segments turn 30° or more in 200 m; otherwise a "risky hour"); if it stays risky, the road is (town, a junction or roundabout in a stretch of 650 m or less, or a generic risky stretch). The warning also says what M1 gave now and at Tuesday 11h.

## Presenting it

Each situation starts just before its problem and runs at 40× by default, so one takes 10 to 20 seconds. **Play all** runs the six in order. **Pause** (or Space) freezes the drive; **Pause on each warning** (on by default) stops it the moment a warning pops up, so you can show the screen, then press Resume. The speed selector goes from 6× (the old pace) to 80×.

## Situations

The buttons at the top. The **Day** and **Hour** selectors replay the same road at another time.

| Situation | Road and time | What to look for |
|---|---|---|
| Calm drive | RN18, Tuesday 11h | no pop-up (0.5× to 1×) |
| Village crossing | RN18 through Ain Laloui, Tuesday 15h | 1.4 km at 3×: a built-up warning, no other route |
| Busy junction | RN18 at Said Abid, Friday 17h | 600 m at 3.6×: the junction is named |
| Through Bouira | Bouira, Friday 17h | 3.6 km of city at up to 5×: the A2 motorway is offered (2.2× → 1.2× on average, 2 minutes faster) |
| Night on the open road | straight RN5, Saturday 02h | 5.4 km at 5.7× (0.6× by day), no other route |
| Mountain at night | CW98, Saturday 01h | 9 km of bends at up to 7× (1.4× by day) |

The left panel shows what the phone knows: the road is coloured only where it has already scanned, and the table lists every scan with its request and M1's time.

## Be honest about

- **M1 gives one score, not a danger type.** The type is worked out from the road data and a second scoring. M1 itself does not see bends, hills or fog as dangerous: the mountain road scores 1.4× by day, and the demo shows it only at night. The weather is always dry.
- **M1 is touchy about lane counts.** The A2 motorway scores 1.5× with three lanes and 0.7× with four, so `nbv` is taken as OSM tags it (as MODEL_SPEC says), with 2 when blank, and stretches have to clear a margin before they warn.
- **M1 learned on French roads.** Applied here, built-up areas and the hours after dark stand out most; it rates all roads very high at night, even a motorway.
- **The road data comes from OpenStreetMap plus defaults.** Blank fields get a default by road type (50 km/h in town, 80 outside), because a blank speed limit makes M1 call a road much safer. Built-up areas come from `place` points and a radius, not from real boundaries.
- **The map is drawn from saved OpenStreetMap data, not tiles** (the tile servers forbid downloading tiles for offline use). While the Overpass servers were overloaded, `static/streets.json` was built from the ways saved along the routes only, so side streets away from the routes are missing. Run `python warn/tools/build_streets.py` when Overpass is back for the full street grid.
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
                        (--offline: only what is already in tools/cache/)
tools/build_streets.py  saves the streets and place names around the routes -> static/streets.json (the offline map)
```

Checks:

```bash
python warn/tools/check_m1.py       # needs node
node warn/tools/check_presets.js
```
