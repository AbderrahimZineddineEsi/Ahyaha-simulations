// Drives each preset through the phone's logic without a browser and prints what the phone would show.
//
//     node warn/tools/check_presets.js                 # all presets
//     node warn/tools/check_presets.js night-road      # one preset
//     node warn/tools/check_presets.js --clock 5,2     # replay at another clock (weekday 0=Mon, hour)
//
// Exits with an error when a preset does not give the result written in presets.json ("expect").
const fs = require("fs");
const path = require("path");
const M1 = require("../static/m1.js");
const { Phone, SPEED_SHARE } = require("../static/scan.js");

const WARN = path.join(__dirname, "..");
const presets = JSON.parse(fs.readFileSync(path.join(WARN, "presets.json"))).presets;
const roads = JSON.parse(fs.readFileSync(path.join(WARN, "roads.json")));
const m1 = new M1(JSON.parse(fs.readFileSync(path.join(WARN, "static/m1_model.json"))));

const SCAN_S = 10, TIME_SCALE = 6;

function legOf(id, leg) { return roads[id][leg]; }

function cumulative(path_) {
  const R = 6371000, rad = Math.PI / 180, cum = [0];
  for (let i = 1; i < path_.length; i++) {
    const [a, b] = [path_[i - 1], path_[i]];
    const h = Math.sin((b[0] - a[0]) * rad / 2) ** 2 + Math.cos(a[0] * rad) * Math.cos(b[0] * rad) * Math.sin((b[1] - a[1]) * rad / 2) ** 2;
    cum.push(cum[i - 1] + 2 * R * Math.asin(Math.sqrt(h)));
  }
  return cum;
}

function positionOf(id) {
  const lines = {};
  return (leg, m) => {
    const { path: pts } = legOf(id, leg);
    lines[leg] = lines[leg] || cumulative(pts);
    const cum = lines[leg];
    m = Math.min(Math.max(m, 0), cum[cum.length - 1]);
    let i = 1;
    while (i < cum.length - 1 && cum[i] < m) i++;
    const u = (m - cum[i - 1]) / ((cum[i] - cum[i - 1]) || 1);
    return [pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * u, pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * u];
  };
}

// What /api/road-ahead returns: the segments from `from_m` for `length`
function fetchRoadFor(id) {
  return async ({ leg, from_m, length }) => {
    const segs = legOf(id, leg).segments;
    const out = segs.filter((s) => s.b > from_m && s.a < from_m + length);
    return { from_m, to_m: out.length ? out[out.length - 1].b : from_m, end: !out.length || out[out.length - 1] === segs[segs.length - 1],
             segments: out, weather: { rain_mm: 0, rain_3h_mm: 0, is_rain: 0, is_fog: 0, is_snow: 0, is_storm: 0 } };
  };
}

async function run(preset, clockOverride, acceptReroute) {
  const id = preset.id;
  const clock = clockOverride ? { ...preset.clock, weekday: clockOverride[0], hour: clockOverride[1] } : preset.clock;
  const phone = new Phone({ m1, preset: { ...preset, branch_m: roads[id].branch_m, alt: roads[id].alt }, clock,
                            fetchRoad: fetchRoadFor(id), positionOf: positionOf(id) });
  const segs = () => legOf(id, phone.leg).segments;
  let metres = preset.start_m || 0, t = 0, line = [], alerts = [];
  const total = () => legOf(id, phone.leg).length;
  while (metres < total() - 50) {
    const r = await phone.scan(metres);
    for (const a of r.alerts) {
      const record = { ...a, atKm: metres / 1000 };
      alerts.push(record);
      line.push(`  scan ${String(Math.round(t / SCAN_S)).padStart(2)}  km ${(metres / 1000).toFixed(1).padStart(5)}  ` +
        `${a.level.toUpperCase().padEnd(9)} ${a.type.padEnd(11)} in ${(a.startsIn / 1000).toFixed(1)} km, ${(a.length / 1000).toFixed(1)} km long` +
        `  peak ${a.peakX.toFixed(1)}x now, ${a.refX.toFixed(1)}x at Tue 11h` +
        (a.offer ? `  -> OFFER safer route (average risk ${a.offer.main.avgX.toFixed(1)}x -> ${a.offer.alt.avgX.toFixed(1)}x, ${a.offer.alt.minutes - a.offer.main.minutes >= 0 ? "+" : ""}${(a.offer.alt.minutes - a.offer.main.minutes).toFixed(0)} min)` : "") +
        (a.noSafer ? `  -> NO SAFER ROAD${a.compare ? ` (average risk ${a.compare.main.avgX.toFixed(1)}x vs ${a.compare.alt.avgX.toFixed(1)}x, cut only ${(a.compare.cut * 100).toFixed(0)}%)` : " (no alternative route)"}` : ""));
      if (a.offer && acceptReroute) { record.accepted = true; phone.rerouting = true; }
    }
    // sim drive for one scan interval
    let left = SCAN_S * TIME_SCALE;
    while (left > 0 && metres < total()) {
      const seg = segs().find((s) => s.b > metres) || segs()[segs().length - 1];
      const v = SPEED_SHARE * seg.vma / 3.6;
      const step = Math.min(left, 1);
      metres += v * step;
      left -= step;
      if (phone.leg === "main" && acceptReroute && alerts.some((a) => a.accepted) && metres >= roads[id].branch_m) {
        phone.takeAlternative();
        line.push(`  km ${(metres / 1000).toFixed(1).padStart(5)}  takes the safer route`);
      }
    }
    t += SCAN_S;
  }
  return { line, alerts, seconds: t, clock };
}

(async () => {
  const args = process.argv.slice(2);
  const clockArg = args.indexOf("--clock");
  const clockOverride = clockArg >= 0 ? args.splice(clockArg, 2)[1].split(",").map(Number) : null;
  const only = args[0];
  let failed = 0;
  for (const preset of presets) {
    if (only && preset.id !== only) continue;
    if (!roads[preset.id]) { console.log(`${preset.id}: not in roads.json`); continue; }
    const hasOffer = (await run(preset, clockOverride, false)).alerts.some((a) => a.offer);
    const r = await run(preset, clockOverride, hasOffer);
    const c = r.clock;
    console.log(`\n${preset.id} · ${preset.name} · weekday ${c.weekday} ${c.hour}h · ${(roads[preset.id].main.length / 1000).toFixed(1)} km, ${Math.round(r.seconds)} s`);
    console.log(r.line.length ? r.line.join("\n") : "  no warning");
    const expect = preset.expect;
    if (expect && !clockOverride) {
      const got = r.alerts.map((a) => ({ type: a.type, level: a.level, long: a.long, button: a.offer ? "reroute" : a.noSafer ? "no-safer" : "none" }));
      const ok = expect.alerts.length === got.length && expect.alerts.every((e, i) => Object.entries(e).every(([k, v]) => got[i][k] === v));
      console.log(ok ? "  ✓ matches expected" : `  ✗ expected ${JSON.stringify(expect.alerts)}\n    got      ${JSON.stringify(got)}`);
      if (!ok) failed++;
    }
  }
  if (failed) { console.log(`\n${failed} preset(s) differ from what presets.json expects`); process.exit(1); }
})();
