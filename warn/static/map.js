"use strict";
// The left side: the car on the map, and the road coloured only where the phone has already scanned it.
const MapView = (() => {
  const COLORS = { low: "#5aa583", medium: "#e8c547", high: "#ee8a2b", "very high": "#c8231b" };
  const EMPTY_TILE = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";
  let map = null, car = null;
  const legs = {};            // leg -> {path, cum, length}
  const painted = {};         // leg -> Map(segment start -> polyline)
  let layer = null;           // everything belonging to the current preset
  let zones = new Map();      // alert -> layers
  let altLine = null;

  function init() {
    map = L.map("map", { zoomControl: true, attributionControl: true, minZoom: 11, maxZoom: 17 });
    L.tileLayer("/static/map/{z}/{x}/{y}.png", { minZoom: 11, maxZoom: 17, maxNativeZoom: 15, errorTileUrl: EMPTY_TILE,
                                                 attribution: "© OpenStreetMap contributors" }).addTo(map);
    map.setView([36.37, 3.9], 12);
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
    roadLine(legs.main.path, { color: "#8a979c", weight: 6 });
    altLine = null;
    if (preset.alt) {
      altLine = roadLine(legs.alt.path, { color: "#2f6fb3", weight: 4, opacity: 0.55, dashArray: "2 9" });
      const at = positionOf("main", preset.branch_m);
      L.marker(at, { icon: L.divIcon({ className: "", html: '<div class="flag split">Other route leaves here</div>', iconSize: [150, 20], iconAnchor: [75, -6] }), interactive: false }).addTo(layer);
    }
    const end = legs.main.path[legs.main.path.length - 1];
    L.marker(legs.main.path[0], { icon: L.divIcon({ className: "", html: '<div class="flag">Start</div>', iconSize: [44, 20], iconAnchor: [22, 28] }), interactive: false }).addTo(layer);
    L.marker(end, { icon: L.divIcon({ className: "", html: '<div class="flag">Destination</div>', iconSize: [80, 20], iconAnchor: [40, 28] }), interactive: false }).addTo(layer);
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
    const color = alert.level === "very high" ? "#c8231b" : "#ee8a2b";
    const band = L.polyline(slice(alert.leg, alert.a, alert.b), { color, weight: 18, opacity: 0.28, lineCap: "butt", interactive: false }).addTo(layer);
    const text = PhoneUI.COPY[alert.type].title;
    const label = L.marker(positionOf(alert.leg, (alert.a + alert.b) / 2), {
      icon: L.divIcon({ className: "", html: `<div class="zone-label" style="background:${color}">${text} · ${PhoneUI.fmtKm(alert.b - alert.a)}</div>`, iconSize: [190, 20], iconAnchor: [95, 30] }),
      interactive: false,
    }).addTo(layer);
    zones.set(alert, [band, label]);
  }

  function removeZone(alert) {
    (zones.get(alert) || []).forEach((l) => l.remove());
    zones.delete(alert);
  }

  function emphasizeAlternative() { if (altLine) altLine.setStyle({ opacity: 1, weight: 6, dashArray: null }); }

  return { init, load, moveCar, paint, addZone, removeZone, positionOf, emphasizeAlternative, invalidate: () => map && map.invalidateSize() };
})();
