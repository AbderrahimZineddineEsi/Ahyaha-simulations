"use strict";
// M1 road risk, running on the phone: the real LightGBM model exported by tools/export_m1.py.
// Works in the browser (window.M1) and in Node (require), so the same code is checked against Python.
(function (root) {
  const LEVELS = ["low", "medium", "high", "very high"];

  class M1 {
    constructor(model) {
      this.model = model;
      this.index = Object.fromEntries(model.features.map((name, i) => [name, i]));
      this.codes = {};   // feature index -> Map(value -> code)
      model.categorical.forEach((name) => {
        this.codes[this.index[name]] = new Map(model.categories[name].map((v, i) => [v, i]));
      });
    }

    // row: {feature: number}. A blank (null/undefined/NaN) stays blank: the caller must fill gaps with defaults.
    rawScore(row) {
      const x = this.model.features.map((name, i) => {
        const v = row[name];
        const blank = v === null || v === undefined || Number.isNaN(v);
        const codes = this.codes[i];
        if (!codes) return blank ? NaN : v;
        return blank || !codes.has(v) ? NaN : codes.get(v);
      });
      let sum = 0;
      for (const tree of this.model.trees) {
        let node = 0;
        if (tree.f.length === 0) { sum += tree.v[0]; continue; }
        while (node >= 0) {
          const v = x[tree.f[node]], d = tree.d[node], t = tree.t[node];
          let goLeft;
          if (d & 1) {                       // categorical: a blank or unknown code goes right
            goLeft = !Number.isNaN(v) && t.includes(v);
          } else if (Number.isNaN(v)) {
            goLeft = (d & 4) ? Boolean(d & 2) : 0 <= t;
          } else {
            goLeft = v <= t;
          }
          node = goLeft ? tree.l[node] : tree.r[node];
        }
        sum += tree.v[~node];
      }
      return sum;
    }

    probability(row) { return 1 / (1 + Math.exp(-this.rawScore(row))); }

    // How many times riskier than an average situation: (p / (1 - p)) / (base / (1 - base))
    timesAverage(row) {
      const base = this.model.base_rate;
      return Math.exp(this.rawScore(row)) / (base / (1 - base));
    }

    level(x) {
      const c = this.model.cuts;
      return x >= c["very high"] ? "very high" : x >= c.high ? "high" : x >= c.medium ? "medium" : "low";
    }

    score(row) {
      const x = this.timesAverage(row);
      return { x, level: this.level(x) };
    }
  }

  M1.LEVELS = LEVELS;
  M1.rank = (level) => LEVELS.indexOf(level);

  if (typeof module !== "undefined" && module.exports) module.exports = M1;
  else root.M1 = M1;
})(typeof window !== "undefined" ? window : globalThis);
