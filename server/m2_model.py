"""
M2 · Crash detection at inference time: loads the ensemble once and scores one 16 s blackbox window.

Same steps as the "Using the models" section of MODEL_SPEC.md: LightGBM on the 43 features, the CNN-RNN
on the standardized channels, then the mean of the two probabilities and the cuts from m2_config.json.
"""
import json
import sys
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import onnxruntime as ort
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "m2" / "models"
sys.path.insert(0, str(ROOT / "m2"))
import m2_blackbox as bb  # noqa: E402


class M2:
    def __init__(self):
        self.config = json.loads((MODELS / "m2_config.json").read_text())
        self.lgbm = lgb.Booster(model_file=str(MODELS / "m2_lgbm.txt"))
        self.cnn = ort.InferenceSession(str(MODELS / "m2_cnn.onnx"), providers=["CPUExecutionProvider"])
        self.ch_mean = np.array(self.config["ch_mean"], np.float32)[None, :, None]
        self.ch_std = np.array(self.config["ch_std"], np.float32)[None, :, None]

    def predict(self, acc, gyro, speed):
        """acc, gyro: (1600, 3). speed: (16,), with NaN for a missing GPS sample."""
        start = time.perf_counter()
        acc, gyro, speed = acc[None], gyro[None], speed[None]

        names, X = bb.window_features(acc, gyro, speed)
        p_lgbm = float(self.lgbm.predict(pd.DataFrame(X, columns=names)[self.config["features"]])[0])

        ch = bb.to_channels(acc, gyro, speed)
        logit = self.cnn.run(None, {"x": (ch - self.ch_mean) / self.ch_std})[0]
        p_cnn = float(1 / (1 + np.exp(-logit.ravel()[0])))

        p_crash = (p_lgbm + p_cnn) / 2
        cuts = self.config["cuts"]
        level = next((k for k in ("crash", "likely", "possible") if p_crash >= cuts[k]), "none")
        return {
            "p_crash": round(p_crash, 6),
            "level": level,
            "p_lgbm": round(p_lgbm, 6),
            "p_cnn": round(p_cnn, 6),
            "latency_ms": round((time.perf_counter() - start) * 1000, 2),
        }
