"""Pipeline step 3 - Evaluate: score the trained model on the held-out test slice (Mar 16-31).

Writes evaluation.json. The Condition step reads `regression.mae` out of it with JsonGet -
that file is the contract between this step and the decision.
"""
import glob
import json
import os
import tarfile

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

MODEL = "/opt/ml/processing/model/model.tar.gz"
TEST = "/opt/ml/processing/test"
OUT = "/opt/ml/processing/evaluation"
TARGET = "tip_amount"
FEATURES = [
    "passenger_count", "trip_distance", "trip_minutes", "fare_amount", "tolls_amount",
    "avg_mph", "fare_per_minute", "pickup_hour", "pickup_dow", "is_weekend", "is_rush_hour",
    "zone_tip_rate", "zone_trips", "zone_rolling_fare_20", "zone_prev_tip", "zone_seconds_since_prev",
]

with tarfile.open(MODEL) as tar:
    tar.extractall("/tmp/model")
model = joblib.load("/tmp/model/model.joblib")

files = sorted(glob.glob(os.path.join(TEST, "*.csv")))
df = pd.concat(pd.read_csv(f, header=None, names=[TARGET] + FEATURES, dtype="float32") for f in files)
X, y = df[FEATURES].replace(-1.0, np.nan), df[TARGET]

pred = np.clip(model.predict(X), 0, None)
rule = X["zone_tip_rate"].fillna(0.2) * X["fare_amount"]          # the one-line rule from Day 16


def scores(p):
    return {"mae": round(float(mean_absolute_error(y, p)), 4),
            "rmse": round(float(np.sqrt(mean_squared_error(y, p))), 4),
            "r2": round(float(r2_score(y, p)), 4)}


report = {"regression": scores(pred), "baseline_rule": scores(rule), "test_rows": int(len(df))}
os.makedirs(OUT, exist_ok=True)
with open(os.path.join(OUT, "evaluation.json"), "w") as f:
    json.dump(report, f, indent=2)
print(json.dumps(report, indent=2))
