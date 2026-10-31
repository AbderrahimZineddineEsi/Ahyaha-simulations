"use strict";
// The 2D road scenes: how each car moves, how it is drawn, and what the caption says.
// A car here is a plain object: { kind, cruise, color, ctx, scn, x, result, resultAt, fx, ... }
const W = 800, H = 450;       // canvas size in pixels
const S_NEAR = 13, S_FAR = 7;  // pixels per metre in the road scenes: close up, and zoomed out for the approach
let S = S_NEAR;               // the scale of the car being drawn
const CAR_L = 4.5, CAR_W = 1.9;
const IMPACT_S = 8;           // the impact is the 8th second of the 16 s recording
const ALARM = "#D21034", CLEAR = "#006233";
const FONT = "'Geist', system-ui, sans-serif";

// ---------------------------------------------------------------- motion

const smooth = u => { u = Math.min(1, Math.max(0, u)); return u * u * (3 - 2 * u); };

// Hard braking: the car behind starts at 7.27 s, the car ahead 0.8 s earlier. The x-y acceleration of
// examples/trigger_no_crash.json passes 1 g at second 8, and the speeds below match its 1.1 g peak.
const BRAKE = { own: 7.27, lead: 6.5, gap: 19, a: 1.1 * 9.81, aLead: 10 };

function brakeSpeeds(t) {
  const own = (() => {
    if (t < BRAKE.own) return 25;
    const tau = Math.min(t - BRAKE.own, 2);
    const v = 25 - BRAKE.a * (2 / Math.PI) * (1 - Math.cos(Math.PI * tau / 2));
    if (t < BRAKE.own + 2) return v;
    const low = 25 - BRAKE.a * 4 / Math.PI;
    return low + (25 - low) * smooth((t - 11.5) / 3.5);
  })();
  const lead = (() => {
    if (t < BRAKE.lead) return 25;
    const tau = Math.min(t - BRAKE.lead, 2.2);
    const v = 25 - BRAKE.aLead * (2.2 / Math.PI) * (1 - Math.cos(Math.PI * tau / 2.2));
    if (t < BRAKE.lead + 2.2) return v;
    const low = 25 - BRAKE.aLead * 4.4 / Math.PI;
    return low + (40 - low) * smooth((t - 11) / 5);
  })();
  return { own, lead };
}

// Positions are integrated once, in steps of 10 ms, then looked up
const BRAKE_TABLE = (() => {
  const dt = 0.01, n = 3000, own = [0], lead = [BRAKE.gap];
  for (let i = 1; i <= n; i++) {
    const v = brakeSpeeds((i - 0.5) * dt);
    own.push(own[i - 1] + v.own * dt);
    lead.push(lead[i - 1] + v.lead * dt);
  }
  return { dt, own, lead };
})();
const lookup = (table, t) => table[Math.min(table.length - 1, Math.round(t / BRAKE_TABLE.dt))];

function pose(car, now) {
  const idle = { x: car.x, y: 1.75, angle: 0, t: null };
  if (!car.scn) return idle;
  const t = (car.scn.t0 || 0) + (now - car.scn.startAt) / 1000;   // t0: a replay that skips the start of the drive
  if (car.kind === "crash") {
    const wallX = car.scn.x0 + car.cruise * IMPACT_S + CAR_L / 2;
    if (t < IMPACT_S) return { x: car.scn.x0 + car.cruise * t, y: 1.75, angle: 0, t, wallX };
    const tau = t - IMPACT_S;
    const front = wallX - 0.7 * (1 - Math.exp(-6 * tau));
    return { x: front - CAR_L / 2, y: 1.75 + 0.5 * (1 - Math.exp(-2.5 * tau)), angle: 0.3 * (1 - Math.exp(-2.5 * tau)), t, wallX, tau };
  }
  return { x: car.scn.x0 + lookup(BRAKE_TABLE.own, t), y: 1.75, angle: 0, t,
           leadX: car.scn.x0 + lookup(BRAKE_TABLE.lead, t) };
}

