"""Day 11: sanity-check the feature table that Athena just wrote.

Reads features/v1/ straight from S3 - both splits - and prints the things that
actually go wrong: nulls, impossible ranges, and train/valid drift.

Run:  py notebooks/02_check_features.py
"""
import pandas as pd

BUCKET = "dave-ds-lab-ca"
S3 = {"profile": "ds"}

# Hive-partitioned directory: pandas/pyarrow reads `split` back as a column.
df = pd.read_parquet(f"s3://{BUCKET}/features/v1/", storage_options=S3)
print(f"{len(df):,} rows x {df.shape[1]} cols\n")

print("Rows per split")
print(df["split"].value_counts(), "\n")

print("Nulls (only columns that have any)")
nulls = df.isna().sum()
print(nulls[nulls > 0].to_string() or "  none", "\n")

num = df.select_dtypes("number").columns.drop("tip_amount")
print("Feature ranges")
print(df[num].describe().T[["min", "mean", "max"]].round(2).to_string(), "\n")

print("Target by split  (these should be close - if valid is far off, the split is wrong)")
print(df.groupby("split")["tip_amount"].agg(["count", "mean", "median", "max"]).round(3).to_string(), "\n")

print("Correlation with the target, training rows only")
train = df[df["split"] == "train"]
corr = train[num.append(pd.Index(["tip_amount"]))].corr()["tip_amount"].drop("tip_amount")
print(corr.sort_values(ascending=False).round(3).to_string())
print("\nAnything above ~0.9 is suspicious: it probably contains the answer.")
