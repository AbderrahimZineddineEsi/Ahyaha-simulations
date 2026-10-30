"""
Turns the saved M1 model (LightGBM text file) into compact JSON that the browser can read, so the phone page
runs the real 182-tree model itself.

    python warn/tools/export_m1.py      # m1/models/risk_model_nohist.* -> warn/static/m1_model.json

Per tree: flat node arrays. A child index >= 0 is a node, a negative one is leaf ~i.
Categorical splits keep LightGBM's own codes (the position of the value in the model's category list).
"""
import json
import sys
from pathlib import Path

import lightgbm as lgb

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "m1" / "models" / "risk_model_nohist"
OUT = ROOT / "warn" / "static" / "m1_model.json"


def flatten(tree):
    f, t, flag, left, right, leaves = [], [], [], [], [], []

    def add(node):
        if "split_feature" not in node:
            leaves.append(round(node["leaf_value"], 9))
            return ~(len(leaves) - 1)
        i = len(f)
        f.append(node["split_feature"])
        categorical = node["decision_type"] == "=="
        t.append([int(c) for c in node["threshold"].split("||")] if categorical else node["threshold"])
        # bit 0: categorical, bit 1: a missing value goes left, bit 2: missing values are NaN (not zero)
        flag.append(int(categorical) | (int(node["default_left"]) << 1) | (int(node["missing_type"] == "NaN") << 2))
        left.append(None)
        right.append(None)
        left[i] = add(node["left_child"])
        right[i] = add(node["right_child"])
        return i

    root = add(tree["tree_structure"])
    assert root == 0 or root < 0, root
    return {"f": f, "t": t, "d": flag, "l": left, "r": right, "v": leaves}


def main():
    booster = lgb.Booster(model_file=str(SRC) + ".txt")
    config = json.loads(Path(str(SRC) + ".json").read_text())
    dump = booster.dump_model()
    model = {
        "features": config["features"],
        "categorical": config["categorical"],
        "categories": dict(zip(config["categorical"], booster.pandas_categorical)),
        "base_rate": config["base_rate"],
        "cuts": config["level_cuts_x_vs_average"],
        "levels": config["levels"],
        "test_auc": config["test_auc"],
        "trees": [flatten(t) for t in dump["tree_info"]],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(model, separators=(",", ":")))
    print(f"{len(model['trees'])} trees -> {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    if not Path(str(SRC) + ".txt").exists():
        sys.exit("m1/models is missing: run python download_models.py first")
    main()