function update(car, now, dt) {
  const p = pose(car, now);
  if (car.kind === "brake" && car.scn && p.t >= 18) {  // the scenario is over: back to plain cruising
    car.x = p.x;
    car.scn = null;
    car.leadOn = false;
  } else if (!car.scn && car.kind !== "loop") {
    car.x += car.cruise * dt;
  }
  if (car.kind === "crash" && p.t >= IMPACT_S && !car.impactDone) { car.impactDone = true; spawnImpact(car, p); }
  if (car.kind === "crash" && p.tau > 0.3 && p.tau < 14 && now - car.lastSmoke > 90) {
    car.lastSmoke = now;
    car.fx.push({ kind: "smoke", x: p.wallX - 0.7 - 1.2, y: p.y + (Math.random() - .5), vx: -1 - Math.random(), vy: (Math.random() - .5) * 2.2, age: 0, life: 2.6 });
  }
  car.fx = car.fx.filter(f => (f.age += dt) < f.life);
  for (const f of car.fx) { f.x += f.vx * dt; f.y += f.vy * dt; if (f.kind === "debris") { f.vx *= 0.96; f.vy *= 0.96; f.a += f.spin * dt; } }
  return p;
}

function spawnImpact(car, p) {
  const x = p.wallX - 0.4;
  for (let i = 0; i < 26; i++) {
    const a = Math.PI + (Math.random() - 0.5) * 2.4, v = 3 + Math.random() * 9;
    car.fx.push({ kind: "debris", x, y: p.y + (Math.random() - .5) * 1.4, vx: Math.cos(a) * v, vy: Math.sin(a) * v,
                  a: Math.random() * 6, spin: (Math.random() - .5) * 20, age: 0, life: 0.8 + Math.random() * 1.2,
                  color: ["#ffffff", car.color, "#f5bc00", "#9aa5aa"][i % 4], size: 0.25 + Math.random() * 0.4 });
  }
  car.fx.push({ kind: "flash", x, y: p.y, vx: 0, vy: 0, age: 0, life: 0.35 });
}

// ---------------------------------------------------------------- drawing

const hash = (i, j) => { const n = Math.sin(i * 127.1 + j * 311.7) * 43758.5453; return n - Math.floor(n); };

function drawCar(ctx, px, py, angle, color, opts = {}) {
  const k = opts.scale ?? S;
  const l = CAR_L * k, w = CAR_W * k;
  ctx.save();
  ctx.translate(px, py);
  ctx.rotate(angle);
  ctx.globalAlpha = opts.alpha ?? 1;
  ctx.fillStyle = "rgba(0,0,0,.28)";
  ctx.fillRect(-l / 2 + 3, -w / 2 + 4, l, w);
  ctx.fillStyle = color;
  ctx.beginPath(); ctx.roundRect(-l / 2, -w / 2, l, w, 7); ctx.fill();
  ctx.fillStyle = "rgba(20,30,36,.82)";                      // windscreen and rear window
  ctx.fillRect(l * 0.12, -w / 2 + 4, l * 0.16, w - 8);
  ctx.fillRect(-l * 0.36, -w / 2 + 4, l * 0.1, w - 8);
  ctx.fillStyle = "rgba(255,255,255,.28)";                   // roof
  ctx.fillRect(-l * 0.22, -w / 2 + 4, l * 0.32, w - 8);
  ctx.fillStyle = "#fff6c4";                                 // headlights
  ctx.fillRect(l / 2 - 4, -w / 2 + 2, 4, 6); ctx.fillRect(l / 2 - 4, w / 2 - 8, 4, 6);
  ctx.fillStyle = opts.brake ? "#ff2a1f" : "#8a2a26";        // tail lights
  ctx.fillRect(-l / 2, -w / 2 + 2, 4, 6); ctx.fillRect(-l / 2, w / 2 - 8, 4, 6);
  if (opts.brake) { ctx.fillStyle = "rgba(255,42,31,.35)"; ctx.fillRect(-l / 2 - 12, -w / 2, 12, w); }
  if (opts.hazard && Math.floor(performance.now() / 350) % 2 === 0) {
    ctx.fillStyle = "#ffa000";
    for (const [cx, cy] of [[l / 2 - 2, -w / 2], [l / 2 - 2, w / 2], [-l / 2 + 2, -w / 2], [-l / 2 + 2, w / 2]]) {
      ctx.beginPath(); ctx.arc(cx, cy, 4.5, 0, 7); ctx.fill();
    }
  }
  ctx.restore();
}

