"""Day 18: launch src/train.py as a SageMaker training job - from the laptop, no notebook needed.

This is what the SageMaker Python SDK's `SKLearn(entry_point="train.py", ...).fit()` does
under the hood, written out so every moving part is visible:

  1. tar the source folder into sourcedir.tar.gz and upload it to S3
  2. pick AWS's scikit-learn container image
  3. call CreateTrainingJob, telling the container where the code is (sagemaker_submit_directory)
     and which file to run (sagemaker_program)
  4. the container downloads the code, sets SM_CHANNEL_* / SM_MODEL_DIR, and runs
     `python train.py --learning-rate 0.1 ...` with our hyperparameters as flags

Run:
    py scripts/launch_train.py --dry-run               # print the request, launch nothing
    py scripts/launch_train.py                         # launch and wait
    py scripts/launch_train.py --learning-rate 0.05    # any train.py hyperparameter
"""
import argparse
import io
import json
import tarfile
import time
from pathlib import Path

import boto3

PROFILE = "ds"
REGION = "ca-central-1"
BUCKET = "dave-ds-lab-ca"
# scikit-learn 1.2-1 exists for both training and inference - the batch transform on Day 20
# must load the model with the same version that trained it.
IMAGE = f"341280168497.dkr.ecr.{REGION}.amazonaws.com/sagemaker-scikit-learn:1.2-1-cpu-py3"
INSTANCE = "ml.m5.large"
SRC = Path(__file__).resolve().parent.parent / "src"

# SageMaker scrapes these from the job's log and graphs them in the console. train.py prints
# lines like "validation:mae=1.2424;" precisely so these regexes can find them.
METRICS = [
    {"Name": "validation:mae", "Regex": r"validation:mae=([0-9\.]+);"},
    {"Name": "validation:rmse", "Regex": r"validation:rmse=([0-9\.]+);"},
    {"Name": "validation:r2", "Regex": r"validation:r2=(-?[0-9\.]+);"},
    {"Name": "baseline:mae", "Regex": r"baseline:mae=([0-9\.]+);"},
]


def execution_role(session):
    """The SageMaker execution role, found by name - it outlives any notebook instance."""
    iam = session.client("iam")
    for page in iam.get_paginator("list_roles").paginate(PathPrefix="/service-role/"):
        for r in page["Roles"]:
            if r["RoleName"].startswith("AmazonSageMaker-ExecutionRole-"):
                return r["Arn"]
    raise RuntimeError("no AmazonSageMaker-ExecutionRole-* found")


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-wait", action="store_true")
    p.add_argument("--tag", default="", help="short label added to the job name, e.g. lr005")
    p.add_argument("--image", default="", help="Day 23: your own ECR image URI instead of AWS's scikit-learn image")
    # Hyperparameters forwarded to train.py unchanged
    p.add_argument("--learning-rate", type=float, default=0.1)
    p.add_argument("--max-iter", type=int, default=300)
    p.add_argument("--max-leaf-nodes", type=int, default=63)
    p.add_argument("--min-samples-leaf", type=int, default=200)
    p.add_argument("--l2-regularization", type=float, default=1.0)
    p.add_argument("--max-rows", type=int, default=0)
    return p.parse_args()


def source_tarball():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for f in SRC.glob("*.py"):
            tar.add(f, arcname=f.name)
    return buf.getvalue()


def main():
    args = parse_args()
    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    sm, s3 = session.client("sagemaker"), session.client("s3")

    role = execution_role(session)
    job_name = "sk-tips-" + (args.tag + "-" if args.tag else "") + time.strftime("%Y%m%d-%H%M%S")
    code_key = f"models/code/{job_name}/sourcedir.tar.gz"   # the role can read models/*

    hp = {
        "learning-rate": args.learning_rate, "max-iter": args.max_iter,
        "max-leaf-nodes": args.max_leaf_nodes, "min-samples-leaf": args.min_samples_leaf,
        "l2-regularization": args.l2_regularization, "max-rows": args.max_rows,
        # sagemaker_* keys configure the container itself; they are not passed to train.py
        "sagemaker_program": "train.py",
        "sagemaker_submit_directory": f"s3://{BUCKET}/{code_key}",
        "sagemaker_region": REGION,
        "sagemaker_container_log_level": 20,
    }
    if args.image:
        # Own image: nothing inside reads sagemaker_* keys, so don't send them.
        hp = {k: v for k, v in hp.items() if not k.startswith("sagemaker_")}
    request = dict(
        TrainingJobName=job_name,
        RoleArn=role,
        AlgorithmSpecification={
            "TrainingImage": args.image or IMAGE, "TrainingInputMode": "File", "MetricDefinitions": METRICS,
        },
        # Framework containers expect every hyperparameter JSON-encoded (strings get their quotes).
        HyperParameters={k: json.dumps(v) for k, v in hp.items()},
        InputDataConfig=[
            {
                "ChannelName": ch,
                "ContentType": "text/csv",
                "DataSource": {"S3DataSource": {
                    "S3DataType": "S3Prefix",
                    "S3Uri": f"s3://{BUCKET}/features/xgb/{ch}/",
                    "S3DataDistributionType": "FullyReplicated",
                }},
            }
            for ch in ["train", "validation"]
        ],
        OutputDataConfig={"S3OutputPath": f"s3://{BUCKET}/models/script/"},
        ResourceConfig={"InstanceType": INSTANCE, "InstanceCount": 1, "VolumeSizeInGB": 10},
        StoppingCondition={"MaxRuntimeInSeconds": 1800},
        Tags=[{"Key": "project", "Value": "ds-lab"}],
    )

    if args.dry_run:
        print(json.dumps(request, indent=2))
        print("\n(dry run - nothing uploaded, nothing launched)")
        return

    if args.image:
        # Own image: train.py is baked in, and there is no toolkit to download code or read sagemaker_* keys.
        print("image ->", args.image)
    else:
        s3.put_object(Bucket=BUCKET, Key=code_key, Body=source_tarball())
        print("code  ->", f"s3://{BUCKET}/{code_key}")
    sm.create_training_job(**request)
    print("job   ->", job_name)
    print(f"console: https://{REGION}.console.aws.amazon.com/sagemaker/home?region={REGION}#/jobs/{job_name}")
    if args.no_wait:
        return

    last, t0 = None, time.time()
    while True:
        d = sm.describe_training_job(TrainingJobName=job_name)
        if d["SecondaryStatus"] != last:
            last = d["SecondaryStatus"]
            print(f"{time.time() - t0:6.0f}s  {d['TrainingJobStatus']:11s} {last}")
        if d["TrainingJobStatus"] in ("Completed", "Failed", "Stopped"):
            break
        time.sleep(15)

    if d["TrainingJobStatus"] != "Completed":
        print("FailureReason:", d.get("FailureReason"))
        print("Full log: CloudWatch -> /aws/sagemaker/TrainingJobs ->", job_name)
        return
    print("\nartifact :", d["ModelArtifacts"]["S3ModelArtifacts"])
    print("billable :", d.get("BillableTimeInSeconds"), "s")
    for m in d.get("FinalMetricDataList", []):
        print(f"{m['MetricName']:16s} {m['Value']:.4f}")


if __name__ == "__main__":
    main()
