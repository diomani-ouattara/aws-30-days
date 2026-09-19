"""Day 4: download the month-long dataset used for the whole 30 days.

NYC TLC yellow taxi trips, Jan-Mar 2024, Parquet (~50 MB each).
Also writes one month out as CSV so Day 5 / Day 9 can compare formats.

Run:  py scripts/get_data.py
Files land in ./data/ (git-ignored). Then push to S3 with:
  aws s3 sync data/ s3://YOUR-BUCKET/raw/ --profile ds
"""
from pathlib import Path
import urllib.request

import pandas as pd

BASE = "https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_{ym}.parquet"
MONTHS = ["2024-01", "2024-02", "2024-03"]
OUT = Path(__file__).resolve().parent.parent / "data"
OUT.mkdir(exist_ok=True)

for ym in MONTHS:
    dest = OUT / f"yellow_tripdata_{ym}.parquet"
    if dest.exists():
        print(f"skip   {dest.name} (already here)")
        continue
    print(f"fetch  {dest.name} ...", end=" ", flush=True)
    urllib.request.urlretrieve(BASE.format(ym=ym), dest)
    print(f"{dest.stat().st_size / 1e6:.0f} MB")

# One month as CSV, for the Parquet-vs-CSV comparison later in the plan.
csv = OUT / "yellow_tripdata_2024-01.csv"
if not csv.exists():
    print("write  yellow_tripdata_2024-01.csv ...", end=" ", flush=True)
    pd.read_parquet(OUT / "yellow_tripdata_2024-01.parquet").to_csv(csv, index=False)
    print(f"{csv.stat().st_size / 1e6:.0f} MB")

print("\nContents of data/:")
for p in sorted(OUT.iterdir()):
    print(f"  {p.name:36s} {p.stat().st_size / 1e6:7.0f} MB")