function drawRoad(ctx, camX, shake, extras) {
  const sx = wx => (wx - camX) * S + shake.x;
  const sy = wy => H / 2 + wy * S + shake.y;
  ctx.fillStyle = "#5d7a4f";
  ctx.fillRect(0, 0, W, H);
  const first = Math.floor(camX / 14) - 1, last = Math.ceil((camX + W / S) / 14) + 1;
  for (let i = first; i <= last; i++) {                          // trees on both verges
    for (const side of [-1, 1]) {
      const h = hash(i, side);
      if (h < 0.35) continue;
      const wx = i * 14 + hash(i, side * 3) * 10, wy = side * (7 + hash(i, side * 7) * 14), r = 1.1 + h * 1.2;
      ctx.fillStyle = "#47653d"; ctx.beginPath(); ctx.arc(sx(wx), sy(wy), r * S, 0, 7); ctx.fill();
      ctx.fillStyle = "#587a4a"; ctx.beginPath(); ctx.arc(sx(wx) - 3, sy(wy) - 3, r * S * 0.6, 0, 7); ctx.fill();
    }
  }
  ctx.fillStyle = "#34444d";                                    // the road
  ctx.fillRect(0, sy(-3.5), W, 7 * S);
  extras?.beforeLines?.(sx, sy);
  ctx.fillStyle = "#f2f2f2";                                    // edge lines
  ctx.fillRect(0, sy(-3.5) + 3, W, 2); ctx.fillRect(0, sy(3.5) - 5, W, 2);
  ctx.fillStyle = "#f5bc00";                                    // dashed centre line
  const d0 = Math.floor(camX / 9) - 1;
  for (let i = d0; i <= d0 + Math.ceil(W / S / 9) + 2; i++) ctx.fillRect(sx(i * 9), sy(0) - 1, 3 * S, 2);
  extras?.afterLines?.(sx, sy);
  return { sx, sy };
}

function drawBanner(ctx, car, now) {
  const r = car.result;
  if (!r || (!LATCHED.has(r.level) && now - car.resultAt > RESULT_MS)) return;
  const alarm = LATCHED.has(r.level);
  ctx.fillStyle = alarm ? ALARM : CLEAR;
  ctx.fillRect(0, 0, W, 92);
  ctx.fillStyle = "#fff";
  ctx.font = `700 50px ${FONT}`;
  ctx.textBaseline = "middle";
  ctx.fillText(WORDS[r.level], 22, 47);
  ctx.textAlign = "right";
  ctx.font = `500 30px ${FONT}`;
  ctx.fillText(`Crash chance ${(r.p_crash * 100).toFixed(1)}%`, W - 22, 47);
  ctx.textAlign = "left";
}

function drawFx(ctx, car, sx, sy) {
  for (const f of car.fx) {
    const u = f.age / f.life;
    if (f.kind === "smoke") {
      ctx.fillStyle = `rgba(210,215,218,${0.45 * (1 - u)})`;
      ctx.beginPath(); ctx.arc(sx(f.x), sy(f.y), (0.8 + u * 3.2) * S, 0, 7); ctx.fill();
    } else if (f.kind === "flash") {
      ctx.fillStyle = `rgba(255,248,210,${0.9 * (1 - u)})`;
      ctx.beginPath(); ctx.arc(sx(f.x), sy(f.y), (1 + u * 7) * S, 0, 7); ctx.fill();
    } else {
      ctx.save(); ctx.translate(sx(f.x), sy(f.y)); ctx.rotate(f.a);
      ctx.globalAlpha = 1 - u; ctx.fillStyle = f.color;
      ctx.fillRect(-f.size * S / 2, -f.size * S / 2, f.size * S, f.size * S * 0.6);
      ctx.restore();
    }
  }
}

// The approach is filmed from far away so the junction is visible long before the car reaches it,
// then the camera moves in for the impact
function approachZoom(t) {
  if (t === null) return 0;
  if (t < 0.5) return 0;
  if (t < 2.5) return smooth((t - 0.5) / 2);
  if (t < 5.8) return 1;
  if (t < 7.8) return 1 - smooth((t - 5.8) / 2);
  return 0;
}

