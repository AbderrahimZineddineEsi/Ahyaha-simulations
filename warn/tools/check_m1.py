"""
Checks that the JavaScript M1 gives the same answers as the Python model.

    python warn/tools/check_m1.py       # needs node

Sends 5,000 random rows (some blank, some with unusual codes) and the MODEL_SPEC example to both.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
CONFIG = json.loads((ROOT / "m1" / "models" / "risk_model_nohist.json").read_text())
FEATURES, CATS = CONFIG["features"], CONFIG["categorical"]
TOLERANCE = 1e-6

SPEC_EXAMPLE = {"catr": 2, "plan": 1, "vma": 90, "int": 3, "agg": 1, "circ": 2, "nbv": 2, "prof": 1, "infra": 0,
                "hour": 23, "weekday": 6, "month": 8, "is_weekend": 1, "is_dark": 1,
                "rain_mm": 0, "rain_3h_mm": 0, "is_rain": 0, "is_fog": 0, "is_snow": 0, "is_storm": 0}

JS = r"""
const M1 = require(process.argv[2]);
const model = new M1(JSON.parse(require('fs').readFileSync(process.argv[3])));
const rows = JSON.parse(require('fs').readFileSync(process.argv[4]));
console.log(JSON.stringify(rows.map(r => model.probability(r))));
"""


def random_rows(booster, n, rng):
    rows = []
    for _ in range(n):
        row = {}
        for name in FEATURES:
            if name in CATS:
                values = booster.pandas_categorical[CATS.index(name)]
                row[name] = float(rng.choice(values + [99.0]))   # 99 is not a known code
            elif name == "vma":
                row[name] = float(rng.choice([20, 30, 50, 70, 80, 90, 110, 130, 400]))
            elif name == "nbv":
                row[name] = float(rng.integers(0, 8))
            elif name == "hour":
                row[name] = float(rng.integers(0, 24))
            elif name == "weekday":
                row[name] = float(rng.integers(0, 7))
            elif name == "month":
                row[name] = float(rng.integers(1, 13))
            elif name.startswith("is_"):
                row[name] = float(rng.integers(0, 2))
            else:
                row[name] = float(rng.choice([0, 0, 0.4, 2, 9, 30]))
            if rng.random() < 0.05:
                row[name] = None
        rows.append(row)
    return rows


def python_probabilities(booster, rows):
    frame = pd.DataFrame(rows)[FEATURES].astype(float)
    for name, categories in zip(CATS, booster.pandas_categorical):
        frame[name] = pd.Categorical(frame[name], categories=categories)
    return booster.predict(frame)


def main():
    booster = lgb.Booster(model_file=str(ROOT / "m1" / "models" / "risk_model_nohist.txt"))
    rows = [SPEC_EXAMPLE] + random_rows(booster, 5000, np.random.default_rng(7))
    expected = python_probabilities(booster, rows)

    with tempfile.TemporaryDirectory() as tmp:
        rows_file = Path(tmp) / "rows.json"
        rows_file.write_text(json.dumps(rows))
        script = Path(tmp) / "run.js"
        script.write_text(JS)
        out = subprocess.run(["node", str(script), str(ROOT / "warn/static/m1.js"), str(ROOT / "warn/static/m1_model.json"),
                              str(rows_file)], capture_output=True, text=True, check=True).stdout
    got = np.array(json.loads(out))

    worst = float(np.max(np.abs(got - expected)))
    x = (expected[0] / (1 - expected[0])) / 0.25
    print(f"{len(rows)} rows, largest difference {worst:.2e}")
    print(f"MODEL_SPEC example: p = {expected[0]:.3f}, {x:.2f}x average (spec says p = 0.160, 0.76x)")
    if worst > TOLERANCE:
        sys.exit("FAIL: the JavaScript model disagrees with Python")
    print("OK")


if __name__ == "__main__":
    main()
