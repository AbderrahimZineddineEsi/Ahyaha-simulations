"use strict";
// The detection panel of one car: its status, the 8 s countdown, and the prediction.
const STALE_MS = 3500;    // a car is sending once a second

function createPanel(id) {
  const el = document.createElement("section");
  el.className = "car";
  el.innerHTML = `
    <div class="who"><span class="name"></span><span class="reading"></span></div>
    <h1 class="headline"></h1>
    <p class="detail"></p>
    <div>
      <div class="tape"></div>
      <div class="tape-labels"><span>8 s before the trigger</span><span>8 s after</span></div>
    </div>
    <ul class="history"></ul>`;
  el.querySelector(".name").textContent = id;
  const tape = el.querySelector(".tape");
  const cells = [];
  for (let i = 0; i < 17; i++) {
    const cell = document.createElement("i");
    if (i === 8) cell.className = "trigger"; else cells.push(cell);
    tape.append(cell);
  }
  const car = { el, cells, status: null, result: null, resultAt: 0, seenAt: 0, history: [] };
  return car;
}

function percent(p) {
  return p >= 0.9995 ? "over 99.9%" : p < 0.001 ? "under 0.1%" : `${(p * 100).toFixed(1)}%`;
}

function render(car) {
  const { el, status, result, resultAt, seenAt, cells } = car;
  const now = Date.now();
  const stale = now - seenAt > STALE_MS;
  const collecting = status?.state === "collecting" && !stale;
  const showResult = result && !collecting && (LATCHED.has(result.level) || now - resultAt < RESULT_MS);

  let mood = "watching", headline = "No trigger detected", detail = "Watching every second of data";
  let held = 0;
  if (collecting) {
    mood = "collecting";
    headline = `Trigger: collecting ${status.remaining_s} s`;
    detail = `Holding the previous ${status.before_s} s. Prediction in ${status.remaining_s} s.`;
    held = status.before_s + (8 - status.remaining_s);
  } else if (showResult) {
    mood = LATCHED.has(result.level) ? "alarm" : "clear";
    headline = WORDS[result.level];
    detail = `Crash chance ${percent(result.p_crash)}.`;
    if (result.filled_s > 0) detail += ` ${result.filled_s} s of the window never arrived.`;
    if (stale) detail += " This car has stopped sending.";
    held = 16;
  } else if (stale) {
    mood = "stale";
    headline = "No data from this car";
    detail = "The last message arrived over 3 s ago";
  }

  el.dataset.mood = mood;
  el.querySelector(".headline").textContent = headline;
  el.querySelector(".detail").textContent = detail;
  el.querySelector(".reading").textContent = status ? `Second ${status.seq}, peak ${status.peak_g.toFixed(2)} g` : "";
  cells.forEach((cell, i) => cell.classList.toggle("held", i < held));
  el.querySelector(".history").replaceChildren(...car.history.slice(0, 3).map(text => {
    const li = document.createElement("li");
    li.textContent = text;
    return li;
  }));
}

function applyToPanel(car, m) {
  car.seenAt = Date.now();
  if (m.type === "status") {
    car.status = m;
  } else if (m.type === "prediction") {
    if (car.result?.prediction_id !== m.prediction_id) {
      car.history.unshift(`Trigger at second ${m.trigger_seq}: ${WORDS[m.level].toLowerCase()}, ${percent(m.p_crash)}`);
    }
    car.result = m;
    car.resultAt = Date.now();
    car.status = { ...car.status, state: "watching" };
  }
  render(car);
}
