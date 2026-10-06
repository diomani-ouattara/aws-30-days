"""Day 23: build the training image, run it locally the way SageMaker would, push it to ECR.

Windows has no `make`, so this script is the Makefile. Each step is a subcommand:

    py scripts/build_push.py sample      # pull ~20k rows per channel from S3 into data/sample/
    py scripts/build_push.py build       # docker build -t ds-lab-train:latest .
    py scripts/build_push.py run         # docker run ... train   (SageMaker's exact file layout)
    py scripts/build_push.py push        # create the ECR repo if needed, log in, tag, push
    py scripts/build_push.py all         # sample + build + run + push

Docker Desktop must be running (whale icon in the system tray) before build / run / push.
"""
import base64
import subprocess
import sys
from pathlib import Path

import boto3

PROFILE, REGION, BUCKET = "ds", "ca-central-1", "dave-ds-lab-ca"
REPO = "ds-lab-train"
TAG = "latest"
ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "data" / "sample"          # git-ignored (data/)
LOCAL_OUT = ROOT / "local_model" / "docker"


def sh(*cmd, stdin=None):
    print("$", " ".join(cmd))
    subprocess.run(cmd, check=True, input=stdin, text=stdin is not None)


def session():
    return boto3.Session(profile_name=PROFILE, region_name=REGION)


def sample(rows=20_000):
    """First `rows` lines of each channel - enough to prove the container works, small enough to be instant."""
    s3 = session().client("s3")
    for ch in ["train", "validation"]:
        key = s3.list_objects_v2(Bucket=BUCKET, Prefix=f"features/xgb/{ch}/")["Contents"][0]["Key"]
        data = s3.get_object(Bucket=BUCKET, Key=key, Range=f"bytes=0-{rows * 130}")["Body"].read()
        data = data[: data.rfind(b"\n") + 1]          # drop the half line at the cut
        out = SAMPLE / ch
        out.mkdir(parents=True, exist_ok=True)
        (out / "part-0.csv").write_bytes(data)
        n = data.count(b"\n")
        print(f"{ch:10s} {n:,} rows -> {out}")


def build():
    sh("docker", "build", "-t", f"{REPO}:{TAG}", str(ROOT))
    sh("docker", "image", "ls", REPO)


def run():
    """Mount the sample where SageMaker mounts channels, and a local folder where it collects the model.
    `train` is the command SageMaker itself sends to a training container."""
    if not (SAMPLE / "train").exists():
        sample()
    LOCAL_OUT.mkdir(parents=True, exist_ok=True)
    sh("docker", "run", "--rm",
       "-v", f"{SAMPLE}:/opt/ml/input/data:ro",
       "-v", f"{LOCAL_OUT}:/opt/ml/model",
       f"{REPO}:{TAG}", "train", "--max-iter", "50")
    print("model files:", sorted(p.name for p in LOCAL_OUT.iterdir()))


def push():
    s = session()
    ecr = s.client("ecr")
    try:
        ecr.create_repository(repositoryName=REPO, imageScanningConfiguration={"scanOnPush": True},
                              tags=[{"Key": "project", "Value": "ds-lab"}])
        # Keep only the 3 newest images: ECR bills storage per GB-month, old images pile up silently.
        ecr.put_lifecycle_policy(repositoryName=REPO, lifecyclePolicyText=(
            '{"rules":[{"rulePriority":1,"description":"keep last 3","selection":'
            '{"tagStatus":"any","countType":"imageCountMoreThan","countNumber":3},"action":{"type":"expire"}}]}'))
        print("created ECR repository", REPO)
    except ecr.exceptions.RepositoryAlreadyExistsException:
        pass

    auth = ecr.get_authorization_token()["authorizationData"][0]
    user, password = base64.b64decode(auth["authorizationToken"]).decode().split(":", 1)
    registry = auth["proxyEndpoint"].replace("https://", "")
    sh("docker", "login", "--username", user, "--password-stdin", registry, stdin=password)

    remote = f"{registry}/{REPO}:{TAG}"
    sh("docker", "tag", f"{REPO}:{TAG}", remote)
    sh("docker", "push", remote)

    img = ecr.describe_images(repositoryName=REPO, imageIds=[{"imageTag": TAG}])["imageDetails"][0]
    print(f"\npushed {remote}")
    print(f"digest {img['imageDigest']}")
    print(f"size   {img['imageSizeInBytes'] / 1e6:.0f} MB compressed in ECR")


def main():
    steps = {"sample": [sample], "build": [build], "run": [run], "push": [push],
             "all": [sample, build, run, push]}
    if len(sys.argv) != 2 or sys.argv[1] not in steps:
        print(__doc__)
        sys.exit(1)
    for step in steps[sys.argv[1]]:
        step()


if __name__ == "__main__":
    main()
