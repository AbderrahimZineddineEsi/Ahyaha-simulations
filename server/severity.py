"""
M3 (how badly is the person hurt) and M4 (which body regions) at inference time.
Same steps as "Using the models" in MODEL_SPEC.md; the thresholds come from the models' config files.
"""
import json
import math
from pathlib import Path

import joblib
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
BODIES = ("utility", "van", "pickup")  # a car is all three at 0


class Severity:
    def __init__(self):
        self.c3 = json.loads((ROOT / "m3" / "models" / "m3_config.json").read_text())
        self.c4 = json.loads((ROOT / "m4" / "models" / "m4_config.json").read_text())
        self.m3 = joblib.load(ROOT / "m3" / "models" / "m3_lgbm.joblib")
        self.m4 = joblib.load(ROOT / "m4" / "models" / "m4_lgbm.joblib")

    def features(self, case, age, male):
        a = math.radians(case["pdof_deg"])
        row = {"dv_kmh": case["dv_kmh"], "pdof_sin": math.sin(a), "pdof_cos": math.cos(a),
               "rollover": case["rollover"], "multi_impact": case["multi_impact"], "age": age, "male": male,
               **{f"body_{b}": int(case["body"] == b) for b in BODIES}}
        return pd.DataFrame([row])[self.c3["features"]].astype(float)

    def level(self, p):
        t = self.c3["thresholds"]
        return next((name for name in ("extreme", "high", "medium") if p >= t[name]), "low")

    def region_state(self, region, p):
        t = self.c4["thresholds"][region]
        return "likely" if p >= t["likely"] else "check" if p >= t["check"] else "unlikely"

    def assess(self, case, age, male):
        """M3's level and M4's regions, most likely first."""
        x = self.features(case, age, male)
        p3 = float(self.m3.predict_proba(x)[0, 1])
        x4 = x[self.c4["features"]]
        regions = [{"region": r, "p": float(m.predict_proba(x4)[0, 1])} for r, m in self.m4.items()]
        for r in regions:
            r["state"] = self.region_state(r["region"], r["p"])
        regions.sort(key=lambda r: -r["p"])
        return {"m3": {"p": p3, "level": self.level(p3)}, "m4": regions}