function drawCrash(car, now, p) {
  const ctx = car.ctx;
  const z = approachZoom(p.t);
  S = S_NEAR + (S_FAR - S_NEAR) * z;
  const camX = p.x - (0.32 + (0.12 - 0.32) * z) * W / S;
  const shake = p.tau !== undefined && p.tau < 0.8
    ? { x: (Math.random() - .5) * 14 * Math.exp(-6 * p.tau), y: (Math.random() - .5) * 14 * Math.exp(-6 * p.tau) } : { x: 0, y: 0 };
  const wallX = p.wallX ?? Infinity;
  const { sx, sy } = drawRoad(ctx, camX, shake, !p.wallX ? null : {
    beforeLines(sx, sy) {                                        // the road it should have turned onto
      ctx.fillStyle = "#34444d";
      ctx.fillRect(sx(wallX - 7), 0, 7 * S, H);
    },
    afterLines(sx, sy) {
      ctx.fillStyle = "#34444d";                                  // no lane lines inside the junction
      ctx.fillRect(sx(wallX - 7), sy(-3.5), 7 * S, 7 * S);
      ctx.fillStyle = "#f2f2f2";
      ctx.fillRect(sx(wallX - 8.5), sy(0.2), 1.1 * S, 3.3 * S);   // stop line, our lane only
      ctx.fillStyle = "#f5bc00";
      for (let wy = -30; wy < 30; wy += 9) ctx.fillRect(sx(wallX - 3.5) - 1, sy(wy), 2, 3 * S);
      ctx.save();                                                 // STOP painted on the road
      ctx.translate(sx(wallX - 12.5), sy(1.75)); ctx.rotate(Math.PI / 2);
      ctx.fillStyle = "rgba(242,242,242,.9)";
      ctx.font = `700 ${Math.round(3.1 * S)}px Arial Narrow, Arial, sans-serif`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText("STOP", 0, 0); ctx.restore();
      ctx.fillStyle = "#f2f2f2";                                  // where the driver should have turned: right, then south
      ctx.fillRect(sx(wallX - 5.25) - 0.3 * S, sy(6), 0.6 * S, 7 * S);
      ctx.beginPath(); ctx.moveTo(sx(wallX - 5.25) - 1.3 * S, sy(13)); ctx.lineTo(sx(wallX - 5.25) + 1.3 * S, sy(13));
      ctx.lineTo(sx(wallX - 5.25), sy(16)); ctx.fill();
      ctx.fillStyle = "#46515a";                                  // buildings behind the wall
      ctx.fillRect(sx(wallX + 1.4), 0, W, H);
      ctx.fillStyle = "#56636b";
      for (let wy = -22; wy < 24; wy += 11) ctx.fillRect(sx(wallX + 3), sy(wy), 14 * S, 8 * S);
      for (let wy = -24, i = 0; wy < 24; wy += 3, i++) {          // the wall, in warning stripes
        ctx.fillStyle = i % 2 ? "#ffffff" : ALARM;
        ctx.fillRect(sx(wallX), sy(wy), 1.4 * S, 3 * S);
      }
    },
  });
  drawFx(ctx, car, sx, sy);
  const wrecked = p.tau !== undefined;
  drawCar(ctx, sx(p.x), sy(p.y), p.angle, car.color, { hazard: wrecked && p.tau > 0.8 });
  drawBanner(ctx, car, now);
}

function drawBrake(car, now, p) {
  const ctx = car.ctx;
  S = S_NEAR;
  const camX = p.x - 0.32 * W / S;
  const { sx, sy } = drawRoad(ctx, camX, { x: 0, y: 0 });
  const t = p.t;
  const leadX = t === null ? p.x + BRAKE.gap : p.leadX;
  if ((car.scn || car.leadOn) && leadX - camX < W / S + 5 && leadX > camX - 5) {
    const braking = t !== null && t > BRAKE.lead && t < BRAKE.lead + 2.9;
    const fade = t !== null && !car.leadWasVisible ? smooth(t / 1.2) : 1;
    drawCar(ctx, sx(leadX), sy(1.75), 0, "#8b2d2d", { brake: braking, alpha: fade });
  }
  const ownBraking = t !== null && t > BRAKE.own && t < BRAKE.own + 2.5;
  drawCar(ctx, sx(p.x), sy(p.y), 0, car.color, { brake: ownBraking });
  drawBanner(ctx, car, now);
}

const RING = { cx: W / 2, cy: H / 2, w: 640, h: 330, r: 100 };
const LOOP_SPEED = 170;  // pixels per second
function ringPoint(s) {
  const { cx, cy, w, h, r } = RING;
  const sx = w - 2 * r, sy = h - 2 * r, q = Math.PI * r / 2, total = 2 * sx + 2 * sy + 4 * q;
  s = ((s % total) + total) % total;
  const parts = [
    [sx, u => ({ x: cx - sx / 2 + u, y: cy - h / 2, a: 0 })],
    [q, u => arc(cx + sx / 2, cy - sy / 2, -Math.PI / 2 + u / r)],
    [sy, u => ({ x: cx + w / 2, y: cy - sy / 2 + u, a: Math.PI / 2 })],
    [q, u => arc(cx + sx / 2, cy + sy / 2, u / r)],
    [sx, u => ({ x: cx + sx / 2 - u, y: cy + h / 2, a: Math.PI })],
    [q, u => arc(cx - sx / 2, cy + sy / 2, Math.PI / 2 + u / r)],
    [sy, u => ({ x: cx - w / 2, y: cy + sy / 2 - u, a: -Math.PI / 2 })],
    [q, u => arc(cx - sx / 2, cy - sy / 2, Math.PI + u / r)],
  ];
  function arc(ax, ay, th) { return { x: ax + r * Math.cos(th), y: ay + r * Math.sin(th), a: th + Math.PI / 2 }; }
  for (const [len, at] of parts) { if (s < len) return at(s); s -= len; }
}

