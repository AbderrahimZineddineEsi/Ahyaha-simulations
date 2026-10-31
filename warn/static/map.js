"use strict";
// The left side: the car on the map, and the road coloured only where the phone has already scanned it.
const MapView = (() => {
  const COLORS = { low: "#2A7F6F", medium: "#D9A400", high: "#C9560F", "very high": "#D21034" };
  // Streets by class (0 motorway, 1 national, 2 secondary, 3 local): drawn from saved OpenStreetMap data, no tiles
  const STREET_STYLE = [
    { color: "#FFFFFF", weight: 4 }, { color: "#FFFFFF", weight: 3.5 }, { color: "#FFFFFF", weight: 3 }, { color: "#FFFFFF", weight: 1.6 },
  ];
  const PLACE_SIZE = ["1.05rem", ".95rem", ".8rem"];
  let map = null, car = null;
  const legs = {};            // leg -> {path, cum, length}
  const painted = {};         // leg -> Map(segment start -> polyline)
  let layer = null;           // everything belonging to the current preset
  let zones = new Map();      // alert -> layers
  let altLine = null;

  async function init() {
    map = L.map("map", { zoomControl: true, attributionControl: true, minZoom: 10, maxZoom: 17, preferCanvas: true });
    map.attributionControl.addAttribution("Streets © OpenStreetMap contributors");
    map.setView([36.37, 3.9], 12);
    document.getElementById("map").style.background = "#F3F0E8";
    try {
      const streets = await fetch("/static/streets.json").then((r) => r.json());
      drawStreets(streets);
    } catch (e) { /* no street data: the route and the car still show */ }
  }

  function drawStreets({ ways, places }) {
    map.createPane("streets").style.zIndex = 350;      // under the route, whenever the data arrives
    const canvas = L.canvas({ padding: 0.3, pane: "streets" });
    const base = L.layerGroup().addTo(map);
    // smallest first so the big roads end up on top
    for (const cls of [3, 2, 1, 0]) {
      for (const [c, pts] of ways) {
        if (c !== cls) continue;
        L.polyline(pts, { ...STREET_STYLE[cls], renderer: canvas, interactive: false, lineCap: "round", lineJoin: "round" }).addTo(base);
      }
    }
    for (const [name, lat, lon, kind] of places) {
      L.marker([lat, lon], {
        icon: L.divIcon({ className: "", html: `<div class="place" style="font-size:${PLACE_SIZE[kind]}">${name.replace(/[<>&]/g, "")}</div>`, iconSize: [0, 0] }),
        interactive: false, keyboard: false,
      }).addTo(base);
    }
  }

  function metres(a, b) {
    const R = 6371000, rad = Math.PI / 180;
    const h = Math.sin((b[0] - a[0]) * rad / 2) ** 2 + Math.cos(a[0] * rad) * Math.cos(b[0] * rad) * Math.sin((b[1] - a[1]) * rad / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(h));
  }

  function prepare(route) {
    const cum = [0];
    for (let i = 1; i < route.path.length; i++) cum.push(cum[i - 1] + metres(route.path[i - 1], route.path[i]));
    return { path: route.path, cum, length: cum[cum.length - 1] };
  }

  function indexAt(leg, d) {
    const { cum } = legs[leg];
    let lo = 0, hi = cum.length - 1;
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (cum[mid] <= d) lo = mid; else hi = mid; }
    return lo;
  }

  function positionOf(leg, d) {
    const { path, cum, length } = legs[leg];
    d = Math.min(Math.max(d, 0), length);
    const i = indexAt(leg, d);
    const span = cum[i + 1] - cum[i];
    const u = span === 0 ? 0 : (d - cum[i]) / span;
    return [path[i][0] + (path[i + 1][0] - path[i][0]) * u, path[i][1] + (path[i + 1][1] - path[i][1]) * u];
  }

  function slice(leg, a, b) {
    const { path, cum } = legs[leg];
    const out = [positionOf(leg, a)];
    for (let i = indexAt(leg, a) + 1; i < path.length && cum[i] < b; i++) out.push(path[i]);
    out.push(positionOf(leg, b));
    return out;
  }

  function load(preset) {
    if (layer) layer.remove();
    layer = L.layerGroup().addTo(map);
    zones = new Map();
    for (const k of Object.keys(legs)) delete legs[k];
    for (const k of Object.keys(painted)) delete painted[k];
    legs.main = prepare(preset.main);
    if (preset.alt) legs.alt = prepare(preset.alt);
    painted.main = new Map();
    painted.alt = new Map();

    const roadLine = (pts, opts) => L.polyline(pts, { lineCap: "round", lineJoin: "round", interactive: false, ...opts }).addTo(layer);
    roadLine(legs.main.path, { color: "#fff", weight: 11, opacity: 0.95 });
    roadLine(legs.main.path, { color: "#8A9189", weight: 6 });
    altLine = null;
    if (preset.alt) {
      altLine = roadLine(legs.alt.path, { color: "#0F766E", weight: 4, opacity: 0.7, dashArray: "2 9" });
      const at = positionOf("main", preset.branch_m);
      L.marker(at, { icon: L.divIcon({ className: "", html: '<div class="flag split">Other route leaves here</div>', iconSize: [0, 0] }), interactive: false }).addTo(layer);
    }
    const end = legs.main.path[legs.main.path.length - 1];
    L.marker(legs.main.path[0], { icon: L.divIcon({ className: "", html: '<div class="flag">Start</div>', iconSize: [0, 0] }), interactive: false }).addTo(layer);
    L.marker(end, { icon: L.divIcon({ className: "", html: '<div class="flag">Destination</div>', iconSize: [0, 0] }), interactive: false }).addTo(layer);
    car = L.marker(legs.main.path[0], { icon: L.divIcon({ className: "", html: '<div class="car-pin"></div>', iconSize: [22, 22], iconAnchor: [11, 11] }), zIndexOffset: 1000, interactive: false }).addTo(layer);
    const all = legs.main.path.concat(preset.alt ? legs.alt.path : []);
    map.fitBounds(L.latLngBounds(all), { padding: [50, 50] });
  }

  function moveCar(leg, m) { car.setLatLng(positionOf(leg, m)); }

  // Colour what the phone has scanned: one line per segment, recoloured if a later scan changes its level
  function paint(leg, scored) {
    const lines = painted[leg];
    for (const s of scored) {
      const key = s.seg.a;
      const color = COLORS[s.level];
      const have = lines.get(key);
      if (have) { if (have.options.color !== color) have.setStyle({ color }); continue; }
      lines.set(key, L.polyline(slice(leg, s.seg.a, s.seg.b), { color, weight: 6, lineCap: "butt", interactive: false }).addTo(layer));
    }
  }

  function addZone(alert) {
    const color = alert.level === "very high" ? "#D21034" : "#C9560F";
    const band = L.polyline(slice(alert.leg, alert.a, alert.b), { color, weight: 18, opacity: 0.28, lineCap: "butt", interactive: false }).addTo(layer);
    const text = PhoneUI.COPY[alert.type].title;
    const label = L.marker(positionOf(alert.leg, (alert.a + alert.b) / 2), {
      icon: L.divIcon({ className: "", html: `<div class="zone-label" style="background:${color}">${text} · ${PhoneUI.fmtKm(alert.b - alert.a)}</div>`, iconSize: [0, 0] }),
      interactive: false,
    }).addTo(layer);
    zones.set(alert, [band, label]);
  }

  function removeZone(alert) {
    (zones.get(alert) || []).forEach((l) => l.remove());
    zones.delete(alert);
  }

  // After the driver leaves the main road, what the phone had scanned beyond the branch fades
  function dimMainAfter(branch_m) {
    painted.main.forEach((line, start) => { if (start >= branch_m) line.setStyle({ opacity: 0.3 }); });
    for (const alert of [...zones.keys()]) if (alert.leg === "main" && alert.a >= branch_m) removeZone(alert);
  }

  function emphasizeAlternative() { if (altLine) altLine.setStyle({ opacity: 1, weight: 6, dashArray: null }); }

  return { init, load, moveCar, paint, addZone, removeZone, positionOf, emphasizeAlternative, dimMainAfter, invalidate: () => map && map.invalidateSize() };
})();
