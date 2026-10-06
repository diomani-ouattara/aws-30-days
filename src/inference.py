"""Day 20: how SageMaker's scikit-learn container should load and call the tip model.

The serving container runs a small web server. For every request (from Batch Transform or,
on Day 22, a real-time endpoint) it calls four hooks, in this order:

    model_fn(model_dir)                    once, at startup - load model.joblib
    input_fn(body, content_type)           bytes in  -> DataFrame
    predict_fn(data, model)                DataFrame -> predictions
    output_fn(prediction, accept)          predictions -> bytes out

Input rows are the 16 features in the same order as training, comma-separated, no header,
nulls written as -1 (the Day 17 CSV contract). Output is one prediction per line.

Kept compatible with Python 3.8 (scikit-learn 1.2-1 container).
"""
import io
import os

import joblib
import numpy as np
import pandas as pd

FEATURES = [
    "passenger_count", "trip_distance", "trip_minutes", "fare_amount", "tolls_amount",
    "avg_mph", "fare_per_minute", "pickup_hour", "pickup_dow", "is_weekend", "is_rush_hour",
    "zone_tip_rate", "zone_trips", "zone_rolling_fare_20", "zone_prev_tip", "zone_seconds_since_prev",
]


def model_fn(model_dir):
    return joblib.load(os.path.join(model_dir, "model.joblib"))


def input_fn(body, content_type):
    if content_type != "text/csv":
        raise ValueError("expected text/csv, got " + str(content_type))
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    df = pd.read_csv(io.StringIO(body), header=None, names=FEATURES, dtype="float32")
    # Same -1 -> NaN step as train.py. Skip it and the model sees -1 as a real value.
    return df.replace(-1.0, np.nan)


def predict_fn(data, model):
    # A tip can't be negative; the regressor occasionally dips just below zero.
    return np.clip(model.predict(data), 0, None)


def output_fn(prediction, accept):
    return "\n".join("{:.4f}".format(p) for p in prediction) + "\n"
