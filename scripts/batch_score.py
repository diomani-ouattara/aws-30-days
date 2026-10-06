"""Day 20: score all of March with the candidate model using Batch Transform. No endpoint.

Two API calls:
  1. CreateModel           - "this artifact + this container + this inference code" (no compute, free)
  2. CreateTransformJob    - spin up a machine, stream every CSV line through the model, write results
                             to S3, shut down. You pay for the minutes it runs and nothing after.

The trick worth knowing is DataProcessing. The input file has the TRUE tip as its first column:
    InputFilter  "$[1:]"    send everything except column 0 to the model (it must not see the answer)
    JoinSource   "Input"    glue the model's output back onto the original input row
    OutputFilter "$[0,-1]"  keep only column 0 (actual tip) and the last column (prediction)
So every output line is "actual,predicted" - ready to score in Athena without any join.

Run:
    py scripts/batch_score.py --dry-run
    py scripts/batch_score.py
"""
import argparse
import io
import json
import tarfile
import time
from pathlib import Path

import boto3

PROFILE, REGION, BUCKET = "ds", "ca-central-1", "dave-ds-lab-ca"
# Must match the scikit-learn that trained the model (1.2.1, from the 1.2-1 training image).
IMAGE = f"341280168497.dkr.ecr.{REGION}.amazonaws.com/sagemaker-scikit-learn:1.2-1-cpu-py3"
INSTANCE = "ml.m5.large"
MODEL_DATA = f"s3://{BUCKET}/models/candidate/model.tar.gz"
INPUT = f"s3://{BUCKET}/features/xgb/validation/"
SRC = Path(__file__).resolve().parent.parent / "src" / "inference.py"


def code_tarball():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(SRC, arcname="inference.py")
    return buf.getvalue()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    sm, s3 = session.client("sagemaker"), session.client("s3")
    role = sm.describe_notebook_instance(NotebookInstanceName="ds-lab-notebook")["RoleArn"]

    candidate = json.loads(s3.get_object(Bucket=BUCKET, Key="models/candidate/candidate.json")["Body"].read())
    stamp = time.strftime("%Y%m%d-%H%M%S")
    model_name = f"tips-candidate-{stamp}"
    job_name = f"tips-batch-{stamp}"
    code_key = f"models/code/{model_name}/sourcedir.tar.gz"
    output = f"s3://{BUCKET}/outputs/predictions/{job_name}/"

    model_req = dict(
        ModelName=model_name,
        ExecutionRoleArn=role,
        PrimaryContainer={
            "Image": IMAGE,
            "ModelDataUrl": MODEL_DATA,
            "Environment": {
                "SAGEMAKER_PROGRAM": "inference.py",
                "SAGEMAKER_SUBMIT_DIRECTORY": f"s3://{BUCKET}/{code_key}",
                "SAGEMAKER_REGION": REGION,
                "SAGEMAKER_CONTAINER_LOG_LEVEL": "20",
            },
        },
        Tags=[{"Key": "project", "Value": "ds-lab"}, {"Key": "source_job", "Value": candidate["job"]}],
    )
    job_req = dict(
        TransformJobName=job_name,
        ModelName=model_name,
        TransformInput={
            "DataSource": {"S3DataSource": {"S3DataType": "S3Prefix", "S3Uri": INPUT}},
            "ContentType": "text/csv",
            "SplitType": "Line",              # one CSV line = one record
        },
        TransformOutput={"S3OutputPath": output, "Accept": "text/csv", "AssembleWith": "Line"},
        TransformResources={"InstanceType": INSTANCE, "InstanceCount": 1},
        BatchStrategy="MultiRecord",          # pack many lines into each request
        MaxPayloadInMB=6,
        DataProcessing={"InputFilter": "$[1:]", "JoinSource": "Input", "OutputFilter": "$[0,-1]"},
        Tags=[{"Key": "project", "Value": "ds-lab"}],
    )

    print("candidate:", candidate["job"], " validation MAE", candidate["valid_mae"])
    if args.dry_run:
        print(json.dumps({"CreateModel": model_req, "CreateTransformJob": job_req}, indent=2))
        print("\n(dry run - nothing created)")
        return

    s3.put_object(Bucket=BUCKET, Key=code_key, Body=code_tarball())
    sm.create_model(**model_req)
    print("model    ->", model_name)
    sm.create_transform_job(**job_req)
    print("job      ->", job_name)
    print(f"console: https://{REGION}.console.aws.amazon.com/sagemaker/home?region={REGION}#/transform-jobs/{job_name}")

    t0, last = time.time(), None
    while True:
        d = sm.describe_transform_job(TransformJobName=job_name)
        if d["TransformJobStatus"] != last:
            last = d["TransformJobStatus"]
            print(f"{time.time() - t0:6.0f}s  {last}")
        if last in ("Completed", "Failed", "Stopped"):
            break
        time.sleep(20)

    if last != "Completed":
        print("FailureReason:", d.get("FailureReason"))
        print("Logs: CloudWatch -> /aws/sagemaker/TransformJobs ->", job_name)
        return

    secs = (d["TransformEndTime"] - d["TransformStartTime"]).total_seconds()
    print(f"\nran {secs:.0f}s on {INSTANCE}  (~${secs / 3600 * 0.134:.3f})")
    print("output  ->", output)
    key = s3.list_objects_v2(Bucket=BUCKET, Prefix=output.replace(f"s3://{BUCKET}/", ""))["Contents"][0]["Key"]
    head = s3.get_object(Bucket=BUCKET, Key=key, Range="bytes=0-200")["Body"].read().decode()
    print("first lines (actual,predicted):")
    print("\n".join(head.splitlines()[:5]))


if __name__ == "__main__":
    main()
