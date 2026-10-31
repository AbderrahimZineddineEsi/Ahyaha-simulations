"use strict";
// Runs the simulation: the car drives the route, the phone scans every few seconds, and the page shows the result on the
// map (left) and the phone (right). The phone side is Scan.Phone, the same code tools/check_presets.js runs.
(async function () {
  const SCAN_EVERY_S = 60;       // the phone scans about once a minute of driving, whatever the speed of the demo
  const FINISH_MARGIN_M = 30;
  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const $ = (id) => document.getElementById(id);

  const S = {
    presets: [], preset: null, clock: null, phone: null, m1: null, m1Bytes: 0,
    leg: "main", m: 0, simSeconds: 0, userPaused: false, comparing: false, scanTimer: 0, scale: 40, autoPause: true, tour: false, tourTimer: 0, busy: false,
    scored: [], windowEnd: 0, alerts: [], pending: false, rerouted: false, finished: false,
    scans: 0, requestsTotal: 0, m1MsTotal: 0, shown: [], lastMs: 0, requestLog: [],
  };

  const running = () => !S.userPaused && !S.comparing && !S.finished;
  const legInfo = () => S.preset[S.leg];

  function speedAt(leg, m) {
    const row = S.preset[leg].speeds.find(([, b]) => b > m) || S.preset[leg].speeds[S.preset[leg].speeds.length - 1];
    return Scan.SPEED_SHARE * row[2];   // km/h
  }

  // The phone's request to the map pack. A fix sent as GPS (lat, lon) is snapped by the server; later calls ask by distance.
  async function fetchRoad({ leg, lat, lon, from_m, length, exact }) {
    const q = new URLSearchParams({ preset: S.preset.id, leg, length: String(Math.round(Math.min(length, 1e8))) });
    if (exact) q.set("from_m", String(Math.round(from_m)));
    else { q.set("lat", lat.toFixed(5)); q.set("lon", lon.toFixed(5)); }
    const url = `/api/road-ahead?${q}`;
    const t0 = performance.now();
    const res = await fetch(url);
    if (!res.ok) throw new Error(`map pack answered ${res.status}`);
    const body = await res.json();
    S.requestLog.push({ url, ms: performance.now() - t0, segments: body.segments.length });
    return body;
  }

  function clockText() {
    const minute = Math.min(59, Math.floor(S.simSeconds / 60));
    return `${DAYS[S.clock.weekday]} ${String(S.clock.hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
  }

  function start(id, clock) {
    const preset = S.presets.find((p) => p.id === id);
    S.preset = preset;
    S.clock = clock ? { ...preset.clock, ...clock } : { ...preset.clock };
    S.phone = new Scan.Phone({
      m1: S.m1, clock: S.clock, fetchRoad, positionOf: MapView.positionOf,
      preset: { id: preset.id, branch_m: preset.branch_m, alt: preset.alt ? true : null },
    });
    clearTimeout(S.tourTimer);
    S.userPaused = false;
    Object.assign(S, { leg: "main", m: preset.demo_start_m || 0, simSeconds: 0, comparing: false, scanTimer: SCAN_EVERY_S, busy: false, scored: [], windowEnd: 0,
                       alerts: [], pending: false, rerouted: false, finished: false, scans: 0, requestsTotal: 0, m1MsTotal: 0, shown: [], lastMs: 0, requestLog: [] });
    location.hash = id;
    for (const b of $("presets").children) b.setAttribute("aria-pressed", String(b.dataset.id === id));
    $("weekday").value = String(S.clock.weekday);
    $("hour").value = String(S.clock.hour);
    $("blurb").textContent = preset.demo;
    $("log").innerHTML = "";
    $("phone-note").textContent = noteText();
    MapView.load(preset);
    PhoneUI.reset();
    PhoneUI.setClock(clockText());
    MapView.moveCar("main", S.m);
    setPlayLabel();
  }

  function noteText() {
    return `M1 runs inside this page: ${S.m1.model.trees.length} trees, ${Math.round(S.m1Bytes / 1024)} KB. The map pack only sends road attributes; ` +
           `the phone works out the risk itself. In real life the scan would run about once a minute.`;
  }

  async function doScan() {
    S.busy = true;
    const phone = S.phone, leg = S.leg, m = S.m;
    S.requestLog = [];
    let r;
    try {
      r = await phone.scan(m);
    } catch (e) {
      S.busy = false;
      if (phone !== S.phone) return;
      addLog({ n: ++S.scans, m, request: "failed", segments: "–", peak: "–", ms: "–", result: "no answer from the map pack: keeping the last scan" });
      PhoneUI.toast("No answer from the map pack");
      return;
    }
    S.busy = false;
    if (phone !== S.phone) return;       // restarted while this scan was running
    S.scored = r.scored;
    S.windowEnd = r.windowEnd;
    S.lastMs = r.m1Ms;
    S.m1MsTotal += r.m1Ms;
    S.requestsTotal += S.requestLog.length;
    MapView.paint(leg, r.scored);
    const peak = r.scored.reduce((a, s) => Math.max(a, s.x), 0);
    const first = S.requestLog[0];
    const result = r.alerts.length
      ? r.alerts.map((a) => `${a.level.toUpperCase()}: ${PhoneUI.COPY[a.type].title}`).join("; ")
      : r.stretches.length ? "risky stretch already announced" : "nothing to warn about";
    addLog({ n: ++S.scans, m, request: first ? `GET ${first.url.replace("/api/road-ahead", "road-ahead")}${S.requestLog.length > 1 ? ` (+${S.requestLog.length - 1} more)` : ""}` : "", segments: `${r.scored.length} (${(r.scored.length * 0.2).toFixed(1)} km)`,
              peak: PhoneUI.fmtX(peak), ms: `${r.m1Ms.toFixed(1)} ms`, result });
    for (const alert of [...r.alerts].sort((a, b) => a.startsIn - b.startsIn)) {
      S.alerts.push(alert);
      S.shown.push(alert);
      MapView.addZone(alert);
      if (S.autoPause) S.userPaused = true;
      PhoneUI.show(alert, { onOffer: openCompare, onGone: (a) => { MapView.removeZone(a); S.alerts = S.alerts.filter((x) => x !== a); } });
    }
    setPlayLabel();
  }

  function addLog({ n, m, request, segments, peak, ms, result }) {
    const tr = document.createElement("tr");
    tr.className = "fresh";
    tr.innerHTML = `<td>${n}</td><td>${(m / 1000).toFixed(1)} km</td><td class="req"></td><td>${segments}</td><td>${peak}</td><td>${ms}</td><td class="res"></td>`;
    tr.querySelector(".req").textContent = request;
    tr.querySelector(".res").textContent = result;
    $("log").prepend(tr);
  }

  function openCompare(alert) {
    S.comparing = true;
    PhoneUI.showCompare(alert, {
      onTake: () => {
        S.comparing = false;
        S.pending = true;
        S.phone.rerouting = true;
        alert.offer.taken = true;
        PhoneUI.hideOffer(alert);
        MapView.emphasizeAlternative();
        const away = Math.max(0, S.preset.branch_m - S.m);
        PhoneUI.toast(`Safer route set: the road changes in ${PhoneUI.fmtKm(away)}`);
      },
      onKeep: () => { S.comparing = false; PhoneUI.hideOffer(alert); },
    });
  }

  function switchToAlternative() {
    S.pending = false;
    S.rerouted = true;
    for (const a of [...S.alerts]) PhoneUI.remove(a);
    S.phone.takeAlternative();
    S.leg = "alt";
    MapView.dimMainAfter(S.preset.branch_m);
    S.scored = [];
    S.scanTimer = SCAN_EVERY_S;          // scan the new road at once
    PhoneUI.toast("Following the safer route");
  }

  function finish() {
    S.finished = true;
    const lines = [];
    lines.push(S.shown.length ? `${S.shown.length} warning${S.shown.length > 1 ? "s" : ""} shown` : "No warnings: nothing on this road worried M1");
    for (const a of S.shown) lines.push(`${a.level}, ${PhoneUI.COPY[a.type].title.toLowerCase()}, ${PhoneUI.fmtKm(a.length)}`);
    if (S.rerouted) lines.push("You took the safer route");
    lines.push(`${S.scans} scans, ${S.requestsTotal} map-pack requests, M1 averaged ${(S.m1MsTotal / Math.max(1, S.scans)).toFixed(1)} ms per scan`);
    PhoneUI.showEnd(lines, () => start(S.preset.id, S.clock));
    setPlayLabel();
    if (S.tour) S.tourTimer = setTimeout(nextInTour, 3500);
  }

  function nextInTour() {
    const i = S.presets.indexOf(S.preset);
    if (i + 1 >= S.presets.length) { setTour(false); return; }
    start(S.presets[i + 1].id);
  }

  function setTour(on) {
    S.tour = on;
    $("tour").setAttribute("aria-pressed", String(on));
    $("tour").textContent = on ? "Stop tour" : "Play all";
    clearTimeout(S.tourTimer);
  }

  function togglePause() {
    if (S.finished) return;
    S.userPaused = !S.userPaused;
    setPlayLabel();
    if (!S.userPaused) setTimeout(PhoneUI.collapseFull, 2500);
  }

  function render(now) {
    const leg = S.leg;
    MapView.moveCar(leg, S.m);
    PhoneUI.setSpeed(speedAt(leg, S.m));
    PhoneUI.setClock(clockText());
    const here = S.scored.find((s) => s.seg.a <= S.m && s.seg.b > S.m);
    PhoneUI.setChip(here || null);
    if (here) PhoneUI.setRoad(here.seg.road, here.seg.town);
    PhoneUI.update(S.m);
    const left = Math.max(0, Math.ceil(SCAN_EVERY_S - S.scanTimer));
    PhoneUI.setScan(Math.min(1, S.scanTimer / SCAN_EVERY_S),
      S.scans ? `Next scan in ${left} s of driving · last scan: M1 took ${S.lastMs.toFixed(1)} ms` : "First scan…");
    if (now - render.last > 100) {
      render.last = now;
      PhoneUI.renderRibbon({ scored: S.scored, m: S.m, windowEnd: S.windowEnd, alerts: S.alerts.filter((a) => a.leg === S.leg) });
    }
  }
  render.last = 0;

  let lastFrame = performance.now();
  function frame(now) {
    const dt = Math.min(0.25, (now - lastFrame) / 1000);
    lastFrame = now;
    if (S.preset && running()) {
      const sim = dt * S.scale;
      const route = legInfo();
      S.m = Math.min(route.length, S.m + (speedAt(S.leg, S.m) / 3.6) * sim);
      S.simSeconds += sim;
      if (S.pending && S.leg === "main" && S.m >= S.preset.branch_m) switchToAlternative();
      S.scanTimer += sim;
      if (S.scanTimer >= SCAN_EVERY_S && !S.busy) { S.scanTimer = 0; doScan(); }
      if (S.m >= route.length - FINISH_MARGIN_M) finish();
    }
    if (S.preset) render(now);
    requestAnimationFrame(frame);
  }

  function setPlayLabel() {
    const paused = S.userPaused && !S.finished;
    $("play").textContent = paused ? "Resume" : "Pause";
    $("play").classList.toggle("paused", paused);
    $("p-paused").hidden = !paused;
  }

  async function boot() {
    PhoneUI.init();
    MapView.init();
    const hours = $("hour");
    for (let h = 0; h < 24; h++) hours.add(new Option(`${String(h).padStart(2, "0")}:00`, String(h)));
    try {
      const [presets, modelText] = await Promise.all([fetch("/api/presets").then((r) => r.json()), fetch("/static/m1_model.json").then((r) => r.text())]);
      S.presets = presets;
      S.m1Bytes = modelText.length;
      S.m1 = new M1(JSON.parse(modelText));
    } catch (e) {
      $("blurb").textContent = "Could not load the presets or the M1 model. Is python warn/app.py running?";
      return;
    }
    for (const p of S.presets) {
      const b = document.createElement("button");
      b.type = "button";
      b.dataset.id = p.id;
      b.textContent = p.name;
      b.addEventListener("click", () => { setTour(false); start(p.id); });
      $("presets").appendChild(b);
    }
    const replay = () => start(S.preset.id, { weekday: Number($("weekday").value), hour: Number($("hour").value) });
    $("weekday").addEventListener("change", replay);
    $("hour").addEventListener("change", replay);
    $("speed").addEventListener("change", () => { S.scale = Number($("speed").value); });
    $("autopause").addEventListener("change", () => { S.autoPause = $("autopause").checked; });
    $("play").addEventListener("click", togglePause);
    $("tour").addEventListener("click", () => { const on = !S.tour; setTour(on); if (on) start(S.presets[0].id); });
    document.addEventListener("keydown", (e) => {
      if (e.code === "Space" && !/^(SELECT|INPUT|TEXTAREA)$/.test(e.target.tagName)) { e.preventDefault(); if (document.activeElement) document.activeElement.blur(); togglePause(); }
    });
    $("restart").addEventListener("click", replay);
    window.addEventListener("resize", MapView.invalidate);
    const wanted = S.presets.find((p) => p.id === location.hash.slice(1)) || S.presets[0];
    start(wanted.id);
    requestAnimationFrame(frame);
    window.__warn = S;       // for tests
  }

  boot();
})();
