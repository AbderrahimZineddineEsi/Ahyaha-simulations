"use strict";
// What the phone does every scan: score the road ahead with M1, find the risky stretches, decide what to show.
// Pure logic, no page code, so the page and tools/check_presets.js run the same thing.
(function (root) {
  const M1 = typeof require !== "undefined" ? require("./m1.js") : root.M1;

  const LOOKAHEAD_M = 3000;      // how far ahead each scan looks
  const EXTEND_M = 3000;         // a stretch that runs past the window: look this much further
  const MAX_AHEAD_M = 9000;
  const LONG_M = 1500;           // from this length a stretch is "long": a different route is worth checking
  const SHORT_JUNCTION_M = 650;  // up to three segments: a short stretch that has a junction in it is a "junction" warning
  const MIN_CUT = 0.3;           // a different route must cut the risk-weighted distance by this much
  const MIN_BRANCH_AHEAD_M = 300;
  const SHARP_BEND_DEG = 30;     // a segment that turns this much in 200 m is a real bend, not a gentle curve
  const BENDY_SHARE = 0.4;       // this share of sharp segments makes a stretch "bends"
  const REF_CLOCK = { weekday: 1, hour: 11 };   // a calm reference time: Tuesday 11h
  const SPEED_SHARE = 0.85;      // the driver's pace as a share of the speed limit
  const HIGH = 2;                // level rank of "high"
  const WARN_MEAN_X = 1.8;       // a stretch with no very high segment must average this: a margin over M1's 1.5 cut, so it doesn't flicker
  const JUNCTIONS = [2, 3, 5];
  const DRY = { rain_mm: 0, rain_3h_mm: 0, is_rain: 0, is_fog: 0, is_snow: 0, is_storm: 0 };

  // Sun height from the date, time and place (NOAA's simple formula). Algeria is UTC+1 all year.
  function sunElevationDeg(month, hour, lat, lon) {
    const dayOfYear = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334][month - 1] + 15;
    const utcHour = hour + 0.5 - 1;
    const g = (2 * Math.PI / 365) * (dayOfYear - 1 + (utcHour - 12) / 24);
    const eqTime = 229.18 * (0.000075 + 0.001868 * Math.cos(g) - 0.032077 * Math.sin(g) - 0.014615 * Math.cos(2 * g) - 0.040849 * Math.sin(2 * g));
    const decl = 0.006918 - 0.399912 * Math.cos(g) + 0.070257 * Math.sin(g) - 0.006758 * Math.cos(2 * g) + 0.000907 * Math.sin(2 * g) - 0.002697 * Math.cos(3 * g) + 0.00148 * Math.sin(3 * g);
    const solarMinutes = utcHour * 60 + eqTime + 4 * lon;
    const hourAngle = (solarMinutes / 4 - 180) * Math.PI / 180;
    const la = lat * Math.PI / 180;
    const sin = Math.sin(la) * Math.sin(decl) + Math.cos(la) * Math.cos(decl) * Math.cos(hourAngle);
    return Math.asin(sin) * 180 / Math.PI;
  }

  // The 20 M1 inputs for one road segment at a moment. `dark` and the clock come from the phone, the road from the map pack.
  function rowFor(seg, clock, dark, weather) {
    return {
      catr: seg.catr, plan: seg.plan, vma: seg.vma, int: seg.int, agg: seg.agg, circ: seg.circ, nbv: seg.nbv,
      prof: seg.prof, infra: seg.infra,
      hour: clock.hour, weekday: clock.weekday, month: clock.month, is_weekend: clock.weekday >= 5 ? 1 : 0, is_dark: dark ? 1 : 0,
      ...DRY, ...weather,
    };
  }

  function scoreSegments(m1, segments, clock, dark, weather) {
    return segments.map((seg) => {
      const { x, level } = m1.score(rowFor(seg, clock, dark, weather));
      return { seg, x, level, rank: M1.rank(level) };
    });
  }

  // Runs of high or very high segments. One calmer segment in the middle doesn't split a run. A run counts when it
  // averages 1.8x or more (so a stretch hovering just over the 1.5x line is not a warning) or has a very high segment.
  function findStretches(scored) {
    const flagged = scored.map((s) => s.rank >= HIGH);
    const runs = [];
    let i = 0;
    while (i < scored.length) {
      if (!flagged[i]) { i++; continue; }
      let j = i;
      while (j + 1 < scored.length && (flagged[j + 1] || (flagged[j + 2] && !flagged[j + 1]))) j++;
      runs.push([i, j]);
      i = j + 1;
    }
    return runs
      .map(([from, to]) => {
        const items = scored.slice(from, to + 1);
        const count = items.filter((s) => s.rank >= HIGH).length;
        const mean = items.reduce((n, s) => n + s.x, 0) / items.length;
        const peak = items.reduce((best, s) => (s.x > best.x ? s : best), items[0]);
        return {
          items, a: items[0].seg.a, b: items[items.length - 1].seg.b, peak, count, mean,
          level: peak.rank >= 3 ? "very high" : "high",
          open: to === scored.length - 1,           // runs on past the end of what we looked at
        };
      })
      .filter((s) => s.level === "very high" || (s.count >= 2 && s.mean >= WARN_MEAN_X));
  }

  // Why is it risky, and so which warning to show. Re-scores the stretch at a calm reference time: if it stops being
  // risky, the time is the cause; if it stays risky, the road is.
  function classify(m1, stretch, weather) {
    const refDark = false;
    const refClock = { ...REF_CLOCK, month: stretch.clock.month };
    const ref = scoreSegments(m1, stretch.items.map((s) => s.seg), refClock, refDark, weather);
    const share = (xs, f) => xs.filter(f).length / xs.length;
    const stayRisky = share(ref, (s) => s.rank >= HIGH);
    const segs = stretch.items.map((s) => s.seg);
    const bendy = share(segs, (s) => s.bend >= SHARP_BEND_DEG);
    const town = share(segs, (s) => s.agg === 2);
    const peakSeg = stretch.peak.seg;
    const peakRef = ref[stretch.items.indexOf(stretch.peak)];
    const short = stretch.b - stretch.a <= SHORT_JUNCTION_M;
    const junctions = segs.map((s) => s.int);
    let type;
    if (stayRisky < 0.5) {
      type = stretch.dark ? (bendy >= BENDY_SHARE ? "night_bends" : "night_road") : "risky_hours";
    } else if (short && junctions.includes(6)) {
      type = "roundabout";
    } else if (short && junctions.some((i) => JUNCTIONS.includes(i))) {
      type = "junction";
    } else {
      type = peakSeg.agg === 2 || town >= 0.5 ? "town" : "road";
    }
    return { type, bendy, town, refX: peakRef.x, refLevel: peakRef.level, causedBy: stayRisky < 0.5 ? "time" : "road" };
  }

  // km times how many times riskier than average: what a trip through these segments adds up to
  const exposure = (scored) => scored.reduce((n, s) => n + (s.seg.b - s.seg.a) / 1000 * s.x, 0);
  const riskyKm = (scored) => scored.filter((s) => s.rank >= HIGH).reduce((n, s) => n + (s.seg.b - s.seg.a), 0) / 1000;
  const minutes = (scored) => scored.reduce((n, s) => n + (s.seg.b - s.seg.a) / (SPEED_SHARE * s.seg.vma * 1000 / 60), 0);

  class Phone {
    // fetchRoad({leg, lat, lon, from_m, length}) -> {from_m, to_m, end, segments, weather}: the map pack, one request per call
    constructor({ m1, preset, clock, fetchRoad, positionOf }) {
      this.m1 = m1;
      this.preset = preset;
      this.clock = clock;
      this.fetchRoad = fetchRoad;
      this.positionOf = positionOf;    // (leg, metres) -> [lat, lon]
      this.leg = "main";
      this.rerouting = false;          // the driver accepted the other route and has not reached the branch yet
      this.announced = [];             // [leg, a, b]: stretches already warned about
      this.requests = 0;
    }

    isDark(latlon) {
      return sunElevationDeg(this.clock.month, this.clock.hour, latlon[0], latlon[1]) < 0;
    }

    // `exact`: ask from this distance along the route. Otherwise the map pack finds where the GPS fix is on the route.
    async ahead(leg, metres, length, exact) {
      this.requests++;
      const [lat, lon] = this.positionOf(leg, metres);
      return this.fetchRoad({ leg, lat, lon, from_m: metres, length, exact });
    }

    // One scan from `metres` along the current leg. Returns what was scored and any new alerts.
    async scan(metres) {
      let m1Ms = 0;   // time spent in M1 itself, not in waiting for the map pack
      const timed = (fn) => { const t = performance.now(); const v = fn(); m1Ms += performance.now() - t; return v; };
      const dark = this.isDark(this.positionOf(this.leg, metres));
      let window = await this.ahead(this.leg, metres, LOOKAHEAD_M, false);
      let weather = window.weather;
      let segments = window.segments;
      if (!segments.length) return { scored: [], stretches: [], alerts: [], metres, dark, m1Ms: 0, requests: this.requests, windowEnd: metres };
      let scored = timed(() => scoreSegments(this.m1, segments, this.clock, dark, weather));
      let stretches = findStretches(scored);
      // A stretch that runs past the window: look further until it ends, so its length is known
      while (stretches.length && stretches[stretches.length - 1].open && !window.end && segments[segments.length - 1].b - metres < MAX_AHEAD_M) {
        window = await this.ahead(this.leg, segments[segments.length - 1].b, EXTEND_M, true);
        segments = segments.concat(window.segments);
        scored = timed(() => scoreSegments(this.m1, segments, this.clock, dark, weather));
        stretches = findStretches(scored);
      }
      const alerts = [];
      for (const stretch of stretches) {
        if (this.rerouting && this.leg === "main" && stretch.a >= this.preset.branch_m) continue;   // a road the driver has chosen not to take
        if (stretch.a - metres > LOOKAHEAD_M) continue;   // seen only while measuring another stretch: announce it when it is near
        stretch.clock = this.clock;
        stretch.dark = dark;
        const known = this.announced.find(([leg, a, b]) => leg === this.leg && stretch.a < b && stretch.b > a);
        if (known) { known[1] = Math.min(known[1], stretch.a); known[2] = Math.max(known[2], stretch.b); continue; }
        this.announced.push([this.leg, stretch.a, stretch.b]);
        const why = classify(this.m1, stretch, weather);
        const length = stretch.b - stretch.a;
        const alert = {
          leg: this.leg, type: why.type, causedBy: why.causedBy, level: stretch.level,
          a: stretch.a, b: stretch.b, length, startsIn: Math.max(0, stretch.a - metres),
          peakX: stretch.peak.x, refX: why.refX, refLevel: why.refLevel, dark, bendy: why.bendy,
          long: length >= LONG_M, offer: null, noSafer: false,
        };
        if (alert.long && this.leg === "main") await this.checkReroute(alert, metres);
        alerts.push(alert);
      }
      return { scored, stretches, alerts, metres, dark, m1Ms, requests: this.requests, windowEnd: segments[segments.length - 1].b };
    }

    // Is there a different route that avoids most of the risky km? Both routes are scored from where they part to the destination.
    async checkReroute(alert, metres) {
      const preset = this.preset;
      if (!preset.alt || preset.branch_m - metres < MIN_BRANCH_AHEAD_M) { alert.noSafer = true; alert.noAlternative = true; return; }
      const dark = alert.dark;
      const [main, alt] = await Promise.all(["main", "alt"].map((leg) => {
        this.requests++;
        const [lat, lon] = this.positionOf(leg, preset.branch_m);
        return this.fetchRoad({ leg, lat, lon, from_m: preset.branch_m, length: 1e9, exact: true });
      }));
      const mainScored = scoreSegments(this.m1, main.segments, this.clock, dark, main.weather);
      const altScored = scoreSegments(this.m1, alt.segments, this.clock, dark, alt.weather);
      const summary = (scored, window) => {
        const km = (window.to_m - window.from_m) / 1000, risk = exposure(scored);
        return { riskyKm: riskyKm(scored), avgX: risk / km, exposure: risk, minutes: minutes(scored), km };
      };
      const mainSum = summary(mainScored, main), altSum = summary(altScored, alt);
      const cut = mainSum.exposure > 0 ? 1 - altSum.exposure / mainSum.exposure : 0;
      const compare = { branch_m: preset.branch_m, cut, main: mainSum, alt: altSum, altScored };
      if (cut >= MIN_CUT) alert.offer = compare;
      else { alert.noSafer = true; alert.compare = compare; }
    }

    // The driver took the other route at the branch point
    takeAlternative() {
      this.leg = "alt";
      this.rerouting = false;
    }
  }

  const api = { Phone, findStretches, classify, scoreSegments, rowFor, sunElevationDeg, riskyKm, exposure, minutes,
                LOOKAHEAD_M, LONG_M, SPEED_SHARE, REF_CLOCK };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Scan = api;
})(typeof window !== "undefined" ? window : globalThis);
