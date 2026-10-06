"""Day 24: the tip model as a Lambda function - a model API that costs $0 when nobody calls it.

Everything above `handler` runs ONCE per container, at cold start: imports, loading the model.
`handler` runs on every request and reuses what's already in memory. Load the model inside
`handler` instead and every request pays the load time - the most common Lambda ML mistake.

Request (POST JSON, through the Function URL):
    {"trips": [{"trip_distance": 3.2, "trip_minutes": 14, "fare_amount": 17.0, ...}, ...]}
or a single trip object without the "trips" wrapper. Missing features are treated as unknown (NaN) -
the model was trained to handle that. Response:
    {"predicted_tip": [3.41, ...], "n": 1, "model": {...}}
"""
import base64
import json
import math
import os
import time
import warnings

_t0 = time.time()
import joblib  # noqa: E402
import numpy as np  # noqa: E402

# Trained on a DataFrame, called with a plain array: sklearn warns about missing column names. Harmless here.
warnings.filterwarnings("ignore", message="X does not have valid feature names")

HERE = os.path.dirname(os.path.abspath(__file__))
FEATURES = [
    "passenger_count", "trip_distance", "trip_minutes", "fare_amount", "tolls_amount",
    "avg_mph", "fare_per_minute", "pickup_hour", "pickup_dow", "is_weekend", "is_rush_hour",
    "zone_tip_rate", "zone_trips", "zone_rolling_fare_20", "zone_prev_tip", "zone_seconds_since_prev",
]

MODEL = joblib.load(os.path.join(HERE, "model.joblib"))
with open(os.path.join(HERE, "metrics.json")) as f:
    _m = json.load(f)
MODEL_INFO = {"sklearn": _m.get("sklearn_version"), "valid_mae": _m.get("valid", {}).get("mae"),
              "trained_at": _m.get("trained_at_utc")}
COLD_START_SECONDS = round(time.time() - _t0, 2)


def _num(v):
    if v is None or v == "":
        return math.nan
    return float(v)


def _response(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body)}


def handler(event, context):
    try:
        if "body" in event:                       # Function URL / API Gateway: payload is a string
            body = event.get("body") or "{}"
            if event.get("isBase64Encoded"):
                body = base64.b64decode(body).decode()
            payload = json.loads(body)
        else:                                     # direct invoke (console Test, aws lambda invoke)
            payload = event
        trips = payload.get("trips", [payload])
        if not isinstance(trips, list) or not trips:
            raise ValueError('expected {"trips": [ {...}, ... ]} or a single trip object')
        if len(trips) > 1000:
            raise ValueError("at most 1000 trips per request")

        X = np.array([[_num(t.get(f)) for f in FEATURES] for t in trips], dtype="float32")
        preds = np.clip(MODEL.predict(X), 0, None)
        unknown = sorted({k for t in trips for k in t} - set(FEATURES))
        return _response(200, {
            "predicted_tip": [round(float(p), 2) for p in preds],
            "n": len(trips),
            "ignored_keys": unknown,
            "model": MODEL_INFO,
            "container_cold_start_s": COLD_START_SECONDS,
        })
    except (ValueError, TypeError, AttributeError, json.JSONDecodeError) as e:
        return _response(400, {"error": str(e), "expected_features": FEATURES})