function drawLoop(car, now) {
  const ctx = car.ctx, { cx, cy, w, h, r } = RING;
  ctx.fillStyle = "#6c8a5c"; ctx.fillRect(0, 0, W, H);
  ctx.fillStyle = "#c9d0d2";                                     // blocks inside the loop
  for (const [x, y, bw, bh] of [[190, 110, 150, 90], [360, 110, 110, 90], [490, 110, 120, 90], [190, 240, 110, 100], [320, 240, 150, 100], [490, 240, 120, 100]]) {
    ctx.fillRect(x, y, bw, bh); ctx.fillStyle = "#aeb8bb"; ctx.fillRect(x + 6, y + 6, bw - 12, bh - 12); ctx.fillStyle = "#c9d0d2";
  }
  ctx.strokeStyle = "#34444d"; ctx.lineWidth = 72; ctx.beginPath(); ctx.roundRect(cx - w / 2, cy - h / 2, w, h, r); ctx.stroke();
  ctx.strokeStyle = "#f2f2f2"; ctx.lineWidth = 3; ctx.setLineDash([22, 18]);
  ctx.beginPath(); ctx.roundRect(cx - w / 2, cy - h / 2, w, h, r); ctx.stroke(); ctx.setLineDash([]);
  ctx.fillStyle = "#47653d";
  for (const [x, y] of [[40, 40], [90, 80], [760, 50], [715, 90], [50, 410], [100, 370], [750, 405], [700, 370]]) { ctx.beginPath(); ctx.arc(x, y, 24, 0, 7); ctx.fill(); }
  const p = ringPoint(now / 1000 * LOOP_SPEED);
  drawCar(ctx, p.x, p.y, p.a, car.color, { scale: 12 });
  drawBanner(ctx, car, now);
}

function captionFor(car, p, now) {
  const r = car.result, alive = r && (LATCHED.has(r.level) || now - car.resultAt < RESULT_MS);
  if (alive) {
    const chance = `Crash chance ${(r.p_crash * 100).toFixed(1)}%`;
    return LATCHED.has(r.level) ? [`${WORDS[r.level]}. ${chance}.`, "alarm"] : [`${WORDS[r.level]}. ${chance}. Back to normal driving.`, "clear"];
  }
  if (car.kind === "loop") return ["Normal driving. Nothing triggers.", ""];
  const t = p.t;
  if (t === null) return [car.idleText ?? "Driving normally.", ""];
  // A replay that is sped up after the impact (car.fast) collects the 8 s of data that much sooner
  const fast = car.fast || 1;
  const left = Math.max(0, Math.ceil(IMPACT_S - Math.max(0, t - IMPACT_S) * fast));
  const waiting = t < IMPACT_S + (IMPACT_S + 2) / fast;
  const sped = fast > 1 ? " (sped up for the demo)" : "";
  if (car.kind === "crash") {
    if (t < 3.5) return ["Driving at 73 km/h.", ""];
    if (t < IMPACT_S) return ["A T-junction ahead. The driver does not turn.", ""];
    return waiting ? [`Impact. The crash model is collecting 8 s of data${sped}: ${left} s left.`, ""] : ["No answer from the server.", ""];
  }
  if (t < BRAKE.lead) return ["Driving at 90 km/h behind another car.", ""];
  if (t < IMPACT_S) return ["The car ahead brakes hard. So does the driver.", ""];
  if (waiting) return [`Hard braking passes 1 g. The crash model is checking${sped}: ${left} s left.`, ""];
  return ["Driving at 90 km/h.", ""];
}

function drawScene(car, now, p) {
  if (car.kind === "crash") drawCrash(car, now, p);
  else if (car.kind === "brake") drawBrake(car, now, p);
  else drawLoop(car, now);
}
