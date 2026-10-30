"use strict";
// The phone's screens. Draws what the scan decided; makes no decisions of its own.
const PhoneUI = (() => {
  const $ = (id) => document.getElementById(id);
  const COLORS = { low: "#5aa583", medium: "#e8c547", high: "#ee8a2b", "very high": "#c8231b", none: "#8a979c" };
  const LABEL = { low: "Low risk", medium: "Medium risk", high: "High risk", "very high": "Very high risk" };
  const AUTO_COLLAPSE_MS = 12000;
  // Ribbon geometry (SVG units): the car at the bottom, LOOK_M metres ahead at the top
  const CAR_Y = 350, TOP_Y = 22, LOOK_M = 3000, PX_PER_M = (CAR_Y - TOP_Y) / LOOK_M;
  const RX = 131, RW = 38;

  const ICONS = {
    town: '<path d="M6 41V23l9-8 9 8v18z"/><path d="M24 41V27l9-8 9 8v14z"/><path d="M12 41v-8h6v8M30 41v-7h6v7"/>',
    junction: '<path d="M18 6v14H5M30 6v14h13M18 42V28H5M30 42V28h13"/><path d="M24 12v4M24 32v4" stroke-dasharray="1 5"/>',
    roundabout: '<circle cx="24" cy="24" r="9"/><path d="M24 5v10M24 33v10M5 24h10M33 24h10"/>',
    night_road: '<path d="M32 6a14 14 0 1 0 10 21 11 11 0 0 1-10-21z"/><path d="M9 43l7-14M39 43l-7-14M24 43v-5M24 33v-4"/>',
    night_bends: '<path d="M33 5a10 10 0 1 0 9 15 8 8 0 0 1-9-15z"/><path d="M24 44c-14-5-14-11-6-15s8-9-2-13"/>',
    risky_hours: '<circle cx="24" cy="24" r="17"/><path d="M24 13v11l8 5"/>',
    road: '<path d="M24 6L4 41h40z"/><path d="M24 18v12M24 35v1"/>',
  };
  const COPY = {
    town: { title: "Built-up area ahead", advice: "Pedestrians, junctions and parked cars. Keep to 50 km/h and watch the sides of the road.", voice: "Built-up area ahead. Slow down." },
    junction: { title: "Busy junction ahead", advice: "Slow down and check both sides before you go through.", voice: "Busy junction ahead. Slow down." },
    roundabout: { title: "Roundabout ahead", advice: "Slow down and give way to the traffic already on it.", voice: "Roundabout ahead. Slow down." },
    night_road: { title: "Risky night driving", advice: "The same road is far riskier after dark: tiredness and speed. Slow down, and stop if you feel sleepy.", voice: "Risky night driving ahead. Slow down." },
    night_bends: { title: "Bends in the dark", advice: "Brake before each bend, not in it. Keep to your side and use full beams when the road is clear.", voice: "Bends ahead in the dark. Slow down." },
    risky_hours: { title: "Risky hour on this road", advice: "Accident risk is higher on this road at this hour. Keep your distance and slow down.", voice: "Risky hour on this road. Slow down." },
    road: { title: "Risky stretch ahead", advice: "M1 rates this road above average. Slow down and keep your distance.", voice: "Risky stretch ahead. Slow down." },
  };

  let muted = false;
  let toastTimer = null;
  const live = new Map();     // alert -> {el, details, state, timer}

  const icon = (type, cls) => `<svg class="${cls || ""}" viewBox="0 0 48 48" aria-hidden="true">${ICONS[type] || ICONS.road}</svg>`;
  const fmtKm = (m) => (m < 950 ? `${Math.max(50, Math.round(m / 50) * 50)} m` : `${(m / 1000).toFixed(1)} km`);
  const fmtX = (x) => (x >= 10 ? Math.round(x) : x.toFixed(1)) + "×";
  const escape = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

  function speak(text) {
    if (muted || !("speechSynthesis" in window)) return;
    try {
      speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      u.lang = "en-GB";
      u.rate = 0.95;
      speechSynthesis.speak(u);
    } catch (e) { /* no voice on this device: the screen still warns */ }
  }

  function init() {
    $("p-mute").addEventListener("click", () => {
      muted = !muted;
      $("p-mute").setAttribute("aria-pressed", String(muted));
      $("p-mute").title = muted ? "Voice off" : "Voice on";
      if (muted && "speechSynthesis" in window) speechSynthesis.cancel();
    });
  }

  function reset() {
    live.forEach((v) => clearTimeout(v.timer));
    live.clear();
    $("p-banners").innerHTML = "";
    for (const id of ["p-full", "p-compare", "p-end", "p-toast"]) { $(id).hidden = true; $(id).innerHTML = ""; }
    $("ribbon").innerHTML = "";
    setChip(null);
    $("p-road").textContent = "–";
    $("p-place").textContent = "";
    $("p-speed").textContent = "0";
    if ("speechSynthesis" in window) speechSynthesis.cancel();
  }

  const setClock = (text) => { $("p-clock").textContent = text; };
  const setSpeed = (kmh) => { $("p-speed").textContent = String(Math.round(kmh)); };
  const setRoad = (name, place) => { $("p-road").textContent = name || "Road"; $("p-place").textContent = place || ""; };

  function setChip(item) {
    const chip = $("p-chip");
    if (!item) { chip.innerHTML = "Scanning…"; return; }
    const key = item.level === "very high" ? "very" : item.level;
    chip.innerHTML = `<i class="lv-${key}"></i>${LABEL[item.level]}<small>${fmtX(item.x)} average here</small>`;
  }

  function setScan(progress, text) {
    $("p-bar").style.width = `${Math.round(progress * 100)}%`;
    $("p-scan-text").textContent = text;
  }

  function toast(text, ms = 4000) {
    const el = $("p-toast");
    el.textContent = text;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, ms);
  }

  // The road ahead as a vertical ribbon, coloured only where the phone has scanned
  function renderRibbon({ scored, m, windowEnd, alerts }) {
    const Y = (d) => CAR_Y - (d - m) * PX_PER_M;
    const clipY = (y) => Math.min(CAR_Y, Math.max(TOP_Y, y));
    let svg = `<rect x="${RX}" y="${TOP_Y}" width="${RW}" height="${CAR_Y - TOP_Y}" fill="#dfe5e5"/>`;
    for (let k = 0; k <= 3; k++) {
      const y = CAR_Y - k * 1000 * PX_PER_M;
      svg += `<line x1="104" x2="124" y1="${y}" y2="${y}" stroke="#8a979c"/>` +
             `<text x="98" y="${y + 4}" text-anchor="end" font-size="12" fill="#5d6e76">${k === 0 ? "now" : k + " km"}</text>`;
    }
    let town = null;
    for (const s of scored) {
      const seg = s.seg;
      if (seg.b <= m || seg.a >= m + LOOK_M) continue;
      const y1 = clipY(Y(seg.b)), y2 = clipY(Y(seg.a));
      svg += `<rect x="${RX}" y="${y1.toFixed(1)}" width="${RW}" height="${(y2 - y1 + 0.6).toFixed(1)}" fill="${COLORS[s.level]}"/>`;
      const mid = clipY(Y((seg.a + seg.b) / 2));
      if (seg.int > 1) {
        const g = seg.int === 6 ? "○" : seg.int === 3 ? "T" : "✕";
        svg += `<text x="188" y="${(mid + 4).toFixed(1)}" font-size="13" font-weight="700" fill="#24343d" text-anchor="middle">${g}</text>`;
      } else if (seg.plan > 1) {
        const g = seg.plan === 2 ? "↰" : seg.plan === 3 ? "↱" : "S";
        svg += `<text x="188" y="${(mid + 4).toFixed(1)}" font-size="13" fill="#5d6e76" text-anchor="middle">${g}</text>`;
      }
      if (seg.agg === 2) {
        svg += `<rect x="204" y="${y1.toFixed(1)}" width="4" height="${(y2 - y1).toFixed(1)}" fill="#7a8b94"/>`;
        if (seg.town && seg.town !== town) {
          svg += `<text x="212" y="${(clipY(Y(seg.a)) - 3).toFixed(1)}" font-size="11" fill="#5d6e76">${escape(seg.town)}</text>`;
        }
      }
      town = seg.agg === 2 ? seg.town : null;
    }
    // not scanned yet: the window ends before the top of the ribbon
    if (windowEnd < m + LOOK_M) {
      const y = clipY(Y(windowEnd));
      if (y > TOP_Y) svg += `<rect x="${RX}" y="${TOP_Y}" width="${RW}" height="${(y - TOP_Y).toFixed(1)}" fill="#dfe5e5"/>`;
    }
    svg += `<line x1="${RX + RW / 2}" x2="${RX + RW / 2}" y1="${TOP_Y}" y2="${CAR_Y}" stroke="#fff" stroke-width="2" stroke-dasharray="7 9" opacity=".85"/>`;
    for (const a of alerts) {
      const y1 = clipY(Y(a.b)), y2 = clipY(Y(a.a));
      if (y2 - y1 < 1) continue;
      const color = a.level === "very high" ? "#c8231b" : "#c4660d";
      svg += `<path d="M296 ${y1.toFixed(1)}h6v${(y2 - y1).toFixed(1)}h-6" fill="none" stroke="${color}" stroke-width="3"/>` +
             `<text transform="translate(290 ${((y1 + y2) / 2).toFixed(1)}) rotate(-90)" text-anchor="middle" font-size="11" font-weight="700" fill="${color}">${fmtKm(a.b - a.a)} ${a.level === "very high" ? "very high" : "high"}</text>`;
    }
    svg += `<path d="M150 ${CAR_Y - 10}l9 22h-18z" fill="#2f6fb3" stroke="#fff" stroke-width="2" stroke-linejoin="round"/>`;
    $("ribbon").innerHTML = svg;
  }

  function detailsLine(alert) {
    const now = `${fmtX(alert.peakX)} average at the worst spot`;
    if (alert.causedBy === "time") return `M1: ${now} now, ${fmtX(alert.refX)} by day (Tuesday 11h).`;
    return `M1: ${now}; ${fmtX(alert.refX)} at Tuesday 11h too, so it is the road, not the hour.`;
  }

  function distanceText(alert, m) {
    const length = fmtKm(alert.b - alert.a);
    if (m >= alert.a) return `Risky stretch: ${fmtKm(Math.max(0, alert.b - m))} left`;
    return `in ${fmtKm(alert.a - m)} · ${length} long`;
  }

  function levelWord(alert) { return alert.level === "very high" ? "Very high risk" : "High risk"; }

  function bannerHTML(alert) {
    const c = COPY[alert.type];
    return `${icon(alert.type)}<div><h3>${c.title}</h3><p class="dist"></p><p>${levelWord(alert)}</p></div>` +
           `<button class="x" type="button" aria-label="Dismiss">×</button>` +
           (alert.offer ? `<button class="go" type="button">Look for a different route</button>` : "");
  }

  function addBanner(alert, handlers) {
    const el = document.createElement("div");
    el.className = "banner";
    el.dataset.level = alert.level;
    el.innerHTML = bannerHTML(alert);
    el.querySelector(".x").addEventListener("click", () => remove(alert));
    const go = el.querySelector(".go");
    if (go) go.addEventListener("click", () => handlers.onOffer(alert));
    $("p-banners").appendChild(el);
    return el;
  }

  function fullHTML(alert) {
    const c = COPY[alert.type];
    let safer = "";
    if (alert.noSafer) {
      safer = alert.noAlternative
        ? `<p class="nosafer"><b>No other route from here.</b> Slow down and keep going carefully.</p>`
        : `<p class="nosafer"><b>No safer road.</b> I checked the other route: it has ${fmtKm(alert.compare.alt.riskyKm * 1000)} of high risk against ${fmtKm(alert.compare.main.riskyKm * 1000)} here. Slow down instead.</p>`;
    }
    return `${icon(alert.type, "big")}<h2>${c.title}</h2>` +
           `<p class="where">${levelWord(alert)} · ${alert.a > 0 && alert.startsIn > 0 ? "in " + fmtKm(alert.startsIn) + " · " : ""}${fmtKm(alert.length)} long</p>` +
           `<p class="advice">${c.advice}</p><p class="why">${detailsLine(alert)}</p>${safer}` +
           `<div class="actions">${alert.offer ? `<button class="btn-main" data-act="offer" type="button">Look for a different route</button>` : ""}` +
           `<button class="${alert.offer ? "btn-ghost" : "btn-main"}" data-act="ok" type="button">OK, got it</button></div>`;
  }

  // A new alert: very high takes the whole screen (and the voice), high is a banner
  function show(alert, handlers) {
    const entry = { alert, handlers, el: null, timer: null };
    live.set(alert, entry);
    if (alert.level === "very high") {
      const full = $("p-full");
      full.dataset.level = alert.level;
      full.innerHTML = fullHTML(alert);
      full.hidden = false;
      full.querySelector('[data-act="ok"]').addEventListener("click", () => collapse(entry));
      const offer = full.querySelector('[data-act="offer"]');
      if (offer) offer.addEventListener("click", () => handlers.onOffer(alert));
      entry.full = true;
      entry.timer = setTimeout(() => collapse(entry), AUTO_COLLAPSE_MS);
      speak(COPY[alert.type].voice);
    } else {
      entry.el = addBanner(alert, handlers);
    }
  }

  function collapse(entry) {
    clearTimeout(entry.timer);
    if (!entry.full) return;
    entry.full = false;
    $("p-full").hidden = true;
    $("p-full").innerHTML = "";
    if (live.get(entry.alert) === entry) entry.el = addBanner(entry.alert, entry.handlers);
  }

  function remove(alert) {
    const entry = live.get(alert);
    if (!entry) return;
    clearTimeout(entry.timer);
    if (entry.el) entry.el.remove();
    if (entry.full) { $("p-full").hidden = true; $("p-full").innerHTML = ""; }
    live.delete(alert);
    entry.handlers.onGone && entry.handlers.onGone(alert);
  }

  // Each frame: update the countdown on banners and drop the ones the car has passed
  function update(m) {
    for (const [alert, entry] of [...live]) {
      if (m >= alert.b) { remove(alert); toast("Risky stretch behind you"); continue; }
      if (entry.el) entry.el.querySelector(".dist").textContent = distanceText(alert, m);
    }
  }

  function hideOffer(alert) {
    const entry = live.get(alert);
    if (!entry) return;
    if (entry.el) { const go = entry.el.querySelector(".go"); if (go) go.remove(); }
  }

  function showCompare(alert, { onTake, onKeep }) {
    const o = alert.offer, el = $("p-compare");
    const extra = Math.round(o.alt.minutes - o.main.minutes);
    const extraText = extra === 0 ? "same time" : extra > 0 ? `+${extra} min` : `${extra} min`;
    el.innerHTML = `<h2>Safer route found</h2><p>M1 scored both routes from where they part, at this hour. The other road cuts the high-risk km by ${Math.round(o.cut * 100)}%.</p>` +
      `<div class="route"><h3>Current route</h3><dl><dt>High-risk road</dt><dd>${o.main.riskyKm.toFixed(1)} km</dd><dt>Distance</dt><dd>${o.main.km.toFixed(1)} km</dd><dt>Time</dt><dd>${Math.round(o.main.minutes)} min</dd></dl></div>` +
      `<div class="route safer"><h3>Safer route</h3><dl><dt>High-risk road</dt><dd>${o.alt.riskyKm.toFixed(1)} km</dd><dt>Distance</dt><dd>${o.alt.km.toFixed(1)} km</dd><dt>Time</dt><dd>${Math.round(o.alt.minutes)} min (${extraText})</dd></dl></div>` +
      `<div class="actions"><button class="btn-main" data-act="take" type="button">Take the safer route</button><button class="btn-ghost" data-act="keep" type="button">Keep the current route</button></div>`;
    el.hidden = false;
    $("p-full").hidden = true;
    el.querySelector('[data-act="take"]').addEventListener("click", () => { el.hidden = true; onTake(); });
    el.querySelector('[data-act="keep"]').addEventListener("click", () => { el.hidden = true; onKeep(); });
  }

  function showEnd(lines, onReplay) {
    const el = $("p-end");
    el.innerHTML = `<h2>Trip finished</h2><ul>${lines.map((l) => `<li>${escape(l)}</li>`).join("")}</ul>` +
                   `<div class="actions"><button type="button">Drive it again</button></div>`;
    el.hidden = false;
    el.querySelector("button").addEventListener("click", () => { el.hidden = true; onReplay(); });
  }

  return { init, reset, setClock, setSpeed, setRoad, setChip, setScan, toast, renderRibbon, show, remove, update, hideOffer, showCompare, showEnd, fmtKm, fmtX, COLORS, COPY };
})();
