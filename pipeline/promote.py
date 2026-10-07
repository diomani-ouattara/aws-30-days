"""Pipeline step 4a - Promote (only runs when the Condition passes).

Copies the model and its evaluation into one output folder; SageMaker uploads that folder to
s3://dave-ds-lab-ca/models/approved/. No S3 code needed - the step's ProcessingOutput does the copy.
"""
import argparse
import json
import os
import shutil
import time

p = argparse.ArgumentParser()
p.add_argument("--execution", default="unknown")
args = p.parse_args()

OUT = "/opt/ml/processing/output"
os.makedirs(OUT, exist_ok=True)
shutil.copy("/opt/ml/processing/model/model.tar.gz", OUT)
shutil.copy("/opt/ml/processing/evaluation/evaluation.json", OUT)
with open("/opt/ml/processing/evaluation/evaluation.json") as f:
    evaluation = json.load(f)
with open(os.path.join(OUT, "approved.json"), "w") as f:
    json.dump({"pipeline_execution": args.execution,
               "approved_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "test_mae": evaluation["regression"]["mae"]}, f, indent=2)
print("promoted:", sorted(os.listdir(OUT)))
