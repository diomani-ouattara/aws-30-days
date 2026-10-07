"""Pipeline step 1 - Prepare: features/v1 (Parquet, from Day 11's SQL) -> three CSV channels.

Fixes the caveat from Days 17-19: March was used both to pick the model and to report its score.
Now March is split in two, so the final number comes from trips nothing ever tuned on:
    train       Jan + Feb           fit the model
    validation  Mar 1-15            early stopping
    test        Mar 16-31           the reported score, read once by the Evaluate step

Same CSV contract as before: target first, 16 features, no header, nulls as -1.
"""
import os

import numpy as np
import pyarrow.dataset as ds

IN = "/opt/ml/processing/input/features"
OUT = "/opt/ml/processing/output"
TARGET = "tip_amount"
FEATURES = [
    "passenger_count", "trip_distance", "trip_minutes", "fare_amount", "tolls_amount",
    "avg_mph", "fare_per_minute", "pickup_hour", "pickup_dow", "is_weekend", "is_rush_hour",
    "zone_tip_rate", "zone_trips", "zone_rolling_fare_20", "zone_prev_tip", "zone_seconds_since_prev",
]

dataset = ds.dataset(IN, format="parquet", partitioning="hive")
df = dataset.to_table(columns=[TARGET] + FEATURES + ["tpep_pickup_datetime", "split"]).to_pandas()
day = df["tpep_pickup_datetime"].dt.day
print(f"read {len(df):,} rows from {IN}")

parts = {
    "train": df["split"] == "train",
    "validation": (df["split"] == "valid") & (day <= 15),
    "test": (df["split"] == "valid") & (day > 15),
}
for name, mask in parts.items():
    out = os.path.join(OUT, name)
    os.makedirs(out, exist_ok=True)
    part = df.loc[mask, [TARGET] + FEATURES].astype("float32").fillna(-1)
    part.to_csv(os.path.join(out, name + ".csv"), header=False, index=False, float_format="%.6g")
    print(f"{name:10s} {len(part):>10,} rows   mean tip ${part[TARGET].mean():.3f}")

assert sum(m.sum() for m in parts.values()) == len(df), "a row landed in no split"
