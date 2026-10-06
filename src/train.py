"""Day 18: the Day 16 model as a script that runs the same way on a laptop and inside SageMaker.

SageMaker "script mode" contract - the container sets these, and this script reads them:
    SM_CHANNEL_TRAIN        local folder holding the "train" channel's files
    SM_CHANNEL_VALIDATION   local folder holding the "validation" channel's files
    SM_MODEL_DIR            write the model here; SageMaker tars it into model.tar.gz
    SM_OUTPUT_DATA_DIR      anything else worth keeping (goes to output.tar.gz)
Hyperparameters arrive as ordinary command-line flags, so argparse handles both worlds.

Data: the headerless CSV channels from Day 17 (target first, then 16 features, nulls = -1).

Laptop (reads straight from S3 via s3fs, no download; needs AWS_PROFILE=ds):
    py src/train.py --train s3://dave-ds-lab-ca/features/xgb/train/ \
                    --validation s3://dave-ds-lab-ca/features/xgb/validation/ \
                    --model-dir local_model --max-rows 200000 --max-valid-rows 100000

Kept compatible with Python 3.8 - the SageMaker scikit-learn 1.2-1 container's version.
"""
import argparse
import json
import os
import time

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

TARGET = "tip_amount"
FEATURES = [
    "passenger_count", "trip_distance", "trip_minutes", "fare_amount", "tolls_amount",
    "avg_mph", "fare_per_minute", "pickup_hour", "pickup_dow", "is_weekend", "is_rush_hour",
    "zone_tip_rate", "zone_trips", "zone_rolling_fare_20", "zone_prev_tip", "zone_seconds_since_prev",
]
COLUMNS = [TARGET] + FEATURES


def parse_args():
    p = argparse.ArgumentParser()
    # Hyperparameters - SageMaker passes these as --name value
    p.add_argument("--learning-rate", type=float, default=0.1)
    p.add_argument("--max-iter", type=int, default=300)
    p.add_argument("--max-leaf-nodes", type=int, default=63)
    p.add_argument("--min-samples-leaf", type=int, default=200)
    p.add_argument("--l2-regularization", type=float, default=1.0)
    p.add_argument("--max-rows", type=int, default=0, help="0 = use every training row")
    p.add_argument("--max-valid-rows", type=int, default=0, help="0 = every validation row; cap it for laptop runs")
    p.add_argument("--seed", type=int, default=42)
    # Where things live - defaults come from SageMaker's environment variables
    p.add_argument("--train", default=os.environ.get("SM_CHANNEL_TRAIN"))
    p.add_argument("--validation", default=os.environ.get("SM_CHANNEL_VALIDATION"))
    p.add_argument("--model-dir", default=os.environ.get("SM_MODEL_DIR", "local_model"))
    p.add_argument("--output-dir", default=os.environ.get("SM_OUTPUT_DATA_DIR"))
    args = p.parse_args()
    if not args.train or not args.validation:
        p.error("--train and --validation are required outside SageMaker")
    return args


def list_files(path):
    """Every data file under a local folder or an s3:// prefix."""
    if path.startswith("s3://"):
        import s3fs  # laptop only; the container always gets local folders
        fs = s3fs.S3FileSystem()
        return ["s3://" + k for k in sorted(fs.find(path)) if not k.endswith("/")], fs.open
    files = sorted(
        os.path.join(root, f) for root, _, names in os.walk(path) for f in names if not f.startswith(".")
    )
    return files, open


def read_channel(path, max_rows=0):
    files, opener = list_files(path)
    frames, remaining = [], max_rows or None
    for f in files:
        with opener(f, "rb") as fh:
            df = pd.read_csv(fh, header=None, names=COLUMNS, dtype="float32", nrows=remaining)
        frames.append(df)
        if remaining is not None:
            remaining -= len(df)
            if remaining <= 0:
                break
    df = pd.concat(frames, ignore_index=True)
    print("read {:,} rows from {} file(s) under {}".format(len(df), len(frames), path))
    return df


def score(y, pred):
    return {
        "mae": round(float(mean_absolute_error(y, pred)), 4),
        "rmse": round(float(np.sqrt(mean_squared_error(y, pred))), 4),
        "r2": round(float(r2_score(y, pred)), 4),
    }


def main():
    args = parse_args()
    print("sklearn", sklearn.__version__, "| args", vars(args))

    train = read_channel(args.train, args.max_rows)
    valid = read_channel(args.validation, args.max_valid_rows)
    X_train, y_train = train[FEATURES], train[TARGET]
    X_valid, y_valid = valid[FEATURES], valid[TARGET]

    # Nulls were written as -1 for the XGBoost CSV contract; give HGB its NaNs back.
    X_train = X_train.replace(-1.0, np.nan)
    X_valid = X_valid.replace(-1.0, np.nan)

    params = dict(
        learning_rate=args.learning_rate, max_iter=args.max_iter,
        max_leaf_nodes=args.max_leaf_nodes, min_samples_leaf=args.min_samples_leaf,
        l2_regularization=args.l2_regularization,
        early_stopping=True, validation_fraction=0.1, n_iter_no_change=15, random_state=args.seed,
    )
    model = HistGradientBoostingRegressor(**params)

    t0 = time.time()
    model.fit(X_train, y_train)
    seconds = round(time.time() - t0, 1)

    valid_scores = score(y_valid, model.predict(X_valid))
    rule = X_valid["zone_tip_rate"].fillna(float((y_train / X_train["fare_amount"]).mean())) * X_valid["fare_amount"]
    baseline = score(y_valid, rule)

    # One line per metric in a fixed format - Day 19 tells SageMaker a regex to scrape these from the log.
    for k, v in valid_scores.items():
        print("validation:{}={};".format(k, v))
    print("baseline:mae={};".format(baseline["mae"]))
    print("trained in {}s, {} boosting rounds".format(seconds, model.n_iter_))

    os.makedirs(args.model_dir, exist_ok=True)
    joblib.dump(model, os.path.join(args.model_dir, "model.joblib"))

    metrics = {
        "model": "sklearn.HistGradientBoostingRegressor",
        "sklearn_version": sklearn.__version__,
        "trained_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "train_rows": len(X_train), "valid_rows": len(X_valid),
        "features": FEATURES, "target": TARGET, "params": params,
        "boosting_rounds": int(model.n_iter_), "train_seconds": seconds,
        "valid": valid_scores, "baseline_zone_rate_x_fare": baseline,
    }
    # Inside the model folder, so the scorecard travels inside model.tar.gz with the model.
    for folder in filter(None, [args.model_dir, args.output_dir]):
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "metrics.json"), "w") as f:
            json.dump(metrics, f, indent=2)
    print("wrote model.joblib + metrics.json to", args.model_dir)


if __name__ == "__main__":
    main()
