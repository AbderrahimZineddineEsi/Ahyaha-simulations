"""
M3 (how badly is the person hurt) and M4 (which body regions) at inference time, plus the choice of what goes in.
The thresholds come from the models' config files. By default the input is the blackbox's crash_summary() and the
owner's profile; a source hook (the sim installs one) can supply the case instead.
"""
import json
import math

import joblib
import pandas as pd

from backend.config import ROOT

BODIES = ("utility", "van", "pickup")  # a car is all three at 0
DEFAULT_ADULT = {"age": 40, "male": 1}  # MODEL_SPEC.md: no profile means a default adult, and the result is flagged


class Severity:
    def __init__(self):
        self.c3 = json.loads((ROOT / "m3" / "models" / "m3_config.json").read_text())
        self.c4 = json.loads((ROOT / "m4" / "models" / "m4_config.json").read_text())
        self.m3 = joblib.load(ROOT / "m3" / "models" / "m3_lgbm.joblib")
        self.m4 = joblib.load(ROOT / "m4" / "models" / "m4_lgbm.joblib")
        self.source = None  # fn(device_id) -> {"case": {...}, "note": str} or None

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

    def for_incident(self, summary, device_id, victim, body_type="car"):
        """The assessment of one incident: the blackbox's summary (or the source hook's case) and the victim's profile."""
        flags = {}
        if victim is not None and victim.age is not None and victim.sex in ("male", "female"):
            age, male = victim.age, int(victim.sex == "male")
        else:
            age, male = DEFAULT_ADULT["age"], DEFAULT_ADULT["male"]
            flags["default_profile"] = True
        hooked = self.source(device_id) if self.source else None
        if hooked:
            case, note = hooked["case"], hooked.get("note")
        else:
            case = {k: summary[k] for k in ("dv_kmh", "pdof_deg", "rollover", "multi_impact")}
            case["body"], note = body_type, None
        return {**self.assess(case, age, male), "case": case, "case_note": note, "flags": flags}
