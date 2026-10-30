"use strict";
// Shared by the pages: how a prediction is named, and how long it stays on screen
const RESULT_MS = 6000;       // how long a no-crash result stays on screen
const WORDS = { crash: "Crash detected", likely: "Likely crash", possible: "Possible crash", none: "No crash" };
const LATCHED = new Set(["crash", "likely"]);  // stay on screen until the car is reset
